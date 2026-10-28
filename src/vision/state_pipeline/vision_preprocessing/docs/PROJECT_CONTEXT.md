# 프로젝트 컨텍스트 — 공사 현장 사진 → 설치율 → DB → 영어 보고서

> 새 채팅 / 새 Cowork 에서 이 파일을 **가장 먼저** 읽게 하세요. 갱신: 2026-10-21.
> 비전 전처리 세부: `AI_HANDOFF.md` · 새 현장 사용법: `NEW_SITE_GUIDE_ko.md` · 에이전트 연동: `agent/`

## 1. 무엇을 만드나
매장 공사 현장(4개 현장: SITE_A~SITE_D)에 고정된 고정 어안 카메라 사진(약 10분 간격)으로
**벽별 석고보드 설치율**을 매일 계산하고, 그 값을 DB 에 쌓아 **영어 질의응답·주간 보고서**를 만드는 시스템.

```
[1 전처리] SAM3 자동 분할(초안) → qa_v3 1차 자동 보정 → vision_preprocessing 2차(구도·ROI·상태 라벨, 사람 검수) → 학습 데이터
[2 모델]   고정 인코더(ConvNeXt-T / DINOv3) + 디코더 U-Net vs UPerNet (+Linear 기준선), 헤드: 물체 8종 · 상태 4종
[3 추론]   사진 → 상태 지도 → 측정 영역(ROI) → 보이는 영역 중 설치율 → vision_output.json (vision_output/1.0)
[4 백엔드] PostgreSQL + FastAPI (AWS Lightsail): 일일 관측 저장 · 변화 검증 · 공정표 DB (SITE_A 작업 79개)
[5 에이전트] 질문 해석 → API 조회 → Qwen3.5-9B 영어 초안 → 원본 수치·날짜 대조 → 답변 / 주간 보고서
```
그림: `figures/overview_v5_preview.png` (한눈에), `figures/full_pipeline_v4_preview.png` (상세).

## 2. 팀 · 역할
| 사람 | 담당 |
|---|---|
| 비전 담당 (호칭 **유난님**) | 비전 전처리 · 모델 · 추론 · `vision_output` 형식 (이 대화들) |
| 백엔드 담당 | DB · 백엔드 (PostgreSQL, FastAPI, 공정표 DB) |
| 에이전트 담당 | 에이전트 (질의응답, 보고서, LLM) |

**협의가 필요한 연결점**: 공정표 작업 ID ↔ 비전 대상(W01…) 매핑 · 설치율 ≠ 공정률 (일정 평가는 백엔드 가중치로) ·
카메라 위치 ↔ 평면도.

## 3. 핵심 정의 (모든 단계 공통, 바꾸지 말 것)
- **설치율** = 측정 ROI 안에서 **카메라에 보이는 부분 중** 석고보드가 덮인 픽셀 비율. 면적(㎡)·매장 전체·공정률이 아님.
- 보이는 부분이 30 % 미만이면 그날은 **held** (값 없음). **0 으로 채우거나 전날 값을 끌어오지 않음.**
- ROI = 대상 벽 전체(아직 안 붙은 곳·가려진 곳 포함). 문·창 같은 영구 개구부만 제외영역, 임시 통로는 포함.
- 자동 결과는 초안. 사람 이름의 승인만 정답. 모델 입력은 RGB 만.
- 데이터는 **기밀일 수 있음**: 로컬 처리, 외부 서비스로 사진·라벨 자동 업로드 금지. ZIP 암호는 저장하지 않음.

## 4. 논문 방향 (사용자 확정)
**비교 연구가 중심**, VLM 은 "되면" 라벨링 보조로만 (VLM 보다 좋다고 주장하지 않음).
1. 전처리: DINO + SAM2.1 vs SAM3 (정성 비교 수행함)
2. 디코더: U-Net vs UPerNet (+ Linear 기준선), 같은 고정 인코더
3. 적은 라벨 학습: 사람 라벨만 vs 안정 구간 라벨 전파 포함
4. 지표: state mIoU, ROI 설치율 MAE, 교차 현장(SITE_A→SITE_C) 오차
한 쪽 요약: `paper/논문주제.docx`.

## 5. 현재 상태 (2026-10-21)
- SITE_A: 라벨 완료, 학습 데이터·전파 데이터(v2) 준비. 사람 라벨 모델 test: state mIoU 0.952, ROI 설치율 MAE 0.0043.
- SITE_C: 구도·ROI 확정, 앵커 라벨링 진행 중. SITE_B · SITE_D: 미착수.
- 추론 코드와 `vision_output/1.0` 형식 확정(초안 v1.0). 실제 모델 출력 예시는 아직 없음 → 에이전트는 SITE_A 라벨 기반 예시
  (`agent/vision_output_site_a_example.json`, `source: "label"`)로 개발. 모델이 붙으면 같은 형식에 `source: "model"`.
- 백엔드: DB·API 동작(백엔드 담당). 에이전트: 계획 단계(에이전트 담당, 10/16 계획서 검토 완료).

## 6. 작업 방식 (사용자 규칙)
- 한국어, 짧게, 단계별. **의견 먼저 → "해줘" 하면 실행.** 막히면 오래 붙잡지 말고 바로 보고.
- 사용자 터미널은 PowerShell. 명령은 복사해서 한 줄씩 쓸 수 있게.
- Git 은 사용자가 직접 실행(복붙 명령만 제공). GitHub 업로드 전 민감 정보 보고·확인.
- 사용자 대신 데이터(store)를 고칠 땐 백업 먼저, 기록에 "사용자 요청으로 대신 기록" 명시.
- 원본 ZIP·JSON·자동 결과는 절대 수정/덮어쓰기 금지.

## 7. 폴더
```
D:\work\project\
  Data\                              원본 사진 ZIP(SITE_C·SITE_D 암호), processed_json ZIP  (기밀)
  전처리된 데이터\
    vision_preprocessing\            2차 전처리·검수 서버·학습·추론 코드 (docs\ 에 이 문서들)
    state_preprocessing_results\     run 폴더 + review_store\<현장>.sqlite
    Claude outputs\                  그림·문서·이 컨텍스트 묶음
  Capstone-Design-Project-1\         GitHub (src\vision\state_pipeline = 익명화 사본)
```

## 8. 다음 할 일 (우선순위)
1. SITE_C 앵커 → 초안 검수 → export
2. SITE_A 비교 실험 (`cmp_propagated_v2`, 디코더 3종) → 논문 표
3. 교차 현장 평가 → `vision_output.model.validation` 채우기
4. 백엔드·에이전트와 공정표 매핑 합의, 에이전트 평가 (→ `agent/AGENT_EVAL_BRIEF.md`)
5. SITE_B · SITE_D

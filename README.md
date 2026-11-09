# Development of an AI-Powered Construction Management Support System for Global Retail Stores

**종합설계프로젝트 1 · Team Aloha (알로)**

경북대학교 SW중심대학 학부생중심 산학협력프로젝트 · 2026학년도 2학기

## 프로젝트 소개

글로벌 리테일 매장의 현장 사진으로 시공 상태를 분석하고, 서버에 저장한 분석 결과와 공정표를 바탕으로 Agent가 근거 있는 보고서·질의응답을 제공하는 시스템을 개발합니다. 벽·천장 상태와 관측 가능한 ROI의 설치 비율을 분석하며, 전체 공정률 환산은 별도 기준이 필요합니다.

기존 과제는 알로요가 매장의 고객 동선·존 분석이었습니다. A안(현장 사진 기반 공사 진행 모니터링)과 B안(개략 공사비·공급업체 견적)을 검토한 뒤, 2026년 9월 19일 팀 내부 회의에서 A안을 우선 선택했습니다. 당시에는 필요한 자료를 알로요가 측에서 제공하기 어려우면 B안으로 전환하는 조건도 검토했습니다. 현재는 A안으로 확정되어 진행 중입니다. [9월 19일 팀 회의록](https://github.com/Slen5124/Capstone-Design-Project-1/blob/main/docs/meetings/2026-09-19-weekly.md)과 [과제 변경 경위·기획 자료 요약](docs/planning/project-background.md)에 배경을 정리했습니다.

현재 기본 모델은 **DINOv2 + UPerNet**입니다. 세부 흐름은 [현재 비전 파이프라인](docs/planning/current-vision-pipeline.md)에 정리했습니다.

벽·천장 설치 상태와 가림을 구분합니다. 관측 설치율을 실제 공정률로 환산하는 기준은 공정표 작업과 함께 별도로 합의해야 합니다.

| 항목 | 내용 |
| --- | --- |
| 참여기업 | Alo Yoga |
| 기업 멘토 | Andy Choe |
| 팀명 | Team Aloha (알로) |
| 공식 과제명 | Development of an AI-Powered Construction Management Support System for Global Retail Stores |
| 교내 지도교수 | 정설영 |
| 산학 지도교수 | 김일경 |
| 팀 | 경북대학교 2조 |
| 수행 기간 | 2026학년도 2학기 |

## 시스템 설계와 구현 계획

**기준: 2026년 11월 2일 · 현장 4곳 라벨 완료, 현장 단위 교차검증(LOSO) 40회 완료, 기본 모델 DINOv2 + UPerNet 확정**

사람이 검수한 라벨로 **"관측 가능한 영역 안에서 얼마나 설치됐는지"** 를 학습합니다. 코드는 전부 `src/vision/state_pipeline/`에 있습니다.

```
현장 사진 ZIP ─▶ 자동 분할 초안 (사진마다 LabelMe 형식 폴리곤 JSON)
             ─▶ [1차] qa_v3        폴리곤 정리 · 중복 제거 · 대표 기준으로 벽/천장 보정
             ─▶ [2차] vp auto      구도(view) 묶기 · ROI 초안 · 카메라 위치/흔들림 검사 · 표본 · 상태 라벨 초안
             ─▶ [검수] vp review   브라우저 검수 화면(할 일 목록 + 편집 화면): 구도 · 대상 · ROI · 설치 상태 라벨 승인
             ─▶ [export]           이미지 + semantic / target / visibility / state 마스크 + train/val/test 분할
             ─▶ [학습] train       동결 DINOv2 ViT-B/14 + UPerNet,
                                   헤드 2개(물체 8클래스 / 설치 상태 6클래스: 벽·천장 미설치·설치, 가림, 대상 아님)
             ─▶ [추론]             사진 → 상태 맵 → ROI별 설치율 + 확신도 → 일별 값
                                   → 확신도 높은 항목은 자동 보고, 낮은 항목은 사람 검수 → vision_output.json ─▶ 백엔드 · 에이전트
```

| 항목 | 상태 |
| --- | --- |
| 전처리 · 검수 · export · 학습 · 추론 코드 | 구현 완료, 단위 테스트 통과 (vision_preprocessing 104 · qa_v3 83 · qa_v2 42) |
| 데이터 (현장 4곳, 익명 SITE_A~D) | 사람 검수 라벨 A 397장(+ 전파 라벨 490장) · B 74장 · C 88장 · D 30장. 벽 2~6면, B·C·D는 천장 포함 |
| 실험 설계 | 현장 하나씩을 시험용으로 비우는 현장 단위 교차검증: 4 fold × 시드 3개 × 디코더 3종 + Linear 하한선 = 40회. 분할 · 지표 · 통계 방법은 실행 전에 고정 |
| 결과 (11/1) | 새 현장 벽 설치율 오차(MAE) 평균 **UPerNet 5.44%p** · Linear 5.86 · DPT 6.04 · MLP 6.52. UPerNet이 MLP·DPT보다 유의하게 낮음(Linear와는 차이 없음). ±10%p 이내 80%, 완료 판정 F1 0.92 |
| 서비스 추론 결정 | 추론 기법(TTA·작은 ROI 확대)과 시간축 보정(VCC)은 평균 오차를 줄이지 못해 기본값에서 끔. 대신 확신도 상위 50%만 자동 보고하면 오차 4.32%p → 나머지는 사람 검수 |
| 진행 중 | 논문 제출(UCWIT 2026, 11/5) · 큰 인코더(DINOv2 ViT-L) 실험(SITE_A 4.53 → 3.28%p, 나머지 현장 진행 중) · 백엔드 · 에이전트 연동 |
| 다음 계획 | 추가 현장 · 철거 구간 라벨 보강, 천장 · 바닥 대상 추가 학습, 라벨링 보조 도구를 Apache-2.0 구성으로 교체 후 재학습 |

### 주요 결과 (현장 단위 교차검증, 2026-11-01)

학습에 쓰지 않은 현장의 날짜×벽 설치율 MAE(%p, 낮을수록 좋음). 동결 DINOv2 ViT-B/14 위에서 디코더만 바꿨고, 시드 3개 평균 ± 표준편차(Linear는 시드 1개)입니다. 굵은 글씨는 열별 최저 오차(또는 최고값)입니다.

| 디코더 | 파라미터 | SITE_A | SITE_B | SITE_C | 평균 A·B·C | SITE_D (철거·재시공) | 벽 mIoU | ±10%p 이내 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **UPerNet** | 4.10M | **4.45 ± 0.17** | **4.10 ± 0.49** | 7.76 ± 0.35 | **5.44 ± 0.32** | **9.33** | **63.6%** | 80.4% |
| Linear (하한선) | 0.05M | 5.45 | 5.12 | 7.02 | 5.86 | 15.08 | 58.1% | **80.9%** |
| DPT | 4.50M | 6.23 ± 0.50 | 4.55 ± 0.34 | 7.33 ± 0.42 | 6.04 ± 0.29 | 15.29 | 61.7% | 78.8% |
| MLP | 1.05M | 7.68 ± 0.55 | 5.17 ± 0.12 | **6.70 ± 0.49** | 6.52 ± 0.28 | 10.55 | 57.8% | 75.8% |

- UPerNet 대비 평균 차이(짝지은 부트스트랩 B=10,000, 95% 신뢰구간): MLP +1.08 [0.64, 1.57], DPT +0.60 [0.19, 1.01]로 유의, Linear +0.12 [−0.46, 0.72](공통 시드 0)로 차이 없음.
- 추론 기법과 시간축 보정(UPerNet 평균): 기본 5.44 → 반전·다중 배율 5.45 → 작은 ROI 확대 5.67 → 단순 인과 보정 6.38 → VCC 5.76. 확신도 상위 50%만 자동 보고하면 4.32.
- SITE_D는 철거로 설치율이 실제로 줄어드는 특수 현장이라 평균에서 빼고 따로 보고합니다. 예외 목록(사람도 판정하기 어려운 행)을 빼도 순위는 같습니다(UPerNet 5.17 · Linear 5.55 · DPT 5.77 · MLP 6.23).

결과 요약과 다음 계획은 [현재 비전 파이프라인](docs/planning/current-vision-pipeline.md)에 정리했습니다. 사진 · 라벨 · 가중치 · 실험 결과 원본은 공개하지 않습니다.

실험 규격은 [`PROTOCOL.md`](src/vision/state_pipeline/vision_preprocessing/train/loso/PROTOCOL.md), 실행 방법(데이터 준비 · 명령어 · 결과 저장)은
[`DATA_SETUP.md`](src/vision/state_pipeline/vision_preprocessing/train/loso/DATA_SETUP.md)에 있습니다.

사용 방법은 [`src/vision/state_pipeline/README.md`](src/vision/state_pipeline/README.md), 새 현장 추가는
[`NEW_SITE_GUIDE_ko.md`](src/vision/state_pipeline/vision_preprocessing/docs/NEW_SITE_GUIDE_ko.md), 설계 결정과 데이터 계약은
[`AI_HANDOFF.md`](src/vision/state_pipeline/vision_preprocessing/docs/AI_HANDOFF.md)를 참고합니다.

기획 문서:

- [현재 비전 파이프라인](docs/planning/current-vision-pipeline.md)
- [과제 변경 경위·기획 자료 요약](docs/planning/project-background.md)
- [시스템 구조도·팀별 일정 분석](docs/planning/02-03-analysis.md)
- [Git 산출물·이슈 초안·PR 순서](docs/planning/git-deliverables-plan.md)

## ERD와 업무별 데이터 흐름

[운영 DB 구조와 사용법](docs/architecture/erd-workflow/README.md)에 005 적용 후 **20개 저장 테이블·186개 필드와 12개 호환·조회 뷰**, 쉬운 흐름도와 상세 ERD를 정리했습니다. 신규 버전 사용설명서와 설치·전환 SQL, 기능별 추가 API 연결 항목을 함께 제공합니다.

[업무 API 코드와 연결 안내](docs/api/README.md)에는 인증·현장 제출·검토·변경 요청·보고서 API와 OpenAPI 명세가 있습니다. 로컬 구현이며 PostgreSQL 통합 검증과 운영 배포는 별도 작업입니다.

## 협업 공간

**[Notion 팀 페이지 바로가기](https://app.notion.com/p/1-8c15b79a9db5824eaf7a0103c8552003)**

주요 일정 관리, 업무 배분, 회의 안건 정리와 회의록 작성은 Notion에서 진행합니다. GitHub에서는 코드와 프로젝트 문서를 관리합니다.

회의록은 공개 가능 여부를 검토하고 고객사 내부 정보와 개인·접속 정보를 제외한 요약만 `docs/meetings/`에 반영합니다. 원본 회의록은 접근이 제한된 협업 공간에서 관리합니다.

| 공간 | 용도 |
| --- | --- |
| [Notion 팀 페이지](https://app.notion.com/p/1-8c15b79a9db5824eaf7a0103c8552003) | 일정·업무 배분, 회의록(결정 사항·후속 업무), 다음 회의 안건 게시판 |

## 저장소 구성

| 경로 | 내용 |
| --- | --- |
| [`src/vision/state_pipeline/`](src/vision/state_pipeline/) | **비전 파이프라인.** 1차 전처리(`qa_v3`, `qa_v2`), 2차 전처리 · 검수 앱 · export · 학습 · 추론(`vision_preprocessing`) |
| [`docs/planning/`](docs/planning/) | 현재 시공 분석 과제의 구조·일정 분석 및 구현 기획 |
| [`docs/meetings/`](docs/meetings/) | 공개 가능 여부를 검토한 회의 요약 |
| [`docs/proposal/`](docs/proposal/) | 최초 배정 과제(고객 동선·Zone 분석) 제안서 요약. 방향 전환 이전의 배경 자료 |
| [`docs/references/`](docs/references/) | 논문·기술문서 레퍼런스 및 리뷰 |
| [`docs/public/`](docs/public/) | 백엔드 공개 문서와 SQL 안내 |
| [`prototype/public-demo/`](prototype/public-demo/) | 가상 자료 기반 웹 시연 (실제 AI·서버 연동 전) |
| `data/` | 로컬 데이터 작업 공간 (Git 추적 제외) |

프로젝트 방향 전환의 배경은 [9월 17일 킥오프 회의록](docs/meetings/2026-09-17-client-kickoff.md)에 정리되어 있습니다.

협업 및 커밋 규칙은 [기여 가이드](CONTRIBUTING.md)를 참고합니다. 기업 내부 자료(현장 사진, 분할 결과, 검수 저장소, 학습 가중치)는 공개 저장소에 올리지 않으며,
저장소 안의 문서는 현장 이름을 `SITE_A` 처럼 익명화해 적습니다. 원본 데이터는 Git 추적에서 제외된 로컬 작업 폴더에서만 다룹니다.

## 팀원

| 이름 | 역할 |
| --- | --- |
| 이윤한 | 비전 팀 리드: 모델 학습·추론·학습 테스트 |
| 강태웅 | 비전 데이터 전처리 |
| 강원준 | Agent·RAG |
| 김병조 | DB·백엔드 |

세부 기술 선택과 업무 일정은 [파이프라인](docs/planning/current-vision-pipeline.md)과 [팀별 일정 분석](docs/planning/02-03-analysis.md)을 참고합니다.

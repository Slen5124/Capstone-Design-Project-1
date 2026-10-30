# Development of an AI-Powered Construction Management Support System for Global Retail Stores

**종합설계프로젝트 1 · Team Aloha (알로)**

경북대학교 SW중심대학 학부생중심 산학협력프로젝트 · 2026학년도 2학기

## 프로젝트 소개

글로벌 리테일 매장의 현장 사진으로 시공 상태를 분석하고, 서버에 저장한 분석 결과와 공정표를 바탕으로 Agent가 근거 있는 보고서·질의응답을 제공하는 시스템을 개발합니다. 분석 대상, 상태 클래스와 측정 기준은 미정입니다.

기존 과제는 알로요가 매장의 고객 동선·존 분석이었습니다. A안(현장 사진 기반 공사 진행 모니터링)과 B안(개략 공사비·공급업체 견적)을 검토한 뒤, 2026년 9월 19일 팀 내부 회의에서 A안을 우선 선택했습니다. 당시에는 필요한 자료를 알로요가 측에서 제공하기 어려우면 B안으로 전환하는 조건도 검토했습니다. 현재는 A안으로 확정되어 진행 중입니다. [9월 19일 팀 회의록](https://github.com/Slen5124/Capstone-Design-Project-1/blob/main/docs/meetings/2026-09-19-weekly.md)과 [과제 변경 경위·기획 자료 요약](docs/planning/project-background.md)에 배경을 정리했습니다.

세 분할 모델을 학습해 성능을 비교하고, 비교 결과가 좋은 모델을 선택하는 과정까지 프로젝트 범위에 포함합니다. 논문 주제에 모델 비교를 포함할지는 아직 정하지 않았습니다. 세부 흐름은 [현재 비전 파이프라인](docs/planning/current-vision-pipeline.md)에 정리했습니다.

상태별로 클래스를 분리해 인식할 계획입니다. 구체적인 대상 공종, 클래스 이름, 비율 계산 방식은 미정이며 추후 정합니다. 관측값을 실제 공정률로 환산하는 기준도 별도로 합의해야 합니다.

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

**기준: 2026년 10월 28일 · 현장 4곳 라벨 완료, 현장 단위 교차검증(LOSO) 본 실험 진행 중**

초기 계획(5클래스 DeepLabv3+ 분할, Grounding DINO → SAM 2.1 마스크 초안)은 10월 초 실행 확인까지만 하고 접었습니다.
지금은 **"관측 가능한 영역 안에서 얼마나 설치됐는지"** 를 사람이 검수한 라벨로 학습하는 아래 파이프라인으로 바뀌었고, 코드는 전부 `src/vision/state_pipeline/`에 있습니다.

```
현장 사진 ZIP ─▶ SAM3 자동 분할 (사진마다 LabelMe JSON)
             ─▶ [1차] qa_v3        폴리곤 정리 · 중복 제거 · 대표 기준으로 벽/천장 보정
             ─▶ [2차] vp auto      구도(view) 묶기 · ROI 초안 · 카메라 위치/흔들림 검사 · 표본 · 상태 라벨 초안
             ─▶ [검수] vp review   브라우저 검수 화면(할 일 목록 + 편집 화면): 구도 · 대상 · ROI · 설치 상태 라벨 승인
             ─▶ [export]           이미지 + semantic / target / visibility / state 마스크 + train/val/test 분할
             ─▶ [학습] train/loso  동결 DINOv2 ViT-B/14 + MLP / UPerNet / DPT 디코더(Linear 하한선),
                                   헤드 2개(물체 8클래스 / 설치 상태 6클래스: 벽·천장 미설치·설치, 가림, 대상 아님)
             ─▶ [추론]             사진 → 상태 맵 → ROI별 설치율 → TTA · 작은 ROI 확대 · 가시성 인지 인과 보정(VCC)
                                   → 일별 진행률 · 철거 의심 표시 → vision_output.json  ─▶ 에이전트(보고서)
```

| 항목 | 상태 |
| --- | --- |
| 전처리 · 검수 · export · 학습 · 추론 코드 | 구현 완료, 단위 테스트 통과 (vision_preprocessing 98 · qa_v3 83 · qa_v2 42) |
| 데이터 (현장 4곳, 익명 SITE_A~D) | 사람 검수 라벨 A 397장(+ 전파 라벨 490장) · B 74장 · C 88장 · D 30장. 벽 2~6면, B·C·D는 천장 포함 |
| 실험 설계 | 현장 하나씩을 시험용으로 비우는 현장 단위 교차검증: 4 fold × 시드 3개 × 디코더 3종 + Linear 하한선 = 40회. 분할 · 지표 · 통계 방법은 실행 전에 고정 |
| 진행 상황 (10/28) | 재현성 비교 1회 완료(UPerNet, SITE_B 시험, 시드 0): 설치 상태 mIoU 0.771, 벽 설치율 MAE 0.045, VCC 적용 시 0.038. 실행 1회 약 2.7시간. 본 실험 진행 중 |
| 보고서 에이전트 | `vision_output.json` 을 입력으로 하는 Agentic AI 파트와 연동 중 |

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

## 협업 공간

**[Notion 팀 페이지 바로가기](https://app.notion.com/p/1-8c15b79a9db5824eaf7a0103c8552003)**

주요 일정 관리, 업무 배분, 회의 안건 정리와 회의록 작성은 Notion에서 진행합니다. GitHub에서는 코드와 프로젝트 문서를 관리합니다.

회의록은 Notion에서 작성·수정한 내용을 `docs/meetings/`에 Markdown으로 동일하게 반영합니다. 변경 시 양쪽 내용을 함께 맞추고, GitHub 커밋으로 변경 이력을 관리합니다.

| 공간 | 용도 |
| --- | --- |
| [Notion 팀 페이지](https://app.notion.com/p/1-8c15b79a9db5824eaf7a0103c8552003) | 일정·업무 배분, 회의록(결정 사항·후속 업무), 다음 회의 안건 게시판 |

## 저장소 구성

| 경로 | 내용 |
| --- | --- |
| [`src/vision/state_pipeline/`](src/vision/state_pipeline/) | **현재 파이프라인.** 1차 전처리(`qa_v3`, `qa_v2`), 2차 전처리 · 검수 앱 · export · 학습 · 추론(`vision_preprocessing`) |
| [`src/vision/preprocessing/`](src/vision/preprocessing/) | (보관) 초기 Grounding DINO → SAM 2.1 마스크 초안 코드. 현재는 SAM3 결과를 직접 사용하므로 쓰지 않음 |
| [`src/vision/README.md`](src/vision/README.md), `train_smoke.py`, `dataset.py` | (보관) 초기 5클래스 DeepLabv3+ 실행 확인. 현재 학습 코드는 `state_pipeline/vision_preprocessing/train/` |
| [`docs/experiments/`](docs/experiments/) | 실험 기록 (10-05 DeepLabv3+ smoke는 초기 계획의 기록) |
| [`docs/planning/`](docs/planning/) | 현재 시공 분석 과제의 구조·일정 분석 및 구현 기획 |
| [`docs/meetings/`](docs/meetings/) | Notion과 동일하게 관리하는 Markdown 회의록 |
| [`docs/proposal/`](docs/proposal/) | 최초 배정 과제(고객 동선·Zone 분석) 제안서 요약. 방향 전환 이전의 배경 자료 |
| [`docs/references/`](docs/references/) | 논문·기술문서 레퍼런스 및 리뷰 |
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

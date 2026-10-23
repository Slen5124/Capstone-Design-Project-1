# 종합설계프로젝트 1

**Alo Yoga 현장 이미지 기반 공사 진행률 모니터링 및 보고 자동화**

경북대학교 SW중심대학 학부생중심 산학협력프로젝트 · 2026학년도 2학기

## 프로젝트 소개

Alo Yoga의 글로벌 매장 공사 현황을 원격으로 파악하기 위해, 현장 사진에서 벽·바닥·천장 등의 시공 상태와 진행률을 분석하고 로컬 LLM을 활용해 공정 보고서를 작성하는 시스템을 개발합니다.

[9월 19일 팀 회의](docs/meetings/2026-09-19-weekly.md)에서 **A안(이미지 기반 공정 모니터링)을 우선 추진**하기로 했습니다. 실제 제공 데이터에 따라 B안(개략 공사비·견적 지원)으로 전환할 가능성을 열어둡니다.

기본 흐름은 **현장 사진 → 시공 상태 인식·진행률 산정 → 공정 보고서 작성**입니다. 지연 의심 판단과 현장 사유 확인은 확장 기능으로 검토합니다.

| 항목 | 내용 |
| --- | --- |
| 참여기업 | Alo Yoga · 알로요가 |
| 기업 멘토 | Andy Choe |
| 교내 지도교수 | 정설영 |
| 산학 지도교수 | 김일경 |
| 팀 | 경북대학교 2조 |
| 수행 기간 | 2026학년도 2학기 |

## 현재 진행 상황

**기준: 2026년 10월 23일 · 비전 파이프라인 구현 완료, 학습 데이터 확장 중**

초기 계획(5클래스 DeepLabv3+ 분할, Grounding DINO → SAM 2.1 마스크 초안)은 10월 초 실행 확인까지만 하고 접었습니다.
지금은 **"관측 가능한 영역 안에서 얼마나 설치됐는지"** 를 사람이 검수한 라벨로 학습하는 아래 파이프라인으로 바뀌었고, 코드는 전부 `src/vision/state_pipeline/`에 있습니다.

```
현장 사진 ZIP ─▶ SAM3 자동 분할 (사진마다 LabelMe JSON)
             ─▶ [1차] qa_v3        폴리곤 정리 · 중복 제거 · 대표 기준으로 벽/천장 보정
             ─▶ [2차] vp auto      구도(view) 묶기 · ROI 초안 · 카메라 위치/흔들림 검사 · 표본 · 상태 라벨 초안
             ─▶ [검수] vp review   브라우저 검수 화면(할 일 목록 + 편집 화면): 구도 · 대상 · ROI · 설치 상태 라벨 승인
             ─▶ [export]           이미지 + semantic / target / visibility / state 마스크 + train/val/test 분할
             ─▶ [학습] train_quick  동결 인코더(ConvNeXt, timm) + U-Net/UPerNet/Linear 디코더, 헤드 2개(물체 8클래스 / 설치 상태 4클래스)
             ─▶ [추론] infer_quick  사진 → 상태 맵 → ROI별 설치율 → 일별 진행률 · 이벤트 → vision_output.json  ─▶ 에이전트(보고서)
```

| 항목 | 상태 |
| --- | --- |
| 전처리 · 검수 · export · 학습 · 추론 코드 | 구현 완료, 단위 테스트 통과 (vision_preprocessing 78 · qa_v3 83 · qa_v2 42) |
| 현장 A (고정 카메라) | 라벨링 완료: 사진 399장(train 256 / val 58 / test 85) + 전파 라벨 490장. 벽 2면 |
| 현장 C (카메라를 자주 옮긴 현장) | 구도 51개 · ROI 전부 승인 · 앵커 라벨 승인. 1차 export 88장, 자동 초안 189장 검수 중. 벽 4면 + 천장 |
| 현장 B · D | 설정만 준비 |
| 모델 (현장 A test, 사람 라벨만) | 설치 상태 mIoU 0.952, ROI 설치율 MAE 0.0043, 사진 단위 MAE 0.027 |
| 보고서 에이전트 | `vision_output.json` 을 입력으로 하는 Agentic AI 파트와 연동 중 |

사용 방법은 [`src/vision/state_pipeline/README.md`](src/vision/state_pipeline/README.md), 새 현장 추가는
[`NEW_SITE_GUIDE_ko.md`](src/vision/state_pipeline/vision_preprocessing/docs/NEW_SITE_GUIDE_ko.md), 설계 결정과 데이터 계약은
[`AI_HANDOFF.md`](src/vision/state_pipeline/vision_preprocessing/docs/AI_HANDOFF.md)를 참고합니다.

## 협업 공간

**[Notion 대시보드 바로가기](https://app.notion.com/p/3db226595aca8059af0df02fe2d5a3eb)**

주요 일정 관리, 업무 배분, 회의 안건 정리와 회의록 작성은 Notion에서 진행합니다. GitHub에서는 코드와 프로젝트 문서를 관리합니다.

회의록은 Notion에서 작성·수정한 내용을 `docs/meetings/`에 Markdown으로 동일하게 반영합니다. 변경 시 양쪽 내용을 함께 맞추고, GitHub 커밋으로 변경 이력을 관리합니다.

| 공간 | 용도 |
| --- | --- |
| [Notion 대시보드](https://app.notion.com/p/3db226595aca8059af0df02fe2d5a3eb) | 일정·안건·업무 확인 |
| [Notion 회의록](https://app.notion.com/p/3d9226595aca8079805ceed26a5f605f) | 회의 내용·결정 사항·후속 업무 작성 |
| [안건 자유게시판](https://app.notion.com/p/3db226595aca8076a52dc6aad11c8002) | 다음 회의에서 논의할 질문과 아이디어 등록 |

## 저장소 구성

| 경로 | 내용 |
| --- | --- |
| [`src/vision/state_pipeline/`](src/vision/state_pipeline/) | **현재 파이프라인.** 1차 전처리(`qa_v3`, `qa_v2`), 2차 전처리 · 검수 앱 · export · 학습 · 추론(`vision_preprocessing`) |
| [`src/vision/preprocessing/`](src/vision/preprocessing/) | (보관) 초기 Grounding DINO → SAM 2.1 마스크 초안 코드. 현재는 SAM3 결과를 직접 사용하므로 쓰지 않음 |
| [`src/vision/README.md`](src/vision/README.md), `train_smoke.py`, `dataset.py` | (보관) 초기 5클래스 DeepLabv3+ 실행 확인. 현재 학습 코드는 `state_pipeline/vision_preprocessing/train/` |
| [`docs/experiments/`](docs/experiments/) | 실험 기록 (10-05 DeepLabv3+ smoke는 초기 계획의 기록) |
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
| 강원준 | Agentic AI·문서 작성 |
| 강태웅 | 전처리·Vision AI |
| 김병조 | Agentic AI·문서 작성 |
| 이윤한 | 전처리·Vision AI |

9월 19일 팀 회의에서 정한 역할을 기준으로 하며, 세부 작업은 진행 상황에 따라 분담합니다.

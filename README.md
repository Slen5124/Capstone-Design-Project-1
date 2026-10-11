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

**기준: 2026년 10월 11일 · 라벨 제작에 SAM3 채택**

라벨 제작 방식으로 **Grounding DINO + SAM 2.1과 SAM3를 비교한 뒤 SAM3를 선택**했습니다. SAM3가 만든 마스크·라벨 초안을 사람이 검수·수정하고 상태별 정답을 확정해 **GT(Ground Truth, 학습에 사용할 정답 데이터)**로 사용합니다. 비교의 세부 평가 조건과 수치는 확인된 실험 기록에 따라 추가합니다.

학습 흐름은 **원본 사진 → SAM3 라벨 초안 → 사람 검수·상태 GT 확정 → 사진 + GT로 ResNet-50 기반 DeepLabv3+·U-Net·Mask R-CNN 학습·성능 비교 → 운영 모델 선택**입니다.

운영 흐름은 **실제 현장 사진 → 학습을 마친 선택 모델로 대상·상태 분석 → 자체 후처리로 결과 JSON·근거 생성 → 서버에서 검증·저장·조회 → Agent가 저장된 결과와 공정표·문서 근거로 보고서 작성·질의응답**입니다.

전후 사진 비교가 필요한 경우에는 LoMa-L 또는 RoMa v2로 대응 위치를 찾고, 별도 기하 정합·검증을 통과한 공통 영역에서 상태 변화를 계산하는 흐름을 추가합니다. 이 결과도 자체 코드가 JSON으로 정리해 서버로 전달합니다.

| 영역 | 설계 내용 |
| --- | --- |
| 전처리·GT 제작 | 비교 후 채택한 SAM3로 마스크 초안 생성 → 사람 검수·상태 GT 확정 |
| 학습·평가 | 원본 사진 + 검수한 GT로 ResNet-50 기반 DeepLabv3+·U-Net·Mask R-CNN을 학습·비교하고 운영 모델 선택 |
| 상태 인식 | DeepLabv3+·U-Net은 픽셀별 클래스 결과, Mask R-CNN은 객체별 박스·클래스·마스크를 출력 |
| 사진 매칭 | LoMa-L 또는 RoMa v2로 대응 위치를 찾고, 별도 정합 검증 후 공통 영역 비교 |
| JSON 생성 | 모델·매칭 결과를 자체 코드로 후처리해 클래스 결과·검증된 측정값·근거를 정형화 |
| 서버·백엔드 | 실제 사진을 선택 모델로 처리한 결과 JSON을 FastAPI·PostgreSQL에서 검증·저장·조회하고 공정표에 연결 |
| AI 에이전트 | 서버에 저장한 분석 결과·공정표·문서 근거를 조회해 보고서 생성, 수치·날짜·근거 검증 |
| 성능 개선 실험 | CLIP 지식 증류, VLM 라벨 검수 등의 적용 효과 검토 |

먼저 전처리와 사람 검수를 통해 학습 라벨을 만들고, 세 모델을 같은 데이터 기준으로 비교합니다. 이 비교 결과와 모델 선택은 프로젝트 산출물이며, 논문 주제 포함 여부는 미정입니다. 백엔드·Agent 작업은 비전 결과 JSON을 연결하는 방향입니다.

ResNet-50은 분할 모델의 백본·인코더로 사용합니다. SAM3는 학습에 쓸 GT 제작을 돕고, 실제 사진의 시공 상태 분석은 학습한 DeepLabv3+·U-Net·Mask R-CNN 후보 중 선택된 모델이 수행합니다. RoMa/LoMa 계열은 전후 사진의 대응 위치를 찾습니다. 상세 인풋·아웃풋과 비교 단계는 파이프라인 문서를 참고하세요.

- [현재 비전 파이프라인](docs/planning/current-vision-pipeline.md)
- [과제 변경 경위·기획 자료 요약](docs/planning/project-background.md)
- [시스템 구조도·팀별 일정 분석](docs/planning/02-03-analysis.md)
- [Git 산출물·이슈 초안·PR 순서](docs/planning/git-deliverables-plan.md)

모델 후보의 정확한 버전, 일정의 세부 날짜, 공정률 산식은 추가 합의 대상입니다. 실행 코드와 검증 결과가 준비되면 실행 방법과 데모를 추가합니다.

개발에는 Python·OpenCV·PyTorch를 사용하고, 보고 자동화에는 로컬 LLM을 활용할 계획입니다.

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
| [`docs/planning/`](docs/planning/) | 현재 시공 분석 과제의 구조·일정 분석 및 구현 기획 |
| [`docs/proposal/`](docs/proposal/) | 변경 전 알로요가 과제 제안서 요약·이력 |
| [`docs/meetings/`](docs/meetings/) | Notion과 동일하게 관리하는 Markdown 회의록 |
| [`docs/references/`](docs/references/) | 논문·기술문서 레퍼런스 및 리뷰 |
| `notebooks/` | 데이터 탐색·실험용 노트북 (작성 예정) |
| `src/` | 데이터 처리·모델·리포트 구현 코드 (작성 예정) |
| `data/` | 로컬 데이터 작업 공간 |

프로젝트 방향 전환의 배경은 [9월 17일 킥오프 회의록](docs/meetings/2026-09-17-client-kickoff.md)에 정리되어 있습니다. `docs/proposal/`의 최초 제안서 요약은 방향 전환 이전의 배경 자료입니다.

협업 및 커밋 규칙은 [기여 가이드](CONTRIBUTING.md)를 참고합니다. 기업 내부 자료는 공개 저장소에 올리지 않으며, 원본 데이터는 Git 추적에서 제외된 `data/raw/`에서 관리합니다.

## 팀원

| 이름 | 역할 |
| --- | --- |
| 이윤한 | 비전 팀 리드: 모델 학습·추론·학습 테스트 |
| 강태웅 | 비전 데이터 전처리 |
| 강원준 | Agent·RAG |
| 김병조 | DB·백엔드 |

세부 기술 선택과 업무 일정은 [파이프라인](docs/planning/current-vision-pipeline.md)과 [팀별 일정 분석](docs/planning/02-03-analysis.md)을 참고합니다.

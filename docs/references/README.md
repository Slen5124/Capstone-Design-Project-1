# 논문·레퍼런스

프로젝트 관련 논문, 기술 문서를 정리하는 공간.

> 아래 주제 분류와 읽을 목록 중 객체 검출·트래킹 / 존 카운팅 / 리테일 분석 항목은 최초 과제(고객 동선·Zone 분석) 기준입니다.
> 2026-09-17 방향 전환 뒤의 과제는 공사 사진의 **시공 상태 분할·진행률 산정**이므로, 지금 관련 있는 주제는
> `segmentation`(SAM3 · ConvNeXt/DINOv3 인코더 · U-Net/UPerNet 디코더), `construction-progress`(사진 기반 공정 진행률),
> `label-propagation`(시계열 사진의 라벨 전파·약지도), `privacy` 입니다.

## 작성 규칙

1. 읽은 자료는 [`TEMPLATE.md`](TEMPLATE.md)를 복사해 리뷰 노트를 남긴다.
2. 파일명은 `<저자성년도>-<짧은제목>.md` 형식으로 짓는다. (예: `wojke2017-deepsort.md`)
3. PDF 원문은 `papers/` 아래에 같은 이름으로 둔다. (예: `papers/wojke2017-deepsort.pdf`)
   - **저작권상 배포가 제한된 논문 PDF는 커밋하지 않고 DOI/링크만 남긴다.**
4. 아래 인덱스 표에 한 줄 추가한다.
5. 커밋 메시지: `docs: <논문 약칭> 리뷰 노트 추가`

## 주제 분류

| 태그 | 설명 |
| --- | --- |
| `cv-tracking` | 객체 검출·트래킹 (YOLO, DeepSORT, ByteTrack 등) |
| `zone-counting` | 존 카운팅, 라인 크로싱, 재식별(Re-ID) |
| `retail-analytics` | 리테일 매장 동선·체류·전환 분석 |
| `kpi-metrics` | KPI 설계, 유입률/침투율 등 지표 정의 |
| `privacy` | CCTV 데이터 프라이버시, 익명화 |
| `viz-dashboard` | 대시보드·시각화 방법론 |
| `segmentation` | 의미 분할 · 사전학습 인코더(SAM3, DINOv3, ConvNeXt) · 디코더 비교 |
| `construction-progress` | 사진 기반 공사 진행률 · 시공 상태 인식 |
| `label-propagation` | 시계열 사진의 라벨 전파 · 약지도 학습 |
| `vlm-hallucination` | 이미지 설명의 시각적 근거 검증과 오류 교정 |
| `long-context` | 긴 입력에서의 정보 활용·근거 인용 평가 |
| `agent-evaluation` | Agent 답변의 근거·정확성·판단불가 처리 평가 |

## 인덱스

| 자료 | 저자/연도 | 태그 | 한 줄 요약 | 노트 |
| --- | --- | --- | --- | --- |
| [Woodpecker](https://arxiv.org/abs/2310.16045) | Yin et al., 2024 | `vlm-hallucination`, `agent-evaluation` | 이미지 근거로 VLM 답변의 물체·속성 오류를 사후 교정 | [리뷰](yin2024-woodpecker.md) · [PDF](papers/yin2024-woodpecker.pdf) |
| [HELMET](https://arxiv.org/abs/2410.02694) | Yen et al., 2025 | `long-context`, `agent-evaluation` | 긴 문맥의 정보 활용을 RAG·인용·요약 등 7개 범주로 평가 | [리뷰](yen2025-helmet.md) · [PDF](papers/yen2025-helmet.pdf) |

## 읽을 목록 (Reading List)

프로젝트 초기 탐색용 후보. 읽고 나면 위 인덱스로 옮긴다.

- [ ] 객체 검출·트래킹 기법 서베이 (YOLO 계열, DeepSORT/ByteTrack 비교)
- [ ] 리테일 매장 내 고객 동선 분석 방법론
- [ ] 체류시간(Dwell time)과 구매 전환의 상관관계 연구
- [ ] 유입률(Capture Rate)·침투율(Penetration) 등 리테일 KPI 정의 문헌
- [ ] CCTV 기반 인원 계수의 정확도 검증 및 오차 보정 기법
- [ ] 영상 데이터 익명화·개인정보 보호 가이드라인

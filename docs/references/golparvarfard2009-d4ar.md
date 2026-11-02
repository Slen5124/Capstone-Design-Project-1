# Application of D4AR – A 4-Dimensional Augmented Reality Model for Automating Construction Progress Monitoring Data Collection, Processing and Communication

| 항목 | 내용 |
| --- | --- |
| 저자 | Mani Golparvar-Fard, Feniosky Peña-Mora, Silvio Savarese |
| 발표 연도 / 학회·저널 | 2009 / Journal of Information Technology in Construction (ITcon), 14, 129–153 |
| 링크 / DOI | [원문 안내](https://www.itcon.org/paper/2009/13) |
| 공식 코드·데이터 | 논문 페이지에서 원문 PDF 제공. 재현용 공식 코드 배포는 확인하지 못함 |
| 태그 | `construction-progress`, `3d-reconstruction` |
| 정리 | 논문 초안 참고문헌 [1]에 대응하는 팀 리뷰 초안 |
| 정리 날짜 | 2026-11-02 |

## 한 줄 요약

여러 현장 사진으로 복원한 실제 시공 모습과 공정 일정이 연결된 3D 설계를 겹쳐, 계획과 실제의 차이를 확인하는 초기 D4AR 연구다.

## 다루는 문제

현장 사진과 주간 보고가 따로 관리되면 사진 속 위치와 계획된 작업을 연결하기 어렵다. 작업자가 현장을 직접 방문하지 않아도 시공 상황을 공간적으로 확인할 수 있는 표현이 필요하다.

## 핵심 아이디어 / 방법

4D BIM은 3D 건물 모델에 공정 시간을 연결한 계획 모델이다. 이 논문은 사진 기반 실제 모습과 이 계획 모델을 정합하고 비교하는 흐름을 제안한다.

| 단계 | 입력 → 처리 → 출력 |
| --- | --- |
| 실제 현장 복원 | 여러 시점의 현장 사진 → 사진 간 대응점과 카메라 위치 추정 → 희소 3D 점군·사진 위치 |
| 계획과 연결 | 실제 복원 결과 + 계획 모델 → 공간 정합 → 같은 좌표에서 비교 가능한 계획·실제 정보 |
| 진행 상황 전달 | 정합 결과 + 일정 → 차이 시각화 → 진척 상황을 검토할 증강현실 화면 |

핵심은 사진을 많이 모으는 것보다, 사진이 어떤 설계 요소를 보여 주는지 연결하는 데 있다.

## 실험 및 결과

공식 초록은 진행 중인 세 건설 프로젝트의 예비 실험을 보고한다. 자동 분할 모델의 mIoU나 관측 설치율 MAE를 제시하는 논문으로 해석하면 안 된다. 이번에 확인한 공개 초록만으로 세부 정량 성능을 옮겨 적지 않았다. [ITcon 공식 초록](https://www.itcon.org/paper/2009/13)

## 우리 프로젝트에 적용할 점

아래는 **논문 내용을 바탕으로 한 프로젝트 적용 해석이며, 원 논문에서 우리 현장 데이터로 검증한 결과가 아니다.**

1. 사진·촬영 시각·구도·대상 ID·참조 이미지의 연결을 먼저 설계한다.
2. 현재처럼 고정 카메라와 수동 검수 ROI를 쓰는 경우, BIM 정합 전체를 구현하지 않고도 동일 대상의 관측 이력을 만들 수 있다.
3. 보고서에는 현재 사진과 기준 사진, 대상 ID, 관측 범위를 함께 제공한다. 3D 시각화는 자료가 확보됐을 때의 확장 과제로 둔다.

## 한계 및 주의점

- 여러 시점의 사진과 공간 정합을 이용하는 연구다. 한 카메라의 비슷한 사진만으로 안정적인 3D 복원을 보장하지 않는다.
- 시각적 비교 체계와 픽셀 단위 상태 분할·설치 비율 계산은 별도 문제다.
- 공식 제목에는 **Application of D4AR**가 포함된다. 초안 참고문헌의 제목은 최종 제출 전에 이 표기와 맞추는 것이 좋다.

## 원문 및 공유 정보

[원문 안내](https://www.itcon.org/paper/2009/13)에서 자료를 확인할 수 있다. 

PDF와 현장 원본 자료는 이 리뷰에 첨부하지 않았다. 코드 공개 여부와 논문·가중치·데이터의 이용 조건은 별개이며, 실제 배포에는 사용하는 버전의 조건을 확인한다.

## 인용 (BibTeX)

```bibtex
@article{golparvarfard2009d4ar,
  title={Application of {D4AR} -- A 4-Dimensional Augmented Reality Model for Automating Construction Progress Monitoring Data Collection, Processing and Communication},
  author={Golparvar-Fard, Mani and Peña-Mora, Feniosky and Savarese, Silvio},
  journal={Journal of Information Technology in Construction},
  volume={14}, pages={129--153}, year={2009},
  url={https://www.itcon.org/paper/2009/13}
}
```

# WILDS: A Benchmark of in-the-Wild Distribution Shifts

| 항목 | 내용 |
| --- | --- |
| 저자 | Pang Wei Koh 외 공동저자 |
| 발표 연도 / 학회·저널 | 2021 / ICML, PMLR 139, 5637–5664 |
| 링크 / DOI | [원문 안내](https://proceedings.mlr.press/v139/koh21a.html) |
| 공식 코드·데이터 | [p-lambda/wilds](https://github.com/p-lambda/wilds) · [프로젝트](https://wilds.stanford.edu/) |
| 태그 | `domain-generalization`, `evaluation` |
| 정리 | 논문 초안 참고문헌 [14]에 대응하는 팀 리뷰 초안 |
| 정리 날짜 | 2026-11-02 |

## 한 줄 요약

병원·카메라 위치·시간 등이 달라질 때 발생하는 실제 분포 변화를 모아, 학습 환경 밖에서의 성능을 평가하는 벤치마크다.

## 다루는 문제

무작위로 나눈 시험 데이터에서 잘 작동하는 모델도 새로운 장소·장비·시기에 실패할 수 있다. 실제 변화 조건이 반영된 평가 데이터와 분할 규칙이 필요하다.

## 핵심 아이디어 / 방법

WILDS는 현실에서 발생하는 분포 이동을 가진 10개 데이터셋을 묶고, 데이터별 학습·검증·시험 구성을 제공한다. 평가 지표는 과제에 따라 평균 성능이나 그룹별 성능 등으로 구분된다.

| 구분 | 예시와 의미 |
| --- | --- |
| 환경 변화 | 다른 병원, 다른 촬영 위치, 다른 시간 등 |
| In-distribution | 학습 환경과 유사한 조건 |
| Out-of-distribution | 학습 환경과 다른 조건 |
| 그룹별 평가 | 전체 평균에 가려진 특정 환경의 실패 확인 |

WILDS는 하나의 보정 모델이 아니라 데이터·분할·평가 체계다.

## 실험 및 결과

논문은 평가한 실제 분포 변화에서 표준 학습 모델의 성능 저하와 기존 방법의 한계를 보여 준다. 데이터셋별 과제·지표가 달라 평균 점수 하나를 다른 과제와 직접 비교하지 않는다. [PMLR 공식 논문](https://proceedings.mlr.press/v139/koh21a.html)

## 우리 프로젝트에 적용할 점

아래는 **논문 내용을 바탕으로 한 프로젝트 적용 해석이며, 원 논문에서 우리 현장 데이터로 검증한 결과가 아니다.**

1. 현장별 배경·재료·카메라·조명 차이를 실제 분포 이동으로 보고 현장을 분리해 평가한다.
2. 전체 사진 평균뿐 아니라 현장별 MAE, 현장 동일 가중 macro, 작은 ROI·가림 등 조건별 성능을 제시한다.
3. 같은 현장의 성능과 새 현장의 성능을 나란히 보여 일반화 간극을 설명한다.
4. WILDS의 관점을 참고한 자체 LOSO 실험이지, 우리 자료가 WILDS 공식 데이터셋인 것은 아니다.

## 한계 및 주의점

- 벤치마크의 존재가 도메인 일반화 해결을 뜻하지 않는다.
- 독립 현장 수가 적으면 많은 사진을 확보해도 환경 다양성은 제한된다.
- 시험 현장의 라벨로 추론 기법을 선택하면 새로운 환경에서의 독립 평가라는 의미가 약해진다.

## 원문 및 공유 정보

[원문 안내](https://proceedings.mlr.press/v139/koh21a.html)에서 자료를 확인할 수 있다. 

PDF와 현장 원본 자료는 이 리뷰에 첨부하지 않았다. 코드 공개 여부와 논문·가중치·데이터의 이용 조건은 별개이며, 실제 배포에는 사용하는 버전의 조건을 확인한다.

## 인용 (BibTeX)

```bibtex
@inproceedings{koh2021wilds,
  title={{WILDS}: A Benchmark of in-the-Wild Distribution Shifts},
  author={Koh, Pang Wei and Sagawa, Shiori and Marklund, Henrik and others},
  booktitle={Proceedings of the 38th International Conference on Machine Learning},
  series={Proceedings of Machine Learning Research}, volume={139},
  pages={5637--5664}, publisher={PMLR}, year={2021},
  url={https://proceedings.mlr.press/v139/koh21a.html}
}
```

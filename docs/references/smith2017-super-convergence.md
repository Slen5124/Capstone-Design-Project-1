# Super-Convergence: Very Fast Training of Neural Networks Using Large Learning Rates

| 항목 | 내용 |
| --- | --- |
| 저자 | Leslie N. Smith, Nicholay Topin |
| 발표 연도 / 학회·저널 | 2017 / arXiv:1708.07120 최초 공개. 이 노트의 BibTeX는 초안이 인용한 arXiv판 기준 |
| 링크 / DOI | [원문 안내](https://arxiv.org/abs/1708.07120) |
| 공식 코드·데이터 | [lnsmith54/super-convergence](https://github.com/lnsmith54/super-convergence) |
| 태그 | `optimization` |
| 정리 | 논문 초안 참고문헌 [18]에 대응하는 팀 리뷰 초안 |
| 정리 날짜 | 2026-11-02 |

## 한 줄 요약

학습률을 크게 올렸다가 낮추는 한 번의 주기와 정규화 균형을 통해, 일부 설정에서 적은 반복으로 좋은 성능에 도달할 수 있음을 보인다.

## 다루는 문제

작은 고정 학습률로 오랫동안 학습하면 실험 비용이 커질 수 있다. 학습률의 크기와 변화 방식이 수렴 속도와 일반화에 미치는 영향을 살펴본다.

## 핵심 아이디어 / 방법

학습 중 학습률을 상승시켰다가 낮추는 주기를 사용한다. 큰 학습률 자체가 정규화처럼 작용할 수 있으므로 weight decay 등 다른 정규화 강도와 균형을 맞춘다. 적절한 최대 학습률을 찾는 과정도 중요하다.

| 입력 | 처리 | 출력 |
| --- | --- | --- |
| 전체 학습 step 수, 최대 학습률, 주기 설정 | 진행률에 따른 학습률 변경 | 각 step에 적용할 학습률 |

OneCycle은 모델 구조가 아니라 학습 스케줄이다. 특정 라이브러리의 OneCycleLR 옵션이 원 논문의 모든 설정과 동일한 것은 아니다.

## 실험 및 결과

논문은 여러 이미지 분류 설정에서 큰 학습률과 주기적 변화로 학습 속도를 크게 줄일 수 있음을 보고한다. “한 자릿수 epoch면 모든 모델이 충분하다”거나 “우리 구성도 동일 배수로 빨라진다”는 의미는 아니다. [논문](https://arxiv.org/abs/1708.07120)

## 우리 프로젝트에 적용할 점

아래는 **논문 내용을 바탕으로 한 프로젝트 적용 해석이며, 원 논문에서 우리 현장 데이터로 검증한 결과가 아니다.**

1. 초안의 OneCycle 학습률 정책을 설명하는 근거로 인용한다.
2. 총 step 수, 최대 학습률, 상승 구간 비율, optimizer와의 조합을 기록한다.
3. 학습 재개·중단 시 scheduler 상태가 이어지는지 확인하고, 마지막 모델과 검증 기준 최선 모델을 구분한다.
4. 디코더 간 비교에서는 같은 스케줄을 기본으로 하되, 특정 모델의 수치 불안정으로 학습이 실패한 경우를 숨기지 않는다.

## 한계 및 주의점

- 큰 학습률은 발산을 유발할 수 있다. 원 논문의 최대 학습률을 우리 디코더에 그대로 적용하지 않는다.
- 고정 DINOv2 위에서 헤드만 학습하는 조건은 전체 분류 모델 학습과 다르다.
- 시험 현장의 성능을 보고 학습률을 조정하면 독립적인 새 현장 평가가 훼손된다.
- 참고문헌의 연도는 arXiv판·후속 출판본 중 실제 인용한 판본에 맞춘다.

## 원문 및 공유 정보

[원문 안내](https://arxiv.org/abs/1708.07120)에서 자료를 확인할 수 있다. 

PDF와 현장 원본 자료는 이 리뷰에 첨부하지 않았다. 코드 공개 여부와 논문·가중치·데이터의 이용 조건은 별개이며, 실제 배포에는 사용하는 버전의 조건을 확인한다.

## 인용 (BibTeX)

```bibtex
@misc{smith2017superconvergence,
  title={Super-Convergence: Very Fast Training of Neural Networks Using Large Learning Rates},
  author={Smith, Leslie N. and Topin, Nicholay}, year={2017},
  eprint={1708.07120}, archivePrefix={arXiv}, primaryClass={cs.LG},
  url={https://arxiv.org/abs/1708.07120}
}
```

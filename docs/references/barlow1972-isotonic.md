# Statistical Inference under Order Restrictions: The Theory and Application of Isotonic Regression

| 항목 | 내용 |
| --- | --- |
| 저자 | Richard E. Barlow, D. J. Bartholomew, J. M. Bremner, H. D. Brunk |
| 발표 연도 / 학회·저널 | 1972 / Wiley, 통계학 전문서적. ISBN 0471049700 |
| 링크 / DOI | [원문 안내](https://books.google.com/books?id=DEamySUDBWcC) |
| 공식 코드·데이터 | 서적 자체의 공식 소프트웨어는 확인하지 못함. 현대 구현 예: [SciPy isotonic_regression](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.isotonic_regression.html) |
| 태그 | `statistics`, `temporal-calibration` |
| 정리 | 논문 초안 참고문헌 [9]에 대응하는 팀 리뷰 초안 |
| 정리 날짜 | 2026-11-02 |

## 한 줄 요약

관측값에 증가·감소 같은 순서 제약을 주어 추정하는 통계 이론의 고전 서적이며, 설치 비율의 시간축 단조 보정을 설명하는 이론적 배경이다.

## 다루는 문제

관측치에 잡음이 있어도 실제 대상은 일정한 순서 관계를 가질 수 있다. 예를 들어 철거가 없는 동일 작업의 누적 설치량은 감소하지 않는다는 가정을 둘 수 있다.

## 핵심 아이디어 / 방법

가중 최소제곱 isotonic regression은 관측값 y와 양의 가중치 w가 있을 때, 다음 문제를 푼다.

```text
minimize  Σ_t w_t (y_t − z_t)²
subject to z_1 ≤ z_2 ≤ ... ≤ z_T
```

PAVA(Pool Adjacent Violators Algorithm)는 순서 제약을 위반하는 이웃 구간을 합쳐 해를 구한다. 출력은 제약을 만족하는 보정 수열이다. 가중치가 곧 “올바른 confidence”가 되는 것은 아니며, 가중치를 어떻게 정할지는 별도의 설계 문제다. [SciPy 공식 설명](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.isotonic_regression.html)

## 실험 및 결과

이 자료는 현대 분할 모델의 벤치마크 논문이 아니라 통계학 서적이다. 이번에 전체 본문을 검토하지 않았으므로 책의 정리·증명·사례를 상세하게 재현했다고 주장하지 않는다. 알고리즘이 단조 수열을 만든다는 사실과 실제 설치 비율 MAE가 줄어든다는 주장은 별개다.

## 우리 프로젝트에 적용할 점

아래는 **논문 내용을 바탕으로 한 프로젝트 적용 해석이며, 원 논문에서 우리 현장 데이터로 검증한 결과가 아니다.**

1. 같은 대상·구도·작업 단계의 관측치를 묶고 철거·재시공처럼 단조 가정이 깨지는 구간을 구분한다.
2. 전체 기간을 한 번에 보정하는 오프라인 방식과, 현재 시점 이전 자료만 쓰는 인과적 방식을 구분한다.
3. 기본 예측, isotonic 보정, 가시성 가중, 재시작 규칙을 나눠 평가한다.
4. 과대 예측이 존재할 때 누적 최댓값 방식이 오차를 고착시키는지 검사한다.

## 한계 및 주의점

- Isotonic regression은 누적 최댓값과 다르다. 전체 수열 최적화는 값의 상승·하강 조정 모두 가능하다.
- 시점마다 과거 구간에 다시 isotonic을 적용하고 마지막 값만 출력하면, 매번 생성된 최종 출력들의 수열까지 자동으로 단조가 되는 것은 아니다. 별도의 온라인 제약과 구분한다.
- 가림 때문에 관측 분모가 달라진 비율과 실제 누적 작업량의 단조성은 같지 않다.
- 1973년 서평의 DOI를 이 1972년 책의 DOI로 넣으면 안 된다.

## 원문 및 공유 정보

[원문 안내](https://books.google.com/books?id=DEamySUDBWcC)에서 자료를 확인할 수 있다. 책의 서지 정보와 [현대 SciPy 구현 설명](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.isotonic_regression.html)을 구분해 사용했다. SciPy 구현을 원저자의 공식 코드로 소개하지 않는다.

PDF와 현장 원본 자료는 이 리뷰에 첨부하지 않았다. 코드 공개 여부와 논문·가중치·데이터의 이용 조건은 별개이며, 실제 배포에는 사용하는 버전의 조건을 확인한다.

## 인용 (BibTeX)

```bibtex
@book{barlow1972isotonic,
  title={Statistical Inference under Order Restrictions: The Theory and Application of Isotonic Regression},
  author={Barlow, Richard E. and Bartholomew, D. J. and Bremner, J. M. and Brunk, H. D.},
  publisher={Wiley}, year={1972}, isbn={0471049700}
}
```

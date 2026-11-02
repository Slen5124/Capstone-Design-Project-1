# In Search of Lost Domain Generalization

| 항목 | 내용 |
| --- | --- |
| 저자 | Ishaan Gulrajani, David Lopez-Paz |
| 발표 연도 / 학회·저널 | 2021 / ICLR. arXiv 최초 공개 2020년 |
| 링크 / DOI | [원문 안내](https://arxiv.org/abs/2007.01434) |
| 공식 코드·데이터 | [facebookresearch/DomainBed](https://github.com/facebookresearch/DomainBed) |
| 태그 | `domain-generalization`, `evaluation` |
| 정리 | 논문 초안 참고문헌 [3]에 대응하는 팀 리뷰 초안 |
| 정리 날짜 | 2026-11-02 |

## 한 줄 요약

새 도메인 성능은 복잡한 학습법만이 아니라 공정한 탐색 예산과 모델 선택 규칙에 크게 좌우되며, 잘 조정한 ERM이 강한 기준선임을 보여 준다.

## 다루는 문제

학습 때 보지 못한 환경에서도 잘 작동하도록 만드는 도메인 일반화 연구는, 구현과 모델 선택 방법이 달라 알고리즘만의 효과를 비교하기 어려웠다.

## 핵심 아이디어 / 방법

DomainBed는 여러 데이터셋·알고리즘을 공통 환경에서 비교한다. ERM(Empirical Risk Minimization)은 주어진 학습 데이터의 평균 손실을 줄이는 일반적인 지도 학습이다.

| 평가 구성 | 의미 |
| --- | --- |
| 학습 도메인 | 모델이 학습할 수 있는 환경 |
| 평가 도메인 | 학습에서 제외한 새로운 환경 |
| 모델 선택 규칙 | 어떤 검증 데이터로 하이퍼파라미터와 모델을 고르는지 |

알고리즘과 별개로 모델 선택 규칙을 명시하고 동일한 탐색 조건을 제공해야 한다. 시험 도메인의 라벨을 이용하는 선택 방식은 실제 배포 가능한 선택 방식과 구분된다.

## 실험 및 결과

논문은 여러 도메인 일반화 벤치마크에서 신중하게 구현·조정한 ERM을 기존 방법이 일관되게 능가하지 못했다고 보고한다. 이것은 모든 새로운 알고리즘이 무의미하다는 결론이 아니라, 비교 기준선과 선택 절차가 중요하다는 결과다. 버전별 실험 구성이 달라 여기서는 알고리즘 수나 평균 점수를 혼용하지 않았다. [논문 초록](https://arxiv.org/abs/2007.01434)

## 우리 프로젝트에 적용할 점

아래는 **논문 내용을 바탕으로 한 프로젝트 적용 해석이며, 원 논문에서 우리 현장 데이터로 검증한 결과가 아니다.**

1. 현장 하나를 시험용으로 제외하는 LOSO를 새 현장 일반화 평가로 사용한다.
2. 제외한 현장의 정답으로 디코더·추론 옵션·보정 임계값을 고르지 않는다.
3. Linear·MLP·UPerNet·DPT 비교에서 인코더, 학습 데이터, 시드, 학습 예산, 선택 규칙을 맞춘다.
4. 우리 LOSO는 DomainBed의 평가 관점을 참고한 자체 과제이며 공식 벤치마크 재현 결과는 아니다.

## 한계 및 주의점

- 주로 분류 중심의 도메인 일반화 연구이며 시공 상태 분할이나 설치 비율 산식을 제공하지 않는다.
- 같은 현장의 인접 사진을 무작위 분할한 성능은 새 현장 성능을 대신하지 못한다.
- 시험 결과를 보고 최종 구성을 선택했다면, 그 시험 세트는 더 이상 독립적인 최종 검증으로 보기 어렵다.

## 원문 및 공유 정보

[원문 안내](https://arxiv.org/abs/2007.01434)에서 자료를 확인할 수 있다. 공식 구현과 모델 선택 규칙은 [DomainBed](https://github.com/facebookresearch/DomainBed)를 참고한다.

PDF와 현장 원본 자료는 이 리뷰에 첨부하지 않았다. 코드 공개 여부와 논문·가중치·데이터의 이용 조건은 별개이며, 실제 배포에는 사용하는 버전의 조건을 확인한다.

## 인용 (BibTeX)

```bibtex
@inproceedings{gulrajani2021domain,
  title={In Search of Lost Domain Generalization},
  author={Gulrajani, Ishaan and Lopez-Paz, David},
  booktitle={International Conference on Learning Representations}, year={2021},
  url={https://arxiv.org/abs/2007.01434}
}
```

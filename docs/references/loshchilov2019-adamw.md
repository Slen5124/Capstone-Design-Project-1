# Decoupled Weight Decay Regularization

| 항목 | 내용 |
| --- | --- |
| 저자 | Ilya Loshchilov, Frank Hutter |
| 발표 연도 / 학회·저널 | 2019 / ICLR. arXiv 최초 공개 2017년 |
| 링크 / DOI | [원문 안내](https://arxiv.org/abs/1711.05101) |
| 공식 코드·데이터 | [loshchil/AdamW-and-SGDW](https://github.com/loshchil/AdamW-and-SGDW) |
| 태그 | `optimization` |
| 정리 | 논문 초안 참고문헌 [17]에 대응하는 팀 리뷰 초안 |
| 정리 날짜 | 2026-11-02 |

## 한 줄 요약

Adam에서 L2 손실 항과 weight decay가 같지 않다는 점을 짚고, 가중치 감쇠를 적응적 gradient 업데이트와 분리한 AdamW를 제안한다.

## 다루는 문제

Adam은 파라미터마다 gradient 크기에 따라 업데이트를 조정한다. 이때 L2 항을 gradient에 섞으면 일반적인 가중치 감쇠와 다르게 작동하고 학습률·정규화 효과가 얽힌다.

## 핵심 아이디어 / 방법

AdamW는 데이터 손실로 계산한 Adam 업데이트와, 파라미터를 줄이는 weight decay를 분리한다. 핵심을 단순화하면 다음과 같다.

```text
θ_new = (1 − learning_rate × weight_decay) × θ
        − learning_rate × Adam의 적응적 gradient 업데이트
```

| 입력 | 출력 |
| --- | --- |
| 현재 학습 파라미터, gradient, optimizer 상태 | 다음 학습 파라미터와 optimizer 상태 |

이는 이미지 인식 모델이나 출력 헤드가 아니라 학습 중 파라미터를 갱신하는 최적화 방법이다.

## 실험 및 결과

논문은 이미지 분류 실험 등에서 decoupled weight decay의 일반화 효과와 학습률·감쇠 설정의 관계를 평가한다. AdamW가 모든 데이터와 모델에서 SGD 또는 Adam보다 우수하다는 보장은 아니다. [논문 초록](https://arxiv.org/abs/1711.05101)

## 우리 프로젝트에 적용할 점

아래는 **논문 내용을 바탕으로 한 프로젝트 적용 해석이며, 원 논문에서 우리 현장 데이터로 검증한 결과가 아니다.**

1. 동결 인코더를 제외한 디코더·출력 헤드의 학습 파라미터를 optimizer에 전달한다.
2. 디코더 비교에서는 optimizer, 학습률, 감쇠, 제외 파라미터 규칙을 같게 하거나 차이를 명시한다.
3. 학습 재개를 위해 모델뿐 아니라 optimizer·scheduler·scaler 상태도 저장한다.
4. AdamW 사용은 재현 가능한 구현 설정이지 이 과제의 새로운 연구 기여로 주장할 부분은 아니다.

## 한계 및 주의점

- Weight decay와 손실 함수에 L2 항을 추가하는 처리를 무심코 중복 적용하지 않는다.
- 인코더를 동결했다면 그 가중치를 업데이트했다고 서술하면 안 된다.
- 정규화 설정만으로 잘못된 라벨·현장 간 분포 차이·측정 분모 문제를 해결할 수 없다.

## 원문 및 공유 정보

[원문 안내](https://arxiv.org/abs/1711.05101)에서 자료를 확인할 수 있다. 

PDF와 현장 원본 자료는 이 리뷰에 첨부하지 않았다. 코드 공개 여부와 논문·가중치·데이터의 이용 조건은 별개이며, 실제 배포에는 사용하는 버전의 조건을 확인한다.

## 인용 (BibTeX)

```bibtex
@inproceedings{loshchilov2019adamw,
  title={Decoupled Weight Decay Regularization},
  author={Loshchilov, Ilya and Hutter, Frank},
  booktitle={International Conference on Learning Representations}, year={2019},
  url={https://arxiv.org/abs/1711.05101}
}
```

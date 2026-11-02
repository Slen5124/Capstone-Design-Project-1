# Vision Transformers Need Registers

| 항목 | 내용 |
| --- | --- |
| 저자 | Timothée Darcet, Maxime Oquab, Julien Mairal, Piotr Bojanowski |
| 발표 연도 / 학회·저널 | 2024 / ICLR. arXiv 최초 공개 2023년 |
| 링크 / DOI | [원문 안내](https://arxiv.org/abs/2309.16588) |
| 공식 코드·데이터 | [facebookresearch/dinov2](https://github.com/facebookresearch/dinov2): register 포함 사전학습 모델 제공 |
| 태그 | `segmentation`, `self-supervised-learning` |
| 정리 | 논문 초안 참고문헌 [5]에 대응하는 팀 리뷰 초안 |
| 정리 날짜 | 2026-11-02 |

## 한 줄 요약

ViT가 일부 이미지 패치를 내부 계산 공간으로 쓰면서 만드는 특징 이상을 줄이기 위해, 이미지 위치에 대응하지 않는 별도 register 토큰을 학습에 추가한다.

## 다루는 문제

ViT 특징 지도에서 배경 패치에 유난히 큰 노름의 토큰이 나타날 수 있다. 이런 패치가 실제 시각 정보보다 내부 계산에 사용되면 픽셀 위치를 해석해야 하는 작업에 방해가 된다.

## 핵심 아이디어 / 방법

이미지 패치 토큰과 별개로 학습 가능한 register 토큰을 넣고 함께 attention을 수행한다. 모델이 내부 정보를 저장할 여유 공간을 갖도록 하는 것이다. dense prediction에서는 이미지에 대응하는 패치 특징을 사용하며 register는 이미지 픽셀로 복원하지 않는다.

| 입력 | 처리 | 출력 |
| --- | --- | --- |
| 이미지 패치 토큰 + 전역 토큰 + register 토큰 | ViT attention | 패치 특징과 비공간 토큰 |

Register는 대상별 메모리나 벽 ID가 아니다. 특정 벽의 이전 상태를 지속 저장하는 장치로 해석하면 안 된다.

## 실험 및 결과

저자들은 register를 포함해 학습한 모델에서 특징·attention 지도의 이상이 줄고 일부 dense prediction 과제에 도움이 됨을 보고한다. 이 결과는 시공 상태 라벨에 대한 검증은 아니다. [논문](https://arxiv.org/abs/2309.16588)

## 우리 프로젝트에 적용할 점

아래는 **논문 내용을 바탕으로 한 프로젝트 적용 해석이며, 원 논문에서 우리 현장 데이터로 검증한 결과가 아니다.**

1. register를 포함해 사전학습된 DINOv2 체크포인트를 사용하는 경우 모델명에 그 사실을 기록한다.
2. 중간 특징을 H×W로 복원할 때 CLS·register 토큰을 이미지 패치와 섞지 않는다. 사용하는 라이브러리의 반환 규약을 확인한다.
3. 벽 ID와 시계열 비교는 ROI·대상 메타데이터로 관리하며 register의 역할과 분리한다.

## 한계 및 주의점

- 일반 DINOv2 체크포인트에 임의 register를 추론 시 추가한다고 논문 효과가 재현되지 않는다.
- 경계나 작은 벽 인식 개선을 보장하지 않는다. 별도의 현장별 평가가 필요하다.
- Register 유무 효과를 주장하려면 가능한 한 다른 조건을 맞춘 체크포인트 비교가 필요하다.

## 원문 및 공유 정보

[원문 안내](https://arxiv.org/abs/2309.16588)에서 자료를 확인할 수 있다. 

PDF와 현장 원본 자료는 이 리뷰에 첨부하지 않았다. 코드 공개 여부와 논문·가중치·데이터의 이용 조건은 별개이며, 실제 배포에는 사용하는 버전의 조건을 확인한다.

## 인용 (BibTeX)

```bibtex
@inproceedings{darcet2024registers,
  title={Vision Transformers Need Registers},
  author={Darcet, Timothée and Oquab, Maxime and Mairal, Julien and Bojanowski, Piotr},
  booktitle={International Conference on Learning Representations}, year={2024},
  url={https://arxiv.org/abs/2309.16588}
}
```

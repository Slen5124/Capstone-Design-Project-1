# SegFormer: Simple and Efficient Design for Semantic Segmentation with Transformers

| 항목 | 내용 |
| --- | --- |
| 저자 | Enze Xie, Wenhai Wang, Zhiding Yu, Anima Anandkumar, Jose M. Alvarez, Ping Luo |
| 발표 연도 / 학회·저널 | 2021 / NeurIPS, 34, 12077–12090 |
| 링크 / DOI | [원문 안내](https://arxiv.org/abs/2105.15203) |
| 공식 코드·데이터 | [NVlabs/SegFormer](https://github.com/NVlabs/SegFormer) |
| 태그 | `segmentation`, `decoder` |
| 정리 | 논문 초안 참고문헌 [7]에 대응하는 팀 리뷰 초안 |
| 정리 날짜 | 2026-11-02 |

## 한 줄 요약

계층적 Transformer 인코더와 가벼운 MLP 디코더를 결합해, 복잡한 디코더 없이 여러 크기의 특징을 융합하는 의미 분할 모델이다.

## 다루는 문제

분할 모델은 넓은 문맥과 세밀한 위치 정보가 필요하지만, 디코더가 복잡해지면 연산과 파라미터가 늘어난다. 학습·추론 해상도가 달라졌을 때의 대응도 고려해야 한다.

## 핵심 아이디어 / 방법

MiT(Mix Transformer) 인코더가 입력 대비 1/4·1/8·1/16·1/32 해상도의 특징을 만든다. 디코더는 각 특징의 채널 수를 MLP로 맞춘 뒤 1/4 해상도로 확대하고, 이어 붙여 융합·분류한다. MiT는 명시적 positional encoding 없이 공간 정보를 처리하도록 설계된다.

| 입력 | 중간 표현 | 출력 |
| --- | --- | --- |
| RGB 이미지 | MiT의 네 단계 특징 → MLP 투영·업샘플·융합 | 픽셀별 클래스 점수·의미 마스크 |

MLP는 여기서 이미지 전체의 단일 라벨만 만드는 분류기가 아니라, 위치별 특징을 변환·융합하는 디코더 구성 요소다.

## 실험 및 결과

논문 초록은 SegFormer-B4가 ADE20K에서 **50.3% mIoU, 64M 파라미터**를 달성했다고 보고한다. 이 수치는 MiT를 포함한 SegFormer 구성의 결과다. DINOv2에 MLP만 붙인 모델의 결과가 아니다. [논문 초록](https://arxiv.org/abs/2105.15203)

## 우리 프로젝트에 적용할 점

아래는 **논문 내용을 바탕으로 한 프로젝트 적용 해석이며, 원 논문에서 우리 현장 데이터로 검증한 결과가 아니다.**

1. 현재 비교의 MLP 디코더를 설명할 때 특징 투영·크기 통일·융합의 출처로 활용한다.
2. DINOv2 특징을 사용한다면 “SegFormer에서 착안한 MLP 디코더”로 구분한다.
3. UPerNet·DPT보다 가벼운 디코더가 충분한지 비교한다. 디코더 파라미터와 전체 모델 파라미터를 별도 기록한다.
4. 같은 인코더·라벨·학습 예산으로 비교하고, 결과 마스크로 계산한 설치 비율 오차도 평가한다.

## 한계 및 주의점

- MiT와 DINOv2는 특징의 해상도·학습 방식이 다르다. 원 논문의 성능을 MLP 구조만의 효과라고 볼 수 없다.
- 전체 SegFormer를 구현하지 않았다면 모델 이름만 “SegFormer”라고 쓰는 것은 부정확하다.
- 공식 코드가 있다는 사실만으로 모든 체크포인트·데이터의 상업 이용 조건이 같다고 판단하지 않는다.

## 원문 및 공유 정보

[원문 안내](https://arxiv.org/abs/2105.15203)에서 자료를 확인할 수 있다. 디코더 설명은 [공개 원문](https://arxiv.org/html/2105.15203v3)의 방법 절을 참고한다.

PDF와 현장 원본 자료는 이 리뷰에 첨부하지 않았다. 코드 공개 여부와 논문·가중치·데이터의 이용 조건은 별개이며, 실제 배포에는 사용하는 버전의 조건을 확인한다.

## 인용 (BibTeX)

```bibtex
@inproceedings{xie2021segformer,
  title={{SegFormer}: Simple and Efficient Design for Semantic Segmentation with Transformers},
  author={Xie, Enze and Wang, Wenhai and Yu, Zhiding and Anandkumar, Anima and Alvarez, Jose M. and Luo, Ping},
  booktitle={Advances in Neural Information Processing Systems},
  volume={34}, pages={12077--12090}, year={2021},
  url={https://arxiv.org/abs/2105.15203}
}
```

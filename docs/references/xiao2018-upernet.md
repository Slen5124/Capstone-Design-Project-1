# Unified Perceptual Parsing for Scene Understanding

| 항목 | 내용 |
| --- | --- |
| 저자 | Tete Xiao, Yingcheng Liu, Bolei Zhou, Yuning Jiang, Jian Sun |
| 발표 연도 / 학회·저널 | 2018 / ECCV, 418–434 |
| 링크 / DOI | [원문 안내](https://arxiv.org/abs/1807.10221) |
| 공식 코드·데이터 | [CSAILVision/unifiedparsing](https://github.com/CSAILVision/unifiedparsing): 공식 구현·데이터 준비 안내 |
| 태그 | `segmentation`, `decoder` |
| 정리 | 논문 초안 참고문헌 [6]에 대응하는 팀 리뷰 초안 |
| 정리 날짜 | 2026-11-02 |

## 한 줄 요약

장면·물체·부분·재료 등을 함께 해석하기 위해, 넓은 문맥과 여러 해상도의 특징을 결합하는 UPerNet을 제안한다.

## 다루는 문제

큰 장면의 문맥과 작은 물체의 경계를 동시에 이해하려면 한 해상도의 특징만으로는 부족하다. 서로 다른 종류의 주석도 함께 활용할 수 있어야 한다.

## 핵심 아이디어 / 방법

최상위 특징에 PPM(Pyramid Pooling Module)을 적용해 여러 범위의 문맥을 모으고, FPN(Feature Pyramid Network)으로 상위 의미 정보와 세밀한 하위 특징을 결합한다. 원 논문은 해석 대상에 따라 서로 다른 위치에 예측 헤드를 연결한다.

| 부분 | 입력 → 출력 |
| --- | --- |
| PPM | 최상위 특징 → 여러 공간 범위의 문맥을 포함한 특징 |
| FPN | 여러 단계 특징 → 해상도가 다른 융합 특징 |
| 예측 헤드 | 융합 특징 → 물체·부분·재료 등의 클래스 점수 |

원래의 통합 장면 해석 전체와, 우리가 사용하는 **UPerNet 형태의 분할 디코더**는 범위가 다르다.

## 실험 및 결과

논문은 Broden+를 구성하고 장면·물체·부분·재료·질감 해석 및 구성 요소의 효과를 평가한다. 과제마다 평가 지표와 주석이 달라 하나의 정확도로 합치지 않는다. 이번 리뷰에는 조건을 완전히 대조하지 않은 세부 수치를 옮기지 않았다. [원문](https://arxiv.org/html/1807.10221v1)

## 우리 프로젝트에 적용할 점

아래는 **논문 내용을 바탕으로 한 프로젝트 적용 해석이며, 원 논문에서 우리 현장 데이터로 검증한 결과가 아니다.**

1. DINOv2 중간 블록에서 얻은 특징을 PPM·FPN 기반 디코더에 연결한다.
2. ViT의 여러 블록은 보통 같은 공간 해상도를 가지므로, CNN의 자연스러운 다중 해상도 피라미드와 구분해 어댑터·리샘플링 구조를 명시한다.
3. 마지막 특징에서 시공 상태 등 우리 라벨 체계에 맞는 출력 헤드를 학습한다.
4. 논문에는 “DINOv2+UPerNet 기반 디코더”로 쓰고, 원 논문 전체를 그대로 재현했다고 표현하지 않는다.

## 한계 및 주의점

- 디코더는 대상의 영구 ID나 일정 대비 지연 여부를 알지 못한다.
- 큰 문맥을 결합한다고 작은 ROI의 오차가 자동으로 줄어드는 것은 아니다.
- 설치 비율은 출력 마스크와 ROI·가시성 규칙을 이용하는 후처리에서 계산한다.

## 원문 및 공유 정보

[원문 안내](https://arxiv.org/abs/1807.10221)에서 자료를 확인할 수 있다. 

PDF와 현장 원본 자료는 이 리뷰에 첨부하지 않았다. 코드 공개 여부와 논문·가중치·데이터의 이용 조건은 별개이며, 실제 배포에는 사용하는 버전의 조건을 확인한다.

## 인용 (BibTeX)

```bibtex
@inproceedings{xiao2018unified,
  title={Unified Perceptual Parsing for Scene Understanding},
  author={Xiao, Tete and Liu, Yingcheng and Zhou, Bolei and Jiang, Yuning and Sun, Jian},
  booktitle={European Conference on Computer Vision}, pages={418--434}, year={2018},
  url={https://arxiv.org/abs/1807.10221}
}
```

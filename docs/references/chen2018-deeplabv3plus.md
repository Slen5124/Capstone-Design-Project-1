# Encoder-Decoder with Atrous Separable Convolution for Semantic Image Segmentation

| 항목 | 내용 |
| --- | --- |
| 저자 | Liang-Chieh Chen, Yukun Zhu, George Papandreou, Florian Schroff, Hartwig Adam |
| 발표 연도 / 학회·저널 | 2018 / ECCV, 833–851 |
| 링크 / DOI | [원문 안내](https://arxiv.org/abs/1802.02611) |
| 공식 코드·데이터 | [tensorflow/models/research/deeplab](https://github.com/tensorflow/models/tree/master/research/deeplab) |
| 태그 | `segmentation`, `decoder` |
| 정리 | 논문 초안 참고문헌 [15]에 대응하는 팀 리뷰 초안 |
| 정리 날짜 | 2026-11-02 |

## 한 줄 요약

넓은 문맥을 얻는 atrous convolution과 세부 경계를 복원하는 디코더를 결합한 DeepLabv3+ 의미 분할 모델이다.

## 다루는 문제

깊은 특징은 물체의 의미를 잘 담지만 공간 해상도가 낮아 경계가 흐려질 수 있다. 입력의 여러 크기 구조를 이해하면서 경계를 복원해야 한다.

## 핵심 아이디어 / 방법

Atrous convolution은 필터 사이 간격을 두어 특징 해상도를 과도하게 줄이지 않고 수용 영역을 넓힌다. ASPP는 여러 간격의 필터 등으로 문맥을 모은다. DeepLabv3+는 이를 저수준 특징과 결합하는 디코더로 보완하며, atrous separable convolution을 활용한다.

| 입력 | 주요 처리 | 출력 |
| --- | --- | --- |
| RGB 이미지 | CNN 특징 → ASPP → 저수준 특징과 융합 → 업샘플 | 픽셀별 클래스 점수·의미 마스크 |

한 이미지에 벽·바닥·천장 등 여러 클래스를 출력할 수 있지만, 어떤 상태를 구분할지는 학습 라벨과 헤드가 결정한다.

## 실험 및 결과

원 논문은 PASCAL VOC 2012와 Cityscapes 등의 의미 분할 과제에서 평가하고 인코더·디코더 구성의 영향을 분석한다. 일반 장면 분할 성능이 시공 상태 식별 성능을 의미하지는 않는다. [공개 원문](https://arxiv.org/html/1802.02611v3)

## 우리 프로젝트에 적용할 점

아래는 **논문 내용을 바탕으로 한 프로젝트 적용 해석이며, 원 논문에서 우리 현장 데이터로 검증한 결과가 아니다.**

1. 문맥 수집과 경계 복원을 결합하는 대표적 분할 방법으로 관련 연구에 둔다.
2. 설치 비율 계산에 경계 오류가 왜 영향을 주는지 설명할 때 참고한다.
3. 현재 선택은 DINOv2 기반 디코더 비교이므로, 이 리뷰는 기존 DeepLab 학습 코드를 복구하거나 새 필수 비교군을 추가한다는 뜻이 아니다.
4. 실제 비교를 수행하지 않았다면 DeepLabv3+보다 우수하다고 쓰지 않는다.

## 한계 및 주의점

- DeepLabv3와 DeepLabv3+는 구분해야 한다. 이 논문은 디코더를 추가한 v3+다.
- 일반 의미 분할은 같은 클래스의 여러 벽을 영구 ID로 구분하지 않는다.
- 픽셀 마스크에서 공정률로 가려면 ROI, 상태 정의, 가림 처리와 분모 정의가 추가로 필요하다.

## 원문 및 공유 정보

[원문 안내](https://arxiv.org/abs/1802.02611)에서 자료를 확인할 수 있다. 

PDF와 현장 원본 자료는 이 리뷰에 첨부하지 않았다. 코드 공개 여부와 논문·가중치·데이터의 이용 조건은 별개이며, 실제 배포에는 사용하는 버전의 조건을 확인한다.

## 인용 (BibTeX)

```bibtex
@inproceedings{chen2018encoder,
  title={Encoder-Decoder with Atrous Separable Convolution for Semantic Image Segmentation},
  author={Chen, Liang-Chieh and Zhu, Yukun and Papandreou, George and Schroff, Florian and Adam, Hartwig},
  booktitle={European Conference on Computer Vision}, pages={833--851}, year={2018},
  url={https://arxiv.org/abs/1802.02611}
}
```

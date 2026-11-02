# DINOv2: Learning Robust Visual Features without Supervision

| 항목 | 내용 |
| --- | --- |
| 저자 | Maxime Oquab 외 공동저자 |
| 발표 연도 / 학회·저널 | 2024 / Transactions on Machine Learning Research. arXiv 최초 공개 2023년 |
| 링크 / DOI | [원문 안내](https://arxiv.org/abs/2304.07193) |
| 공식 코드·데이터 | [facebookresearch/dinov2](https://github.com/facebookresearch/dinov2): 공식 코드·사전학습 가중치 |
| 태그 | `segmentation`, `self-supervised-learning` |
| 정리 | 논문 초안 참고문헌 [4]에 대응하는 팀 리뷰 초안 |
| 정리 날짜 | 2026-11-02 |

## 한 줄 요약

정답 라벨 없이 대규모 이미지에서 학습한 ViT 특징을 제공하며, 특징 추출기를 고정하고 작은 작업별 모델만 학습하는 기반으로 사용할 수 있다.

## 다루는 문제

현장별 라벨이 적을 때 모든 시각 표현을 처음부터 학습하기 어렵다. 특정 분류 라벨에만 맞춰지지 않은 범용 특징이 필요하다.

## 핵심 아이디어 / 방법

자기지도 사전학습에서는 서로 다른 이미지 뷰를 보는 학생과 교사 네트워크의 표현을 맞춘다. 교사는 학생 가중치의 이동 평균으로 갱신된다. 이미지 수준 DINO 목적과 가려진 패치의 표현을 맞추는 iBOT 목적 등을 함께 사용한다. 큰 교사 모델의 지식을 작은 모델로 옮기는 과정도 포함한다.

| 구분 | 내용 |
| --- | --- |
| 사전학습 입력 | 라벨 없는 이미지의 여러 crop·가려진 패치 |
| 우리 사용 시 입력 | 정규화한 현장 RGB 이미지 |
| 기본 출력 | 전역 표현과 패치별 특징 토큰 |
| 추가로 필요한 부분 | 특징을 상태별 픽셀 점수로 변환할 디코더·출력 헤드 |

DINOv2 자체는 벽 설치율이나 대상 ID를 출력하지 않는다.

## 실험 및 결과

원 논문은 이미지 분류뿐 아니라 의미 분할·깊이 추정 등에서 고정 특징의 전이 성능을 평가한다. 모델 크기와 평가 헤드에 따라 조건이 다르므로, 원 논문의 최고 mIoU를 우리 소형 인코더+UPerNet 구성의 기대 성능으로 옮기지 않는다. [원문](https://arxiv.org/html/2304.07193v2)

## 우리 프로젝트에 적용할 점

아래는 **논문 내용을 바탕으로 한 프로젝트 적용 해석이며, 원 논문에서 우리 현장 데이터로 검증한 결과가 아니다.**

1. 사전학습 DINOv2 인코더를 고정하고 상태 분할 디코더·출력 헤드를 학습한다.
2. 동일 인코더 위에 Linear·MLP·UPerNet·DPT를 붙여 디코더 효과를 분리한다.
3. 패치 크기, 선택한 중간 블록, 입력 해상도, register 사용 여부를 실험 설정에 기록한다.
4. DINOv2 사전학습의 자기증류와 우리가 별도로 제안했던 CLIP 지식 증류는 다른 과정이다.

## 한계 및 주의점

- 이름이 비슷한 **Grounding DINO**와 다르다. DINOv2는 텍스트로 물체를 찾는 검출기가 아니다.
- 고정 특징이 새 현장의 모든 재료·조명·가림을 해결하는 것은 아니다.
- 작은 구조가 패치 해상도에서 소실될 수 있으므로 디코더 평가와 입력 해상도 평가가 필요하다.

## 원문 및 공유 정보

[원문 안내](https://arxiv.org/abs/2304.07193)에서 자료를 확인할 수 있다. 방법 설명은 [arXiv v2](https://arxiv.org/html/2304.07193v2), 실제 체크포인트 구성은 [공식 저장소](https://github.com/facebookresearch/dinov2)를 기준으로 확인했다.

PDF와 현장 원본 자료는 이 리뷰에 첨부하지 않았다. 코드 공개 여부와 논문·가중치·데이터의 이용 조건은 별개이며, 실제 배포에는 사용하는 버전의 조건을 확인한다.

## 인용 (BibTeX)

```bibtex
@article{oquab2024dinov2,
  title={{DINOv2}: Learning Robust Visual Features without Supervision},
  author={Oquab, Maxime and Darcet, Timothée and Moutakanni, Théo and others},
  journal={Transactions on Machine Learning Research}, year={2024},
  url={https://arxiv.org/abs/2304.07193}
}
```

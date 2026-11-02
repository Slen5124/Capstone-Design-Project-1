# Vision Transformers for Dense Prediction

| 항목 | 내용 |
| --- | --- |
| 저자 | René Ranftl, Alexey Bochkovskiy, Vladlen Koltun |
| 발표 연도 / 학회·저널 | 2021 / ICCV, 12179–12188 |
| 링크 / DOI | [원문 안내](https://arxiv.org/abs/2103.13413) |
| 공식 코드·데이터 | [isl-org/DPT](https://github.com/isl-org/DPT): 공식 구현·가중치 안내 |
| 태그 | `segmentation`, `decoder` |
| 정리 | 논문 초안 참고문헌 [8]에 대응하는 팀 리뷰 초안 |
| 정리 날짜 | 2026-11-02 |

## 한 줄 요약

ViT의 토큰을 여러 해상도의 공간 특징으로 다시 구성하고 융합해, 깊이 지도나 의미 분할처럼 픽셀마다 값을 예측하는 DPT를 제안한다.

## 다루는 문제

Transformer가 만든 토큰은 이미지 전체 관계를 담지만, 그대로는 조밀한 픽셀 출력을 만들기 어렵다. 여러 블록의 특징을 공간적 예측으로 변환할 구조가 필요하다.

## 핵심 아이디어 / 방법

DPT(Dense Prediction Transformer)는 중간 블록의 토큰을 선택해 reassemble 과정을 거친다. 전역 readout 토큰을 처리하고 이미지 패치 토큰을 2D로 배치한 뒤 여러 해상도로 리샘플링한다. 이후 convolution 기반 fusion block에서 큰 문맥과 세밀한 정보를 결합한다.

| 단계 | 입력 → 출력 |
| --- | --- |
| ViT 인코더 | RGB 이미지 → 여러 블록의 토큰 |
| Reassemble | 토큰 → 공간 특징 지도·해상도 피라미드 |
| Fusion·헤드 | 피라미드 특징 → 깊이 값 또는 클래스별 점수 |

깊이 추정과 의미 분할은 서로 다른 출력 헤드를 쓰는 과제다. 하나의 DPT가 무조건 둘을 동시에 출력하는 것은 아니다.

## 실험 및 결과

원 논문은 단안 깊이 추정과 의미 분할에서 DPT를 평가하며 전역적 표현과 고해상도 복원의 효과를 보고한다. 깊이 오차와 분할 mIoU를 같은 지표로 취급하지 않는다. [공개 원문](https://arxiv.org/html/2103.13413v1)

## 우리 프로젝트에 적용할 점

아래는 **논문 내용을 바탕으로 한 프로젝트 적용 해석이며, 원 논문에서 우리 현장 데이터로 검증한 결과가 아니다.**

1. DINOv2의 선택된 중간 특징에 DPT 형태의 reassemble·fusion 디코더를 연결한다.
2. 우리 구현의 채널 수, 정규화 층, readout 처리, 출력 해상도가 원 구현과 어떻게 다른지 적는다.
3. 동일 특징을 쓰는 UPerNet·MLP·Linear와 상태 마스크 및 설치 비율 오차를 비교한다.
4. 혼합 정밀도 사용 시 비정상 손실·gradient를 기록하고, 정밀도 변경은 비교 조건에 포함한다.

## 한계 및 주의점

- 2021년 DPT는 DINOv2를 사용한 논문이 아니다. 우리 조합은 원 구조의 응용이다.
- “Dense prediction”은 조밀한 예측이라는 뜻이며 3D 복원이나 시공률 직접 예측을 뜻하지 않는다.
- 깊이 모델의 가중치를 가져온다고 상태 라벨 예측이 되는 것은 아니다. 출력 헤드와 학습 데이터가 필요하다.

## 원문 및 공유 정보

[원문 안내](https://arxiv.org/abs/2103.13413)에서 자료를 확인할 수 있다. 

PDF와 현장 원본 자료는 이 리뷰에 첨부하지 않았다. 코드 공개 여부와 논문·가중치·데이터의 이용 조건은 별개이며, 실제 배포에는 사용하는 버전의 조건을 확인한다.

## 인용 (BibTeX)

```bibtex
@inproceedings{ranftl2021dense,
  title={Vision Transformers for Dense Prediction},
  author={Ranftl, René and Bochkovskiy, Alexey and Koltun, Vladlen},
  booktitle={Proceedings of the IEEE/CVF International Conference on Computer Vision},
  pages={12179--12188}, year={2021}, url={https://arxiv.org/abs/2103.13413}
}
```

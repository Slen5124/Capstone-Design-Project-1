# Integrated Vision-based Automated Progress Monitoring of Indoor Construction Using Mask Region-based Convolutional Neural Networks and BIM

| 항목 | 내용 |
| --- | --- |
| 저자 | Wei Wei, Yujie Lu, Tao Zhong, Peixian Li, Bo Liu |
| 발표 연도 / 학회·저널 | 2022 / Automation in Construction, 140, 104327 |
| 링크 / DOI | [원문 안내](https://doi.org/10.1016/j.autcon.2022.104327) |
| 공식 코드·데이터 | 이번 확인 범위에서 공식 코드·학습 데이터 배포 링크를 확인하지 못함 |
| 태그 | `construction-progress`, `segmentation` |
| 정리 | 논문 초안 참고문헌 [13]에 대응하는 팀 리뷰 초안 |
| 정리 날짜 | 2026-11-02 |

## 한 줄 요약

실내 사진에서 Mask R-CNN으로 시공 영역을 분할하고 BIM과 연결해, 벽 미장 작업의 진행 상황을 관리하는 통합 시스템이다.

## 다루는 문제

현장 사진에서 시공된 표면을 구분하더라도, 그 결과가 어느 벽·층·작업에 해당하는지 연결되지 않으면 공정 관리에 활용하기 어렵다.

## 핵심 아이디어 / 방법

시공 상태를 인식하는 영역 분할과 BIM 연결을 결합한다. 논문은 실내 미장 작업을 대상으로 하며, 촬영·위치 정보와 BIM을 통해 인식 결과를 공간적 진행 정보에 연결한다.

| 단계 | 역할 |
| --- | --- |
| 영상·위치 수집 | 실제 현장 이미지와 촬영 위치 관련 정보 확보 |
| Mask R-CNN | 학습한 시공 대상·영역의 인스턴스 마스크 추정 |
| BIM 연결·진행 관리 | 인식 결과를 계획 요소·작업에 연결하여 진행 상황 표현 |

Mask R-CNN은 박스만 예측하는 Faster R-CNN과 달리 인스턴스별 마스크를 출력한다. 진행률 계산은 이 마스크와 대상·계획 정보를 사용하는 별도 단계다.

## 실험 및 결과

공개 초록은 사례 연구에서 **mAP 96.8%**를 보고한다. 이는 검출/분할 모델의 평가 수치이며 “공정률이 96.8% 정확하다”거나 “설치율 MAE가 3.2%p다”라는 뜻이 아니다. 사용한 IoU 기준과 세부 집계 방식은 전체 본문에서 추가 확인해야 한다. [출판사 초록](https://www.sciencedirect.com/science/article/pii/S092658052200200X)

## 우리 프로젝트에 적용할 점

아래는 **논문 내용을 바탕으로 한 프로젝트 적용 해석이며, 원 논문에서 우리 현장 데이터로 검증한 결과가 아니다.**

1. 상태 마스크와 대상 ID·ROI를 연결한 뒤 비율을 계산하는 구조의 선행 연구로 활용한다.
2. 우리 모델이 의미 분할이라면, 개별 벽의 식별은 ROI/대상 메타데이터에서 담당한다고 명시한다.
3. 분할 성능(mIoU 등)과 최종 비율 오차(MAE, %p)를 별도로 보고한다.
4. BIM 없는 구성에서는 물리적 면적·층 전체 진척을 계산했다고 주장하지 않는다.

## 한계 및 주의점

- 미장 작업에 대한 사례를 모든 벽·천장·바닥 작업의 검증으로 확대하지 않는다.
- 카메라·위치 추정 및 BIM 연결 조건이 우리 고정 카메라 방식과 다르다.
- mAP 비교에는 같은 데이터·라벨·IoU 기준이 필요하다. 우리 MAE와 직접 순위를 비교할 수 없다.

## 원문 및 공유 정보

[원문 안내](https://doi.org/10.1016/j.autcon.2022.104327)에서 자료를 확인할 수 있다. 

PDF와 현장 원본 자료는 이 리뷰에 첨부하지 않았다. 코드 공개 여부와 논문·가중치·데이터의 이용 조건은 별개이며, 실제 배포에는 사용하는 버전의 조건을 확인한다.

## 인용 (BibTeX)

```bibtex
@article{wei2022integrated,
  title={Integrated vision-based automated progress monitoring of indoor construction using mask region-based convolutional neural networks and {BIM}},
  author={Wei, Wei and Lu, Yujie and Zhong, Tao and Li, Peixian and Liu, Bo},
  journal={Automation in Construction}, volume={140}, pages={104327}, year={2022},
  doi={10.1016/j.autcon.2022.104327}
}
```

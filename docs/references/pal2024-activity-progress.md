# Activity-level Construction Progress Monitoring through Semantic Segmentation of 3D-informed Orthographic Images

| 항목 | 내용 |
| --- | --- |
| 저자 | Aritra Pal, Jacob J. Lin, Shang-Hsien Hsieh, Mani Golparvar-Fard |
| 발표 연도 / 학회·저널 | 2024 / Automation in Construction, 157, 105157. DOI에 포함된 2023은 저널 발행 연도와 다름 |
| 링크 / DOI | [원문 안내](https://doi.org/10.1016/j.autcon.2023.105157) |
| 공식 코드·데이터 | 이번 확인 범위에서 전체 시스템을 재현하는 저자 공식 코드·데이터 배포를 확인하지 못함 |
| 태그 | `construction-progress`, `segmentation`, `3d-reconstruction` |
| 정리 | 논문 초안 참고문헌 [16]에 대응하는 팀 리뷰 초안 |
| 정리 날짜 | 2026-11-02 |

## 한 줄 요약

현장 사진을 3D·BIM 정보로 정면에 가까운 정사영상으로 만들고, 작업별 표면 영역을 분할해 요소별 진행 비율을 추정한다.

## 다루는 문제

비스듬한 사진의 픽셀 면적은 실제 표면 면적과 다르다. 또한 요소가 존재하는지 여부만으로 그 표면의 세부 작업이 얼마나 끝났는지 알기 어렵다.

## 핵심 아이디어 / 방법

사진 기반 3D 복원과 4D BIM을 연결하고, 요소의 존재·점유 상태를 확인한다. 작업 표면은 투영 변환 또는 NeRF 기반 표현을 이용해 정사영상으로 구성한 뒤, 분할 결과를 요소 표면과 연결해 작업 수준 진행을 계산한다. 공개 방법 설명에는 Mask R-CNN 기반 분할이 포함된다.

| 입력 | 중간 처리 | 출력 |
| --- | --- | --- |
| 여러 현장 사진 + 4D BIM | SfM/MVS·공간 정합 → 요소 표면 정사영상 → 작업 영역 분할 | 요소별 작업 비율과 3D/BIM 시각화 |

“사진에서 설치 픽셀을 세기”와 달리, 영역을 측정할 좌표계·표면을 구성하는 단계가 중요하다.

## 실험 및 결과

공개 초록은 제안 시스템이 **평균 절대 오차 6% 미만**의 결과를 얻었다고 보고한다. 도입부는 두 프로젝트의 네 사례, 약 500개 BIM 요소를 설명한다. 평가 단위·정규화·집계 조건을 전체 본문에서 대조하기 전에는 우리 날짜×벽 MAE와 같은 조건의 숫자로 취급하지 않는다. [출판사 초록·미리보기](https://www.sciencedirect.com/science/article/pii/S092658052300417X)

## 우리 프로젝트에 적용할 점

아래는 **논문 내용을 바탕으로 한 프로젝트 적용 해석이며, 원 논문에서 우리 현장 데이터로 검증한 결과가 아니다.**

1. 상태 분할 마스크를 작업량 비율로 연결하는 논리의 핵심 선행 연구로 활용한다.
2. 우리 방식은 BIM·정사영상 없이 고정 구도의 검수 ROI에서 계산하므로, 기본 산출물을 **정의된 관측 영역의 설치 비율**로 제한한다.
3. 향후 실제 면적 비율이 필요하면 평면 보정·캘리브레이션·BIM 또는 3D 정보를 추가한다.
4. 방법 비교 표에는 데이터·기하 정보·라벨·평가 단위를 함께 적고, 원 논문 숫자보다 좋다는 주장에는 공통 평가 조건을 요구한다.

## 한계 및 주의점

- 원 논문의 정사영상 생성과 3D/BIM 연결을 빼면 동일 방법의 재현이라고 할 수 없다.
- NeRF와 Gaussian Splatting은 다른 기법이다. 이 연구를 Gaussian Splatting 사용 사례로 소개하지 않는다.
- 일부 관측 벽의 설치 비율을 매장 전체 공정률로 합산하려면 작업 물량·가중치·미관측 영역 처리 기준이 추가로 필요하다.

## 원문 및 공유 정보

[원문 안내](https://doi.org/10.1016/j.autcon.2023.105157)에서 자료를 확인할 수 있다. [Illinois Experts](https://experts.illinois.edu/en/publications/activity-level-construction-progress-monitoring-through-semantic-/)에서도 초록·서지 정보를 확인할 수 있다.

PDF와 현장 원본 자료는 이 리뷰에 첨부하지 않았다. 코드 공개 여부와 논문·가중치·데이터의 이용 조건은 별개이며, 실제 배포에는 사용하는 버전의 조건을 확인한다.

## 인용 (BibTeX)

```bibtex
@article{pal2024activity,
  title={Activity-level construction progress monitoring through semantic segmentation of {3D}-informed orthographic images},
  author={Pal, Aritra and Lin, Jacob J. and Hsieh, Shang-Hsien and Golparvar-Fard, Mani},
  journal={Automation in Construction}, volume={157}, pages={105157}, year={2024},
  doi={10.1016/j.autcon.2023.105157}
}
```

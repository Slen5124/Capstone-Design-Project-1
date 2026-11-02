# Appearance-based Material Classification for Monitoring of Operation-level Construction Progress Using 4D BIM and Site Photologs

| 항목 | 내용 |
| --- | --- |
| 저자 | Kevin K. Han, Mani Golparvar-Fard |
| 발표 연도 / 학회·저널 | 2015 / Automation in Construction, 53, 44–57 |
| 링크 / DOI | [원문 안내](https://doi.org/10.1016/j.autcon.2015.02.007) |
| 공식 코드·데이터 | 논문은 Construction Material Library(CML) 확장과 현장 데이터셋을 소개함. 현재 이용 가능한 공식 코드·데이터 다운로드는 확인하지 못함 |
| 태그 | `construction-progress`, `material-recognition` |
| 정리 | 논문 초안 참고문헌 [2]에 대응하는 팀 리뷰 초안 |
| 정리 날짜 | 2026-11-02 |

## 한 줄 요약

BIM 요소가 사진에서 차지하는 영역을 찾고, 그 영역의 재료 외관을 분류해 작업 상태를 추론한다.

## 다루는 문제

벽이 존재한다는 사실만으로 보드 설치, 미장, 도장 등의 작업 상태를 구분할 수 없다. 같은 건물 요소라도 표면 재료가 달라지므로 외관을 작업 단계와 연결해야 한다.

## 핵심 아이디어 / 방법

현장 사진에서 SfM으로 만든 점군과 4D BIM의 대응점을 사람이 초기 지정한다. 이를 통해 BIM 요소를 해당 요소가 보이는 사진에 투영한다. 가림을 고려해 요소별 이미지 패치를 모으고, 색·질감 필터 응답을 양자화한 특징으로 재료를 분류한다. 요소에서 관찰된 재료의 빈도로 대표 재료와 진행 상태를 추론한다.

| 구분 | 내용 |
| --- | --- |
| 입력 | 현장 사진, SfM 점군, 4D BIM, 초기 대응점 |
| 인식 단위 | BIM 요소에 대응하는 이미지 패치 |
| 출력 | 패치의 재료 종류, 요소별 관측 재료·진행 상태 |

현대적인 픽셀 분할 네트워크를 쓰는 논문은 아니지만, **대상을 찾는 단계와 상태를 해석하는 단계**를 나누는 근거가 된다.

## 실험 및 결과

공개 초록은 100×100 크기의 CML 패치에서 평균 재료 분류 정확도 **92.4%**, 네 현장 데이터셋의 실험에서 **95.9% 정확도**를 보고한다. 이는 설치 비율의 MAE나 전체 공정률 정확도가 아니다. 후자의 평가 단위·집계 방식은 본문을 추가 확인해야 한다. [저자 소속기관 초록](https://experts.illinois.edu/en/publications/appearance-based-material-classification-for-monitoring-of-operat/)

## 우리 프로젝트에 적용할 점

아래는 **논문 내용을 바탕으로 한 프로젝트 적용 해석이며, 원 논문에서 우리 현장 데이터로 검증한 결과가 아니다.**

1. 벽·천장이라는 대상 분류와 설치·미설치·가림 같은 상태 분류를 구분한다.
2. 대상 ROI 내부에서 상태를 판단하고, 설치 상태 픽셀을 정의된 분모로 나눠 관측 비율을 계산한다.
3. 상태 라벨은 실제로 사진에서 구분 가능한 재료·외관에 근거해야 한다. 보이지 않는 내부 공정을 사진만으로 완료 처리하지 않는다.

## 한계 및 주의점

- BIM·사진 정합과 재료에 대한 사전 지식이 필요한 연구다. 우리 데이터에 그대로 적용되는 무설정 모델은 아니다.
- 같은 재료가 여러 작업 단계에 등장하거나 보호막이 표면을 가리면 상태 해석이 모호해진다.
- 우리 픽셀 분할의 mIoU 및 비율 MAE와 위 분류 정확도를 같은 표에서 직접 순위 비교하면 안 된다.

## 원문 및 공유 정보

[원문 안내](https://doi.org/10.1016/j.autcon.2015.02.007)에서 자료를 확인할 수 있다. [Illinois Experts](https://experts.illinois.edu/en/publications/appearance-based-material-classification-for-monitoring-of-operat/)에 초록과 서지 정보가 공개되어 있다.

PDF와 현장 원본 자료는 이 리뷰에 첨부하지 않았다. 코드 공개 여부와 논문·가중치·데이터의 이용 조건은 별개이며, 실제 배포에는 사용하는 버전의 조건을 확인한다.

## 인용 (BibTeX)

```bibtex
@article{han2015appearance,
  title={Appearance-based material classification for monitoring of operation-level construction progress using {4D BIM} and site photologs},
  author={Han, Kevin K. and Golparvar-Fard, Mani},
  journal={Automation in Construction}, volume={53}, pages={44--57}, year={2015},
  doi={10.1016/j.autcon.2015.02.007}
}
```

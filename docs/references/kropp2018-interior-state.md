# Interior Construction State Recognition with 4D BIM Registered Image Sequences

| 항목 | 내용 |
| --- | --- |
| 저자 | Christopher Kropp, Christian Koch, Markus König |
| 발표 연도 / 학회·저널 | 2018 / Automation in Construction, 86, 11–32 |
| 링크 / DOI | [원문 안내](https://doi.org/10.1016/j.autcon.2017.10.027) |
| 공식 코드·데이터 | 공식 전체 파이프라인 코드는 확인하지 못함. [저자 공개 영상 데이터셋](https://data.mendeley.com/datasets/rskgn5f8y8/1), DOI:10.17632/rskgn5f8y8.1 |
| 태그 | `construction-progress`, `bim-registration` |
| 정리 | 논문 초안 참고문헌 [12]에 대응하는 팀 리뷰 초안 |
| 정리 날짜 | 2026-11-02 |

## 한 줄 요약

실내 영상을 4D BIM에 정합해 사진 속 설계 요소를 특정하고, 해당 요소의 시각적 상태로 작업 진행을 인식한다.

## 다루는 문제

실내 사진에서 벽이 보인다는 사실만으로는 도면상의 어느 벽인지 알 수 없다. 정확한 작업 상태 비교에는 사진과 계획 요소의 대응이 먼저 필요하다.

## 핵심 아이디어 / 방법

이미지 시퀀스의 카메라 위치·자세를 BIM 좌표에 연결하고, 계획 요소가 영상에 투영되는 영역을 사용해 상태를 인식한다. 대상 위치 연결과 외관 상태 판단을 이어 붙이는 구조다.

| 입력 | 중간 단계 | 출력 |
| --- | --- | --- |
| 실내 영상 + 4D BIM | 프레임 정합 → 설계 요소의 영상 영역 결정 → 상태 인식 | 요소·작업에 연결된 관측 상태 |

공개 데이터 설명에는 태블릿으로 촬영한 세 영상과 라디에이터·석고보드 벽의 서로 다른 상태가 제시되어 있다.

## 실험 및 결과

논문은 실내 작업 상태를 대상으로 방법을 평가한다. 함께 공개된 데이터는 실제 개보수 현장의 라디에이터 및 벽면 상태를 포함한다. 공개 미리보기만으로 전체 성능 표를 대조하지 못했으므로 정확도 수치는 옮기지 않았다. [출판사 미리보기](https://www.sciencedirect.com/science/article/pii/S0926580517304405) · [데이터 설명](https://data.mendeley.com/datasets/rskgn5f8y8/1)

## 우리 프로젝트에 적용할 점

아래는 **논문 내용을 바탕으로 한 프로젝트 적용 해석이며, 원 논문에서 우리 현장 데이터로 검증한 결과가 아니다.**

1. 우리 방식에서는 초기 구도·벽/천장 ID·ROI를 사람이 확정해 사진과 대상의 대응을 만든다.
2. 그 뒤 분할 모델이 ROI 안의 상태를 예측하도록 역할을 분리한다.
3. 카메라가 이동하면 기존 ROI를 무조건 재사용하지 않고 구도 변경·재정합 검토로 보낸다.
4. 공개 데이터는 구조 이해와 소규모 시험 후보가 될 수 있지만, 우리 현장의 상태 라벨과 호환되는지 먼저 확인한다.

## 한계 및 주의점

- BIM 정합 연구의 성능을 BIM이 없는 고정 카메라 구성의 성능으로 가져올 수 없다.
- 하나의 현장 또는 제한된 작업 사례가 모든 천장·바닥 공정의 식별 가능성을 뜻하지 않는다.
- 데이터가 공개되어 있어도 검수된 우리 라벨 정의와 촬영 조건에 맞춰 재구성해야 한다.

## 원문 및 공유 정보

[원문 안내](https://doi.org/10.1016/j.autcon.2017.10.027)에서 자료를 확인할 수 있다. [Mendeley Data v1](https://data.mendeley.com/datasets/rskgn5f8y8/1)의 설명과 라이선스(CC BY 4.0)를 확인했다. 데이터는 이 묶음에 복제하지 않았다.

PDF와 현장 원본 자료는 이 리뷰에 첨부하지 않았다. 코드 공개 여부와 논문·가중치·데이터의 이용 조건은 별개이며, 실제 배포에는 사용하는 버전의 조건을 확인한다.

## 인용 (BibTeX)

```bibtex
@article{kropp2018interior,
  title={Interior construction state recognition with {4D BIM} registered image sequences},
  author={Kropp, Christopher and Koch, Christian and König, Markus},
  journal={Automation in Construction}, volume={86}, pages={11--32}, year={2018},
  doi={10.1016/j.autcon.2017.10.027}
}
```

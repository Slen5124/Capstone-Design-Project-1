# 기존 references/README.md에 추가할 내용

아래는 복사할 초안이다. 저장소의 README.md는 수정하지 않았다. `reviews/` 안의 18개 파일을 `docs/references/`에 복사한 뒤, 아래 행을 기존 인덱스 표에 추가하면 상대 링크가 연결된다.

## 인덱스 추가 행

| 자료 | 저자/연도 | 태그 | 한 줄 요약 | 노트 |
| --- | --- | --- | --- | --- |
| [D4AR](https://www.itcon.org/paper/2009/13) | Golparvar-Fard et al., 2009 | `construction-progress`, `3d-reconstruction` | 사진 기반 실제 복원과 4D 계획의 정합·진척 시각화 | [리뷰](golparvarfard2009-d4ar.md) |
| [Material-based Progress](https://doi.org/10.1016/j.autcon.2015.02.007) | Han & Golparvar-Fard, 2015 | `construction-progress`, `material-recognition` | BIM 요소의 관측 재료로 작업 상태 추론 | [리뷰](han2015-material-progress.md) |
| [DomainBed](https://arxiv.org/abs/2007.01434) | Gulrajani & Lopez-Paz, 2021 | `domain-generalization`, `evaluation` | 새 도메인 평가에서 기준선·모델 선택 절차의 중요성 | [리뷰](gulrajani2021-domainbed.md) |
| [DINOv2](https://arxiv.org/abs/2304.07193) | Oquab et al., 2024 | `segmentation`, `self-supervised-learning` | 라벨 없이 학습한 범용 ViT 특징 추출기 | [리뷰](oquab2024-dinov2.md) |
| [Registers](https://arxiv.org/abs/2309.16588) | Darcet et al., 2024 | `segmentation`, `self-supervised-learning` | 별도 계산 토큰으로 ViT 공간 특징의 이상 완화 | [리뷰](darcet2024-registers.md) |
| [UPerNet](https://arxiv.org/abs/1807.10221) | Xiao et al., 2018 | `segmentation`, `decoder` | PPM·FPN 기반의 다중 범위 특징 융합 | [리뷰](xiao2018-upernet.md) |
| [SegFormer](https://arxiv.org/abs/2105.15203) | Xie et al., 2021 | `segmentation`, `decoder` | 계층적 Transformer와 가벼운 MLP 분할 디코더 | [리뷰](xie2021-segformer.md) |
| [DPT](https://arxiv.org/abs/2103.13413) | Ranftl et al., 2021 | `segmentation`, `decoder` | ViT 토큰의 공간 재구성과 다중 해상도 융합 | [리뷰](ranftl2021-dpt.md) |
| [Isotonic Regression](https://books.google.com/books?id=DEamySUDBWcC) | Barlow et al., 1972 | `statistics`, `temporal-calibration` | 순서 제약을 둔 통계 추정의 이론적 배경 | [리뷰](barlow1972-isotonic.md) |
| [SAM 3](https://arxiv.org/abs/2511.16719) | Carion et al., 2025 | `segmentation`, `label-propagation` | 개념 기반 분할과 과거 라벨 제작 출처 | [리뷰](carion2025-sam3.md) |
| [Bootstrap](https://doi.org/10.1201/9780429246593) | Efron & Tibshirani, 1993 | `statistics`, `evaluation` | 재표집으로 오차 차이의 불확실성 추정 | [리뷰](efron1993-bootstrap.md) |
| [BIM-registered Interior State](https://doi.org/10.1016/j.autcon.2017.10.027) | Kropp et al., 2018 | `construction-progress`, `bim-registration` | 실내 영상을 BIM 요소에 연결한 상태 인식 | [리뷰](kropp2018-interior-state.md) |
| [Mask R-CNN + BIM](https://doi.org/10.1016/j.autcon.2022.104327) | Wei et al., 2022 | `construction-progress`, `segmentation` | 시공 영역 분할과 BIM을 연결한 실내 미장 모니터링 | [리뷰](wei2022-indoor-progress.md) |
| [WILDS](https://proceedings.mlr.press/v139/koh21a.html) | Koh et al., 2021 | `domain-generalization`, `evaluation` | 실제 분포 이동을 반영하는 평가 체계 | [리뷰](koh2021-wilds.md) |
| [DeepLabv3+](https://arxiv.org/abs/1802.02611) | Chen et al., 2018 | `segmentation`, `decoder` | Atrous 문맥과 경계 복원 디코더를 결합한 의미 분할 | [리뷰](chen2018-deeplabv3plus.md) |
| [3D-informed Activity Progress](https://doi.org/10.1016/j.autcon.2023.105157) | Pal et al., 2024 | `construction-progress`, `segmentation`, `3d-reconstruction` | 정사영상의 작업 영역 분할을 통한 요소별 비율 추정 | [리뷰](pal2024-activity-progress.md) |
| [AdamW](https://arxiv.org/abs/1711.05101) | Loshchilov & Hutter, 2019 | `optimization` | 적응적 gradient와 가중치 감쇠를 분리한 optimizer | [리뷰](loshchilov2019-adamw.md) |
| [Super-Convergence](https://arxiv.org/abs/1708.07120) | Smith & Topin, 2017 | `optimization` | 큰 학습률과 한 번의 주기를 이용하는 학습 정책 | [리뷰](smith2017-super-convergence.md) |

## 필요하면 주제 분류에 추가할 태그

| 태그 | 설명 |
| --- | --- |
| `domain-generalization` | 새 현장 일반화·분포 이동 |
| `evaluation` | 데이터 분할·비교·불확실성 평가 |
| `self-supervised-learning` | 자기지도 사전학습·범용 특징 |
| `decoder` | 특징의 공간 복원·픽셀 예측 |
| `statistics` | 통계 추정·재표집 |
| `temporal-calibration` | 시간축 제약·관측 보정 |
| `optimization` | optimizer·학습률 정책 |
| `3d-reconstruction` | 사진 기반 공간 복원·정사영상 |
| `bim-registration` | 영상·설계 좌표 정합 |
| `material-recognition` | 재료 외관 기반 상태 인식 |

## 기존 인덱스에서 별도로 확인할 부분

현재 확인한 README.md의 HELMET·Woodpecker 행에는 저장소에서 제거한 PDF의 상대 링크가 남아 있다. 이번에는 수정하지 않았다. 공식 arXiv/학회 링크와 리뷰 링크를 남기고, 없는 PDF 링크는 직접 정리하면 된다.

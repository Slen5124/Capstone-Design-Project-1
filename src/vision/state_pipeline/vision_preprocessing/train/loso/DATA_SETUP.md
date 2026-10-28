# LOSO 실험 실행 안내 (팀원용, Linux / Windows 공통)

실험 규격은 `PROTOCOL.md`, 설정값은 `protocol.json`에 고정돼 있다. 실행하는 사람은 **데이터 경로만** 적는다.

## 0. 요약

```bash
git clone <repo> && cd <repo> && git checkout <작업 브랜치>
conda env create -f src/vision/state_pipeline/vision_preprocessing/train/loso/environment.yml
conda activate loso
cd src/vision/state_pipeline                    # vision_preprocessing 폴더가 있는 곳 (이후 모든 명령은 여기서)
# 개인 전달 zip을 풀고 local.json 작성 (2번)
python -m vision_preprocessing.train.loso check --local /path/to/local.json
tmux new -s loso                                # Linux: 접속이 끊겨도 계속 돌게
python -m vision_preprocessing.train.loso run   --local /path/to/local.json --models mlp,dpt
python -m vision_preprocessing.train.loso pack  --local /path/to/local.json --out results_mlp_dpt.zip
```

## 1. 준비물

| 항목 | 조건 |
|---|---|
| GPU | NVIDIA 8GB 이상 (CUDA 12.4를 지원하는 드라이버) |
| 디스크 | 데이터(export 5개) + 결과 여유 5GB 이상. 결과는 실행당 약 20MB(가중치 16MB 포함) |
| 데이터 | 현장 4곳의 export 폴더 5개 (아래 표). 이미 가지고 있는 export와 **같은 버전**이어야 한다 → `check`가 확인 |
| 개인 전달 zip | 분할 목록·제외/예외 목록·가명 대응표(`README_private.md`)·`local.example.json`. **저장소에 올리지 않는다** |
| 인터넷 | 첫 실행 때 인코더 가중치 자동 다운로드(Hugging Face `timm/vit_base_patch14_reg4_dinov2.lvd142m`, 약 350MB) |

export 폴더 (이름은 아무거나, 안의 구조만 맞으면 된다):

| 가명 | 종류(kind) | 내용 |
|---|---|---|
| SITE_A | `human` | 사람 라벨 export |
| SITE_A | `propagated` | 전파 라벨 export |
| SITE_B | `full` | 전체 사진 export (약라벨 + 사람 라벨 일부) |
| SITE_C | `human` | 사람 라벨 export |
| SITE_D | `full` | 전체 사진 export |

각 export 안: `images/`, `semantic_masks/`, `target_masks/`, `visibility_masks/`, `state_masks/<activity>/`, `splits/`,
`metadata.jsonl`, `targets.json` (`label_map.json` 등 나머지는 있어도 그만).
어떤 가명이 어떤 현장 폴더인지는 개인 전달 zip의 `README_private.md`에 있다.

## 2. local.json 만들기

zip을 원하는 곳에 풀고, 안의 `local.example.json`을 복사해 `local.json`으로 저장한 뒤 경로를 채운다.
상대 경로는 local.json 위치 기준이다. 이 파일은 저장소 밖에 두거나, 저장소 안이면 `.gitignore`가 막는다.

```json
{
 "bundle": "/data/loso_bundle",
 "results": "/data/loso_results",
 "exports": {
  "SITE_A": {"human": "/data/exports/A_human", "propagated": "/data/exports/A_propagated"},
  "SITE_B": {"full": "/data/exports/B_full"},
  "SITE_C": {"human": "/data/exports/C_human"},
  "SITE_D": {"full": "/data/exports/D_full"}
 }
}
```

Windows는 `"D:\\data\\exports\\A_human"`처럼 역슬래시를 두 번 쓰거나 `/`를 쓴다.

## 3. 확인 (`check`)

```bash
python -m vision_preprocessing.train.loso check --local local.json
```

- **ERROR**가 하나라도 있으면 `run`은 시작하지 않는다. 주로: export가 다른 버전(해시 불일치) / 경로 오류 / 번들이 다른 규격용.
- **WARNING**(버전 차이, GPU 메모리, 디스크)은 실행은 되지만 결과 비교에 영향을 줄 수 있다. 환경은 `environment.yml` 그대로 쓰자.

## 4. 실행 (`run`)

```bash
python -m vision_preprocessing.train.loso run --local local.json --models mlp,dpt
python -m vision_preprocessing.train.loso status --local local.json --models mlp,dpt --watch 60   # 진행 상황(60초마다 갱신)
```

- 진행 표시: 실행 중 화면에 약 1분마다 `[진행]` 줄(이번 실행의 막대·%·epoch/step·남은 시간·완료 예상 시각)이,
  실행이 바뀔 때마다 `[전체]` 줄(완료한 실행 수·전체 %·남은 시간·완료 예상 시각)이 나온다.
  다른 터미널에서 `status --local local.json --watch 60`을 켜 두면 실행별 막대와 전체 진행이 60초마다 새로 그려진다(Ctrl+C로 종료).
  남은 시간은 실제로 잰 step·평가 시간으로 계산하고, 아직 안 돈 실행은 끝난 실행(같은 모델·fold 우선)의 시간으로 추정한다.
  첫 실행이 끝나기 전에는 "측정 전"으로 나온다.
- 순서: 시드 0의 (mlp, dpt) × 4 fold → 시드 1 → 시드 2. linear는 항상 맨 마지막.
- 실행 하나 = 15 epoch, RTX 4060 Laptop 기준 약 3시간. mlp+dpt 24회 ≈ 72시간.
- **멈춰도 괜찮다.** 같은 명령을 다시 치면 끝난 실행은 건너뛰고, 중간이던 실행은 마지막으로 끝난 epoch 다음부터 이어간다
  (`resume.pt`). 이어서 돌려도 처음부터 끊김 없이 돌린 것과 같은 순서·증강으로 진행된다.
- 실패하면 거기서 멈추고 `results/<model>/<fold>/seed<k>/stdout.txt`를 가리킨다. `--keep-going`이면 다음 실행으로 넘어간다.
- `--workers N`: 데이터 읽기 프로세스 수(기본 4). 결과에는 영향 없음. 메모리가 부족하면 줄인다.
- Linux에서 SSH로 접속했다면 `tmux new -s loso` 안에서 실행하고, 나갈 때 `Ctrl-b d`, 다시 볼 때 `tmux attach -t loso`.

## 5. PC 간 재현성 비교 (1회, 약 3시간)

양쪽 PC에서 같은 설정을 각자의 별도 폴더에 돌리고, 결과 폴더를 서로 받아 비교한다.

```bash
python -m vision_preprocessing.train.loso run --local local.json --models upernet --folds SITE_B --seeds 0 --results /data/loso_repro
python -m vision_preprocessing.train.loso compare /data/loso_repro/upernet/SITE_B/seed0 <상대 PC 결과의 같은 폴더>
```

## 6. 결과 넘기기 (`pack`)

```bash
python -m vision_preprocessing.train.loso pack --local local.json --out results_mlp_dpt.zip
```

기본으로 기록 파일(json/csv/txt)만 담는다. 현장 사진이 들어간 overlay(`--with-overlays`)와 가중치(`--with-weights`)는
요청이 있을 때만 넣는다. zip은 개인적으로 전달한다(현장 사진·ID 포함 가능 → 공개 저장소·클라우드 공유 금지).

## 7. 표와 그림 (책임자)

```bash
python -m vision_preprocessing.train.loso report --results <내 결과 폴더> results_mlp_dpt.zip --out summary --lang ko
```

`summary/`: `summary.md`(한글 문서로 옮기기 쉬운 표), `main_table.csv`, `macro_table.csv`, `pipeline_table.csv`,
`bootstrap.csv`, `size_bins.csv`, `resources.csv`, `curves_epoch.csv`, `curves_step.csv`, `val_selected_epoch.csv`, `events.csv`,
`risk_coverage.csv`, `figures/fig1~fig8, fig10 (.png 300dpi + .pdf + 그림별 .csv)`, `figures_with_photos/fig9_*`(현장 사진 → 사용 전 확인).

## 8. 실행 하나가 남기는 기록

| 파일 | 내용 |
|---|---|
| `config.json` | 규격 전체, 실행 식별자, 버전, GPU, 코드 해시, git 커밋, 인코더 가중치 해시, 데이터 해시 |
| `environment.txt` | 설치된 패키지 전체 |
| `split_manifest.json` | train / val / test 사진 ID와 개수 |
| `steps.csv` | 50 step마다 손실(전체/물체/상태), 학습률, 기울기 크기, 처리 속도 |
| `epochs.csv`, `epoch_metrics.jsonl` | epoch별 학습 손실, val 지표, test 지표(분석용), 시간, 최대 GPU 메모리 |
| `final.pt` | 마지막 epoch 디코더·헤드 |
| `metrics.json` | 최종 val / test 평가 |
| `photo_rows_<추론변형>_<val/test>.csv` | 사진×대상별 정답·예측 설치율, 시점, ROI 크기, 보이는 비율, 확신도 |
| `daily_rows_base_*.csv` | 날짜×대상 행 |
| `confusion_state_*.csv`, `confusion_object_*.csv` | 혼동행렬 |
| `posteval.json` | 추론 변형별 요약 |
| `resources.json` | 파라미터 수, 추론 시간, 최대 메모리, epoch 시간 |
| `overlays/test/` | 미리 정한 사진의 사진 / 정답 / 예측 |
| `progress.json` | 실시간 진행(단계, %, 남은 시간) — `status`가 읽는다 |
| `stdout.txt`, `DONE.json` | 실행 화면 기록, 완료 표시(총 소요 시간 포함) |

## 9. 자주 나는 문제

| 증상 | 조치 |
|---|---|
| `check`: export differs | 다른 버전의 export. 책임자와 같은 export를 받는다 |
| `CUDA out of memory` | 다른 GPU 프로그램 종료. 그래도 나면 책임자에게 알림(batch는 규격이라 바꾸지 않는다) |
| 가중치 다운로드 실패 | 인터넷/프록시 확인. 한 번 받으면 캐시(`~/.cache/huggingface`)에서 읽는다 |
| DataLoader / worker 오류 (특히 Windows) | `run ... --workers 0` (느려지지만 결과는 같다) |
| `resume.pt belongs to a different run` | 규격이나 번들이 바뀐 뒤 옛 중간 파일이 남음. 그 실행 폴더를 옮기고 다시 실행 |
| 테스트 | `python -m pytest vision_preprocessing/tests/test_loso.py -q` (합성 데이터, 몇 분) |

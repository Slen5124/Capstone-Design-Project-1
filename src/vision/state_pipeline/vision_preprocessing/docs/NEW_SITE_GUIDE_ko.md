# 새 현장(도시) 라벨링 사용 설명서

> 갱신: 2026-10-21 (할 일 화면 카드·버튼, 앵커 진행 방식, prewarm, 저장 실패 로그 반영)

SITE_A에서 실제로 해 본 순서를 그대로 정리했습니다. 다른 도시(SITE_B, SITE_C, SITE_D …)도 같은 순서로 하면 됩니다.
명령은 모두 **Windows PowerShell** 기준입니다.

---

## 0. 한눈에 보기

```
[1회] 컴퓨터 준비 ─ (필요할 때만) 1차 자동 전처리 qa_v3 ─ [현장마다] 설정 확인 → auto → 검수 → auto → … → 할 일 0건
```

| 단계 | 프로그램 | 입력 → 출력 |
|---|---|---|
| 1차 자동 전처리 | `전처리_실행.cmd` (qa_v3) | 사진 ZIP + 원본 JSON ZIP → `automatic_results\run_*` (객체 폴리곤 자동 보정본) |
| 2차 상태 전처리 | `vision_preprocessing\vp.cmd auto` | `automatic_results` → 구도·ROI·상태 라벨·학습 데이터 |

| 누가 | 무엇을 |
|---|---|
| 자동(auto) | 사진 확인, 구도 구분, 카메라 밀림 검사, 상태 변화 후보, 표본 선택, 라벨 초안, 학습 데이터 export, 할 일 목록 |
| 사람 | 구도 승인 → ROI 그리기 → 대상/공정 적용 여부 → 기준(앵커) 사진 몇 장 → 초안 승인 |

자동으로 만든 것은 전부 **초안**입니다. 사람이 승인한 것만 학습 정답이 됩니다.

---

## 1. 다른 컴퓨터 준비 (한 번만)

### 1-1. 폴더 놓기 — 묶음(zip) 구조 그대로
이 설명서의 경로는 **묶음 zip을 푼 구조**를 기준으로 합니다. `<작업폴더>`는 아무 위치나 됩니다(예: `E:\work`, `C:\Users\me\proj`).

```
<작업폴더>\
  Data\                                   ← 따로 복사: 원본 사진 ZIP 4개 + processed_json ZIP  (기밀, 외부 업로드 금지)
  전처리된 데이터\                          ← 묶음 zip 안의 같은 이름 폴더를 그대로 풀기
    vision_preprocessing\                  (묶음에 포함) 이 파이프라인
    qa_v2\  qa_v3\                         (묶음에 포함) 기존 코드, import만 함
    requirements.v3.lock.txt               (묶음에 포함)
    automatic_reference_v3\project.json    (묶음에 포함) 1차 자동 전처리 설정(대표 기준 사진 12장 정의)
    전처리_실행.cmd, 전처리_상태.cmd, run_auto_preprocess.ps1, README_AUTO.txt, README_OPERATIONS.txt   (묶음에 포함) 1차 단계 실행기
    state_preprocessing_results\review_store\SITE_A.sqlite   (묶음에 포함) SITE_A 승인 라벨
    automatic_results\                     ← 따로 복사: qa_v3 결과 폴더 통째로(약 6GB, 4개 도시 포함)
    .venv\                                 ← 새로 만들기 (1-2)
```

- **`Data` 폴더는 `전처리된 데이터` 폴더와 같은 위치(형제 폴더)에** 두세요. 그러면 경로 설정을 고칠 필요가 없습니다.
  설정 파일에는 원래 PC 경로(`D:\work\project\...`)가 적혀 있지만, 그 경로가 없는 컴퓨터에서는 프로그램이
  자동으로 "`vision_preprocessing` 이 들어 있는 폴더 = 전처리된 데이터, 그 옆 = Data" 로 바꿔 읽습니다.
- `Data`를 다른 곳(예: 외장 디스크)에 두고 싶을 때만, 현장 설정 파일(`vision_preprocessing\config\<현장>.json`)의 `"path_map"`에 한 줄 넣습니다.
  ```json
  "path_map": {"D:\\work\\project\\Data": "F:\\사진\\Data"}
  ```
- **ZIP 파일 이름을 바꾸지 마세요.** 사진 ID(`SITE_A-20250219T153218-7ba681f8`)가 ZIP 이름+파일명으로 만들어져서, 이름이 바뀌면 기존 라벨과 연결이 끊깁니다.
- 아래 명령은 모두 `<작업폴더>\전처리된 데이터` 에서 실행합니다.

### 1-2. 파이썬 환경
```powershell
cd "<작업폴더>\전처리된 데이터"
python -m venv .venv                      # Python 3.10 이상 (SITE_A는 3.13에서 실행)
.venv\Scripts\python.exe -m pip install -r requirements.v3.lock.txt
```
필요한 패키지: numpy, opencv-python-headless(SIFT 포함), pillow, shapely, pyzipper, pycryptodomex.

### 1-3. 설치 확인 (합성 데이터 테스트, 2~3분)
```powershell
.venv\Scripts\python.exe -m unittest vision_preprocessing.tests.test_pipeline vision_preprocessing.tests.test_auto
```
`OK` 가 나오면 준비 끝. (이 테스트는 코드 동작만 확인합니다. 실제 인식 정확도와는 무관합니다.)

---

## 1-4. 1차 자동 전처리 (qa_v3) — 필요할 때만

**`automatic_results` 폴더를 원래 PC에서 복사해 왔다면 이 단계는 건너뜁니다.** (4개 도시 36,862장 완료본, 원래 PC에서 약 5시간 걸림)

다시 돌려야 하는 경우: 새 도시 ZIP을 추가할 때, 원본 JSON(`processed_json` ZIP)이 바뀌었을 때, `automatic_results` 없이 새로 시작할 때.

1. **경로 맞추기 (새 컴퓨터에서 한 번)** — qa_v3 설정(`automatic_reference_v3\project.json`)에는 원래 PC의 ZIP 경로가 적혀 있습니다.
   ```powershell
   .venv\Scripts\python.exe -m vision_preprocessing.tools.relocate_qa_v3_project            # 무엇이 바뀌는지 미리보기
   .venv\Scripts\python.exe -m vision_preprocessing.tools.relocate_qa_v3_project --apply    # 적용 (원본은 .bak 로 보관)
   ```
   새 도시를 추가할 때는 `--add-zip "<작업폴더>\Data\새도시.zip"` 을 붙입니다. (그 도시의 원본 JSON이 `processed_json` ZIP 안에 있어야 합니다.)
2. **실행** — 탐색기에서 `전처리_실행.cmd` 더블클릭 → 메뉴
   - `1` 대표 13장(빠른 확인) → 문제없으면 `3` 전체
   - **ZIP 암호를 물어봅니다.** 입력만 하고 파일에는 저장되지 않습니다(또는 환경변수 `QA_ZIP_PASSWORD`).
   - 명령으로: `전처리_실행.cmd -Limit 100` (100장 시험), `전처리_실행.cmd -MaxMinutes 60` (60분 후 안전 중단)
3. **상태 확인 / 중단 / 이어하기** (다른 창에서)
   ```powershell
   .\전처리_상태.cmd -Watch                         # 진행률
   .\전처리_상태.cmd -Stop                          # 안전 중단 요청
   .\전처리_실행.cmd -Resume "<작업폴더>\전처리된 데이터\automatic_results\run_실행시각"
   ```
4. **결과** — `automatic_results\run_실행시각\` 와 `automatic_results\latest_active_run.json`(최신 완료 결과를 가리킴)이 갱신됩니다.
   2차 단계는 **새로 만드는 run**(`auto --config ...`)부터 이 최신 결과를 씁니다. 기존 run은 만들 때의 결과를 계속 씁니다.
   승인한 라벨은 그대로 이어집니다(사진 ID는 1차 결과와 무관).
   - 대표 기준 사진(references)이 없는 새 도시는 벽·천장 자동 복원을 못 하고 그 사유가 기록됩니다. 2차 단계는 이런 결과(needs_review)도 정상 입력으로 씁니다.
   - 자세한 1차 단계 설명: `README_AUTO.txt`, `README_OPERATIONS.txt`
5. **(권장) 폴더 이름 정리** — 끝난 결과 폴더(`run_실행시각`)를 알아보기 쉬운 이름으로 바꿉니다. 포인터도 함께 고쳐지고, 폴더 안에 `README_1차결과.md`와 `FIRST_STAGE_RUN.json`이 생깁니다 (결과 파일 자체는 그대로).
   ```powershell
   .venv\Scripts\python.exe -m vision_preprocessing.tools.rename_first_stage_run            # 확인만
   .venv\Scripts\python.exe -m vision_preprocessing.tools.rename_first_stage_run --apply    # 이름 변경
   ```
   현재 4개 도시 결과: `automatic_results\1차결과_qa_v3_전체4도시_20261009` (원래 `run_20261009T163018_494922Z`).
   `FIRST_STAGE_RUN.json`은 지우지 마세요 — 안의 manifest가 원래 이름을 가리키고, 2차 코드가 이 파일로 새 위치를 찾습니다.

---

## 2. 현장 설정 파일

이미 만들어 둔 설정:

| 현장 | 설정 파일 | site_id | 사진 수(manifest) |
|---|---|---|---:|
| SITE_A | `config\site_example.json` | SITE_A | 10,175 (완료) |
| SITE_B | `config\site_b.json` | SITE_B | 9,916 |
| SITE_C | `config\site_c.json` | SITE_C | 7,362 |
| SITE_D | `config\site_d.json` | SITE_D | 9,407 |

새 도시가 더 생기면:
```powershell
vision_preprocessing\vp.cmd new-site --site-id SEOUL --name "Example Store #200 Seoul" --zip-contains "#200_-_Seoul"
```
- 단, 그 도시가 먼저 qa_v3 자동 전처리(`run_auto_preprocess.ps1`)를 거쳐 `automatic_results` manifest에 들어 있어야 합니다.
- 설정의 대상 W01/W02/F01/C01 은 **SITE_A 배치를 복사한 임시 대상**입니다(`proposed`, 적용 여부 `unknown`). 검수 화면에서 현장에 맞게 이름·확정·적용 여부를 정합니다. 필요하면 `+ target` 으로 W03 등을 추가합니다.

---

## 3. 현장 하나 라벨링하기 (SITE_B 예)

아래 `<RUN>` 은 처음 auto가 끝날 때 출력되는 run 폴더 경로입니다. **PowerShell 변수(`$RUN`)는 창을 새로 열면 사라지므로 경로를 그대로 쓰는 것이 안전합니다.**

### 3-1. 첫 auto (전체 사진, 20~40분)
```powershell
cd "<작업폴더>\전처리된 데이터"
vision_preprocessing\vp.cmd auto --config vision_preprocessing\config\site_b.json --label site_b_full
```
끝나면 마지막 줄에 `run dir: state_preprocessing_results\run_..._site_b_full` 이 나옵니다. 이 경로를 메모하세요.
중간에 끊기면 그대로 이어서:
```powershell
vision_preprocessing\vp.cmd auto --run "state_preprocessing_results\run_..._site_b_full"
```

### 3-2. 검수 화면 열기
```powershell
vision_preprocessing\vp.cmd review --run "state_preprocessing_results\run_..._site_b_full"
```
브라우저에서 **http://127.0.0.1:8765/todo** (할 일 화면). 오른쪽 위에 검수자 이름 입력.

### 3-3. 할 일 화면에서 위에서부터 (순서가 중요)
1. **구도(view) 경계 제안 / 구도 승인**
   초록(남는 사진)·주황(넘어가는 사진) 썸네일을 보고 카메라 화면이 바뀐 게 맞으면 수락+승인. 구도별 기준 사진과 기간 안 사진이 같은 화면이면 Approve view.
2. **ROI 그리기 → ROI 승인** (구도 × 대상마다)
   기준 사진에서 Draw ROI polygon → Save → Approve ROI. 비슷한 구도는 "Copy as drafts" 후 꼭짓점만 고치기.
   - 문·창·설비처럼 **끝까지 남는 개구부는 제외영역**(Draw exclusion, 이유 door_opening / window / fixture / not_in_scope)
   - **나중에 막을 임시 통로는 ROI에 포함**(막히기 전 = not_installed)
   - 새 화면의 벽이 다른 구도의 W01과 **같은 물리적 벽인지** 판단. 다르면 `+ target` 으로 새 대상.
3. **대상/공정 적용 여부**
   - 이 기간에 실제로 진행되는 공정만 `applicable`.
   - **촬영 시작 전에 이미 끝난 공정(예: SITE_A 바닥·천장)은 `not_applicable`** + 근거. 보호재로 덮인 완공 바닥을 "미설치"로 라벨하면 모델이 잘못 배웁니다.
4. 검수 창 **Ctrl+C** → **auto 다시 실행** (3-1의 두 번째 명령) → 검수 화면 다시 열기
5. **ROI 검수 결과 · ROI 겹침 · 구도 사이 빈 시간** (있으면)
   카드마다 사진 위에 ROI가 그려져 있고 버튼이 있습니다. 고칠 것은 "열기 → 고치기"로 Save → Approve 하면 목록에서 저절로 빠지고,
   확인만 할 것은 "확인 (그대로 둠)". 쓸 수 없는 구도는 "구도 학습에서 제외". 카메라가 밀린 날 ROI가 그대로 맞으면 "ROI 그대로 써도 됨".
6. **직접 그릴 기준(앵커) 사진** — 구도마다 처음·중간·마지막 날 한 장씩
   "열기 → W01"로 그 벽이 선택된 채 열립니다. installed / not_installed 를 그리고(전체가 한 상태면 "fill rest of ROI") Save → Approve.
   앞 사진과 상태가 같으면 체크 후 "앞 사진과 같음 → 복사+승인". 승인한 사진은 바로 목록에서 빠집니다.
   천장·바닥 앵커는 아래에 접혀 있습니다(그 공정을 안 하면 적용 여부를 `not_applicable`로). 끝나면 auto 다시 실행 → 나머지 표본에 초안.
7. **자동 초안 검수** (Grid로 검수)
   - `stable_copy`(안정 구간 복사): 훑어보고 맞으면 선택 → "Same as previous photo → approve"
   - `change_period_copy_needs_editing`(변화 구간): 사진을 열어 경계를 고친 뒤 승인
   - **반드시 시간 순서상 앞쪽 사진부터 고치고 나서 뒤쪽을 일괄 승인.** 순서가 바뀌면 고치기 전 값이 복사됩니다(SITE_A에서 13건 발생).
8. **승인된 라벨 중 확인이 필요한 것** (설치율이 거꾸로 가거나 몇 시간 만에 크게 바뀜)
   비교 사진과 나란히 보고 맞으면 그대로, 틀렸으면 "초록 사진과 같은 상태로 교체+승인" 또는 직접 수정.
9. 4번처럼 auto 다시 → 할 일 화면에 "자동 초안 검수"가 **0건이면 끝**.

### 3-4. 결과물
`<RUN>\exports\<날짜>_train_full\` (가장 최근 것)

| 파일 | 내용 |
|---|---|
| `images/`, `semantic_masks/`, `target_masks/`, `state_masks/<공정>/`, `visibility_masks/` | 학습용 이미지·마스크 (값 정의는 `label_map.json`) |
| `splits/train.txt, val.txt, test.txt` | 날짜 묶음 단위 분할(누수 검사 통과) |
| `metadata.jsonl` | 샘플별 출처·좌표 변환·승인 기록 |
| `measurements_<공정>.jsonl` | 사진×대상별 "보이는 영역 안 설치 비율" |
| `daily_progress.csv / .json` | 날짜별 진행률 + 전날 대비 |
| `validation_report.json` | export 검증 결과 |

할 일 목록 파일: `<RUN>\HUMAN_TODO.md`

---

## 4. 자주 생긴 문제

| 증상 | 원인 / 해결 |
|---|---|
| `argument --run: expected one argument` | `$RUN` 이 비어 있음(새 창). 경로를 직접 쓰기 |
| `run is in use by 'review server'` | 검수 창이 켜져 있음. Ctrl+C로 끄고 auto. (auto 중에는 검수 화면이 "연결할 수 없음") |
| 다른 PC가 남긴 잠금 | `--force-unlock` (그 프로세스가 정말 꺼져 있을 때만) |
| `ConnectionAbortedError [WinError 10053]` | 사진이 다 뜨기 전에 다음 사진으로 넘어간 것. 무해, 데이터 손실 없음 |
| auto가 중간에 멈춤 / 창을 닫음 | 같은 `--run` 으로 다시 실행하면 이어서 진행 |
| `views` 단계가 3분쯤 걸림 | 정상 |
| 사진이 처음 열릴 때 1~3초 (특히 SITE_C·SITE_D 암호 ZIP) | 원본 ZIP에서 푸는 중. 미리 한 번에 풀기: `vp.cmd prewarm --run <RUN>` (검수 서버 켜둔 채 다른 창에서, 끊겨도 이어서 됨) |
| 라벨/ROI 저장이 안 됨 | 화면 아래 빨간 메시지(클릭해야 닫힘)를 확인. 모든 실패는 `<RUN>\review_errors.log` 에 기록됨 → 그 파일을 보여주면 원인 파악 가능 |
| 저장 후 "자동 보정" 메시지 | 사진 밖에 찍힌 점·꼬인 폴리곤을 자동으로 고쳐 저장한 것. 정상 |
| PowerShell 에서 `cd /d`, `rem`, `%VP%` 가 안 됨 | 그건 cmd 문법. PowerShell 에서는 `.\vision_preprocessing\vp.cmd ...` 형태로 한 줄씩 |
| 할 일 화면에 사진이 안 보임 | 검수 서버를 최신 코드로 다시 켜기 |

---

## 5. 지켜야 할 원칙
- 원본 ZIP, `automatic_results`, qa_v2/qa_v3 코드는 **수정하지 않음** (읽기만)
- 사진·라벨을 **외부 서비스로 업로드하지 않음**. 검수 서버는 127.0.0.1 에서만 열림
- 자동 결과는 초안. 승인은 사람 이름으로만 가능 (`auto`, `system`, `claude` 같은 이름은 거부)
- 퍼센트 = **사진에서 보이는 부분 안에서 설치된 비율(픽셀)**. 실제 ㎡ 진행률·매장 전체 진행률 아님
- 시간은 카메라 파일명 기준 현지시각, 시간대 미확인

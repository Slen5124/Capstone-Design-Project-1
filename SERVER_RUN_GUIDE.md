# GPU 서버 실행 가이드 (Windows PowerShell 원클릭 가이드)

Windows PowerShell을 열고 접속부터 U-Net 학습 실행까지 **그대로 복사-붙여넣기**할 수 있도록 순서대로 정리된 가이드입니다.

---

## 1단계: 서버 접속 (Windows PowerShell에서 실행)

내 컴퓨터에서 **Windows PowerShell**을 열고 아래 명령어를 붙여넣어 실행합니다:

```powershell
ssh -p 10000 coss32@155.230.81.91
```

> 💡 **비밀번호 입력 팁**: 
> 비밀번호를 타이핑할 때 화면에 `***` 같은 글자가 일절 뜨지 않는 것이 정상입니다. 당황하지 마시고 비밀번호를 끝까지 입력한 후 **Enter**를 치세요.
> 정상 접속되면 프롬프트가 `coss32@ABRM02:~$` 로 변경됩니다.

---

## 2단계: GPU 연산 노드 대여 (ABRM02에서 실행)

로그인 서버(`ABRM02`)에 진입하셨으면 아래 명령어를 복사해 GPU 1대를 대여합니다:

```bash
srun -p p02 --gres=gpu:1 --cpus-per-task=4 --mem=32G --time=04:00:00 --job-name=taeung-gpu --pty bash
```

> 💡 **확인 포인트**: 
> 잠시 후 프롬프트가 `coss32@ABRM02`에서 **`coss32@DIS06`** (또는 다른 번호)로 바뀌면 GPU 할당 완료입니다.

---

## 3단계: 환경 켜기 및 학습 시작 (GPU 노드에서 실행)

GPU 노드(`DIS06`)로 진입하셨으면, 아래 박스의 명령어를 **통째로 복사해서 한 번에 붙여넣기** 하시면 됩니다:

```bash
source /abr/coss32/anaconda3/etc/profile.d/conda.sh
conda activate alo-train
export PYTHONNOUSERSITE=1
cd ~/Aloha
git pull origin Preprocessing_SAM3
python src/train_unet_server.py
```

---

## 🎯 정상 실행 화면 및 안내

* **자동 이어 학습**: 
  실행 즉시 `weights/` 폴더의 가장 최신 체크포인트(`unet_epoch_5.pth`)를 스스로 감지하여 **Epoch 6부터 자동으로 이어서 학습**을 시작합니다.
  ```text
  [*] 사용할 디바이스: cuda
  [*] [이어 학습하기] 체크포인트 'weights/unet_epoch_5.pth' 로드 완료! Epoch 6부터 학습을 시작합니다.
  [*] 모델 학습을 시작합니다! (진행 범위: Epoch 6 ~ 20)
  Epoch [6/20]:   0%|  ...
  ```
* **자동 중간 저장**: 
  5 에포크마다(`unet_epoch_10.pth`, `unet_epoch_15.pth`, `unet_epoch_20.pth`) 안전하게 가중치가 디스크에 자동 저장됩니다.

---

## 4단계: 작업 종료 및 취소 방법

### 1. 학습이 끝나고 나갈 때
```bash
exit  # GPU 노드 반환 (DIS06 -> ABRM02)
exit  # SSH 접속 완전 종료 (내 PC로 복귀)
```

### 2. 돌고 있는 내 작업을 강제 중지하고 싶을 때
공용 계정이므로 팀원 작업이 꺼지지 않도록 본인 작업 번호만 종료해야 합니다:
```bash
squeue -u coss32                    # 내 작업 번호(JOBID) 확인 (이름: taeung-gpu)
scancel [확인한_작업번호]            # 예: scancel 12345
```

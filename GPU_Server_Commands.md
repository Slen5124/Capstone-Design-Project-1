# GPU 서버 명령어 가이드

접속 정보는 2026-10-08 실제 접속 결과 기준이에요.
비밀번호는 직접 입력하며, 입력해도 화면에 표시되지 않아요.

## 1. 서버 접속 — Windows PowerShell

```powershell
ssh -p 10000 coss32@155.230.81.91
```

접속하면 `coss32@ABRM02`가 표시돼요.

## 2. 다른 사람의 사용 현황 — 서버에서 실행

```bash
# p02의 전체 작업: 사용자·작업 이름·상태·노드 확인
squeue -p p02 -o "%.18i %.12u %.24j %.2t %.10M %R"

# 노드별 상태
sinfo -p p02 -N

# coss32 계정의 작업
squeue -u coss32
```

| 표시 | 의미 |
|---|---|
| 작업 R | 실행 중 |
| 작업 PD | 대기 중 |
| 노드 idle | 작업 할당 없음 |
| 노드 mix | 일부 자원 할당 중; GPU 여유는 별도 확인 |
| 노드 alloc | 작업에 할당됨 |
| 노드 down / drain | 새 작업 할당 불가 |

다른 USER의 R 작업이 있으면 다른 계정이 사용 중이에요.
공유 계정 coss32에서는 팀원 작업도 함께 보여요.
작업 이름·번호로 구분하고 본인이 제출한 작업만 중지하세요.
조회 권한에 따라 일부 작업은 보이지 않을 수 있어요.

## 3. GPU 할당 — 로그인 서버 ABRM02

```bash
srun -p p02 --gres=gpu:1 --cpus-per-task=4 --mem=32G --time=04:00:00 --job-name=taeung-gpu --pty bash
```

GPU 1개·CPU 4개·시스템 RAM 32GB를 최대 4시간 요청해요.
빈 자원이 없으면 대기하며, 대기 취소는 Ctrl+C예요.
성공하면 DIS06 등의 GPU 노드로 프롬프트가 바뀌어요.

```bash
hostname
echo "현재 작업 번호: $SLURM_JOB_ID"
nvidia-smi
```

nvidia-smi의 GPU-Util·Memory-Usage·Processes를 확인하세요.
이는 현재 노드에서 보이는 GPU 현황이며 서버 전체 현황은 아니에요.
사용률이 0%여도 다른 작업이 자원을 할당받고 있을 수 있어요.

## 4. 학습 환경 활성화 — GPU 노드

alo-train 환경을 만들어 둔 경우 실행하세요.

```bash
source /abr/coss32/anaconda3/etc/profile.d/conda.sh
conda env list
conda activate alo-train

cd Aloha
python ./src/train_unet_server.py

python --version
python -c "import torch; print('GPU 사용 가능:', torch.cuda.is_available())"
```

이 GPU 셸에서 본인의 학습 명령을 실행하세요.
로그인 서버 ABRM02에서는 GPU 학습을 직접 실행하지 마세요.
데이터·학습 결과는 공유 저장소 /abr/coss32 아래에 보관하세요.

## 5. 오래 걸리는 학습

대화형 srun은 연결 종료에 영향을 받을 수 있어요.
오래 걸리는 학습은 로그인 서버에서 sbatch로 제출하세요.

앞서 만든 train.sbatch가 있을 때 사용하는 예시예요.
먼저 GPU 셸에서 exit하여 기존 할당을 반환하세요.

```bash
read -r -p "학습 Python 파일의 서버 절대 경로: " train_file
if [ -f /abr/coss32/alo/train.sbatch ] && [ -f "$train_file" ]; then
    sbatch /abr/coss32/alo/train.sbatch "$train_file"
else
    echo "제출 스크립트 또는 학습 파일 경로를 확인하세요."
fi
```

필수 학습 옵션은 sbatch 줄의 "$train_file" 뒤에 추가하세요.
Submitted batch job 숫자가 나오면 제출 성공이에요.
제출된 작업은 SSH 연결을 종료해도 계속 진행돼요.

## 6. 종료·작업 중지

GPU 셸에서 exit하면 GPU를 반환하고 로그인 서버로 돌아가요.
로그인 서버에서 다시 exit하면 SSH를 종료해요.

```bash
exit
```

배치 작업을 중지하려면 본인이 제출한 번호만 입력하세요.

```bash
squeue -u coss32
read -r -p "중지할 본인의 작업 번호: " job_id
if [[ "$job_id" =~ ^[0-9]+$ ]]; then
    scancel "$job_id"
fi
```

공유 계정에서는 scancel -u coss32로 전체 작업을 취소하지 마세요.

## 7. 파일 업로드 — Windows PowerShell

서버에서 먼저 폴더를 만들어요.

```bash
mkdir -p /abr/coss32/alo/data
```

별도 Windows PowerShell에서 실행해요. scp 포트 옵션은 대문자 -P예요.

```powershell
scp -P 10000 -r "D:\job\종말 프로젝트\Capstone-Design-Project-1" coss32@155.230.81.91:/abr/coss32/alo/
scp -P 10000 -r "D:\job\종말 프로젝트\전처리된 데이터" coss32@155.230.81.91:/abr/coss32/alo/data/
```

코드의 Windows 데이터 경로는 서버 경로로 바꿔주세요.

## 출처

- 접속 정보·계정·p02 권한·Anaconda 경로: 사용자가 제공한 서버 출력.
- [Slurm squeue](https://slurm.schedmd.com/squeue.html)
- [Slurm sinfo](https://slurm.schedmd.com/sinfo.html)
- [Slurm srun](https://slurm.schedmd.com/srun.html)
- [Slurm sbatch](https://slurm.schedmd.com/sbatch.html)
- [Slurm scancel](https://slurm.schedmd.com/scancel.html)
- [NVIDIA nvidia-smi](https://docs.nvidia.com/deploy/nvidia-smi/index.html)
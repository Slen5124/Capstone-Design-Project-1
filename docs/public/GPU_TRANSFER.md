# DB 연결과 GPU 사용

2026년 10월 13일 정리. 주소·계정·경로 자리표시자는 실제 값으로 바꾼다.

## 전달 대상과 권한

- 자료를 받는 사용자: 개인용 Markdown과 전송 도구 ZIP을 받는다. GPU 로그인 계정은 기관에서 별도로 제공받는다.
- 사용자는 GPU에서 키를 만들고 공개키(.pub)만 AWS 관리자에게 전달한다. 관리자는 지정 자료의 읽기 전용 권한을 등록한다.
- AWS 관리자용 PEM은 사용자에게 전달하지 않는다. 아래 1절의 DB/API 접속은 AWS 관리자용이며, 자료 다운로드 사용자는 2절부터 진행한다.

## 1 DB와 API 접속

Windows PowerShell에서 실행한다.

~~~powershell
$AloKey = 'AWS 개인키.pem의 전체 경로'
$AloServer = '<AWS_HOST>'
$AloUser = '<AWS_USER>'
$AloTarget = $AloUser + '@' + $AloServer
ssh -i $AloKey -N -L 127.0.0.1:18000:127.0.0.1:8000 $AloTarget
~~~

이 창을 유지하고 브라우저에서 http://127.0.0.1:18000/docs 를 연다.
별도의 PowerShell 창에서 DB 연결을 확인한다.

~~~powershell
Invoke-RestMethod 'http://127.0.0.1:18000/health'
~~~

database가 connected면 정상이다. 파일·공정표·모델·관측 등록과 조회는 /docs에서 한다.

## 2 작은 도구 ZIP 보내기와 GPU 로그인

Windows PowerShell에서 실행한다. 이 ZIP에는 전송 도구만 있고 실제 자료는 AWS에 있다.

~~~powershell
$TransferZip = '<HELPER_ZIP>'
scp -P <GPU_PORT> $TransferZip '<GPU_USER>@<GPU_HOST>:~/project-transfer-tools.zip'
ssh -p <GPU_PORT> <GPU_USER>@<GPU_HOST>
~~~

## 3 이번 사용의 읽기 전용 키 등록

GPU 로그인 서버에서 실행한다.

~~~bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
KEY="$HOME/.ssh/alo_fetch_$(date +%Y%m%dT%H%M%S)"
test ! -e "$KEY" && test ! -e "$KEY.pub" || exit 1
ssh-keygen -t ed25519 -f "$KEY" -C alo-fetch-session
chmod 600 "$KEY"
cat "$KEY.pub"
~~~

공개키(.pub) 한 줄만 AWS 관리자에게 전달한다. 비밀번호와 passphrase는 터미널에서 입력하고 개인키는 전달하지 않는다.

AWS 관리자는 rsync·rrsync 경로를 확인하고 기존 authorized_keys를 보존하면서 이번 공개키에 아래 제한을 붙여 등록한다.

~~~text
restrict,command="<RRSYNC_PATH> -ro <AWS_DATA_ROOT>" <SESSION_PUBLIC_KEY>
~~~

읽기 전용 등록이 끝나야 다음 다운로드가 가능하다. 이 키는 AWS 셸 접속·결과 업로드에 사용하지 않는다.

## 4 도구 준비와 자료 다운로드

GPU 로그인 서버에서 실행한다. KEY와 ALO_RUN은 같은 셸에서 유지한다.

~~~bash
umask 077
ALO_RUN="<GPU_SHARED_ROOT>/alo/runs/bj-$(date +%Y%m%dT%H%M%S)"
export ALO_RUN
test ! -e "$ALO_RUN" || exit 1
mkdir -p "$ALO_RUN"
df -h <GPU_SHARED_ROOT>
quota -s
python3 -m zipfile -e ~/project-transfer-tools.zip "$ALO_RUN/tools"
cd "$ALO_RUN/tools/alo-gpu-transfer"
python3 fetch.py --list
eval "$(ssh-agent -s)"
ssh-add "$KEY"
python3 fetch.py --key "$KEY" --check-connection
~~~

Python3.8 이상·SSH·rsync가 필요하다. 개인 quota와 ZIP·해제 데이터·결과를 함께 보관할 공간을 확인한다. 호스트 키 불일치는 관리자에게 확인하고 검사를 끄지 않는다.

연결 확인 후 Brea만 받는다.

~~~bash
python3 fetch.py --key "$KEY" --site brea --dest "$ALO_RUN/archives"
~~~

다섯 자료 전체가 필요하면 위 명령 대신 다음을 실행한다.

~~~bash
python3 fetch.py --key "$KEY" --site all --dest "$ALO_RUN/archives"
~~~

크기·SHA-256 검증 완료 메시지를 확인한다. 중단되면 같은 키·저장 경로·명령으로 재시도한다. 완료 파일을 덮어쓰거나 잠금을 지워 동시 실행하지 않는다. 개인 quota 조회가 안 되면 관리자에게 확인한다.

## 5 ZIP 확인과 GPU 할당

GPU 로그인 서버에서 Brea ZIP의 내부 목록을 확인한다.

~~~bash
python3 -m zipfile -l "$ALO_RUN/archives/brea/v001/archive.zip"
~~~

정답·클래스·분할과 안전한 내부 경로를 확인하고 새 폴더에 해제한다. 다운로드 완료만으로 학습 GT가 승인된 것은 아니다. 압축 해제 공간과 기관의 파일 관리 정책도 확인한다.

p02 사용 권한을 확인한 뒤 GPU를 할당받는다.

~~~bash
sacctmgr show assoc format=User,Partition where user=<GPU_USER>
srun -p p02 --gres=gpu:1 --cpus-per-task=4   --mem=32G --job-name=alo-bj-train --pty bash
~~~

할당된 GPU 노드에서 확인한다.

~~~bash
hostname
echo "$SLURM_JOB_ID"
nvidia-smi
~~~

확인된 팀 학습 환경과 실행 코드를 사용한다. 결과는 공유 저장소에 보관하고 대화형 GPU 작업을 끝낼 때 exit로 할당을 반환한다. 작업 취소는 본인 JOB_ID만 대상으로 한다.

## 6 사용 후 정리

1. 가중치·로그·분석 결과가 보관됐는지 확인한다.
2. GPU 로그인 셸에서 이번 키를 agent에서 제거한다.

~~~bash
ssh-add -d "$KEY"
~~~

3. AWS 관리자는 authorized_keys에서 이번 공개키 항목만 회수한다.
4. 작업 종료와 보관을 확인한 뒤 이번 개인키·.pub·GPU 자료만 운영 방침대로 정리한다. AWS 원본과 다른 팀원의 자료는 보존한다.

agent 제거만으로 키 파일과 AWS 권한이 삭제되지는 않는다. 다음 사용은 새 키로 시작한다.

이번 문서 수정에서 실제 키 등록·GPU 다운로드·학습은 실행하지 않았다.

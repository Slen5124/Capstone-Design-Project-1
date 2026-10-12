# GPU 연결과 자료 가져오기

## 1. 목표와 현재 상태

AWS 자료를 GPU 공유 저장소로 직접 가져온다. PC는 로그인과 작은 도구 파일 전달에만 사용하며 데이터 ZIP을 받아 다시 올리지 않는다.

사용 후 GPU 자료와 연결 키를 삭제하는 운영 조건이므로 **매 사용마다 새 키를 생성하고 등록한다.** 현재 다운로드 도구는 준비됐지만 키 발급·등록·회수·자료 삭제를 한 번에 자동화한 상태는 아니다.

## 2. 필요한 값

실제 값은 관리자에게 별도로 받는다. 아래 표와 명령에는 공개용 자리표시자만 사용했다.

| 자리표시자 | 의미 |
|---|---|
| `<GPU_HOST>`, `<GPU_PORT>`, `<GPU_USER>` | GPU 로그인 서버 주소·포트·계정 |
| `<AWS_HOST>`, `<AWS_PORT>`, `<AWS_USER>` | AWS 자료 제공 서버 접속 정보 |
| `<AWS_DATA_ROOT>` | 읽기 허용할 자료 폴더 |
| `<GPU_SHARED_ROOT>` | GPU 계정의 공유 저장소 루트 |
| `<HELPER_ZIP>` | 관리자가 별도 제공하는 작은 도구 ZIP의 PC 경로 |
| `<SITE_NAME>` | 도구의 --list 출력에 있는 자료 이름 |

실행 도구 `fetch.py`, 실제 `manifest.json`, 확인된 공개 호스트 키 파일은 별도 전달한다. 공개 문서는 구체적인 연결 값이나 인증 파일을 포함하지 않는다.

## 3. 매 사용 시 순서

| 단계 | 실행 위치 | 할 일 |
|---|---|---|
| 1 | Windows PC | GPU 로그인 서버 접속 |
| 2 | GPU 로그인 서버 | 이번 사용의 전용 SSH 키 생성 |
| 3 | AWS 관리자 | 공개키를 자료 읽기 전용으로 등록 |
| 4 | Windows/GPU 로그인 서버 | 작은 도구 전달 및 연결 확인 |
| 5 | GPU 로그인 서버 | 원하는 자료 다운로드·SHA-256 확인 |
| 6 | 할당받은 GPU 노드 | 팀이 자료를 확인하고 학습·분석 |
| 7 | GPU/AWS 관리자 | 결과 보관 확인 후 자료·키 정리 및 권한 회수 |

## 4. 연결과 가져오기 명령

### Windows PowerShell: GPU 로그인

```powershell
ssh -p <GPU_PORT> <GPU_USER>@<GPU_HOST>
```

비밀번호는 터미널에서 직접 입력한다.

### GPU 로그인 서버: 새 키 만들기

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keygen -t ed25519 -f ~/.ssh/project_fetch_session -C project-fetch-session
chmod 600 ~/.ssh/project_fetch_session
cat ~/.ssh/project_fetch_session.pub
```

기존 같은 이름의 키가 있다면 덮어쓰지 말고 이전 작업 종료·권한 회수 여부를 먼저 확인한다. 공개키(.pub)만 관리자에게 전달한다. 개인키와 passphrase는 전달하지 않는다. 기존 AWS 관리용 개인키나 구글 인증 JSON은 GPU에 복사하지 않는다.

### AWS 관리자: 공개키 제한 등록

AWS의 rsync/rrsync 설치와 실행 경로를 확인한다. 기존 SSH 키를 보존하면서 새 키에 다음 제한을 붙여 등록한다. 이 문서의 예시는 실제 값으로 교체해야 한다.

```text
restrict,command="<RRSYNC_PATH> -ro <AWS_DATA_ROOT>" <SESSION_PUBLIC_KEY>
```

rrsync -ro는 지정 자료 폴더의 읽기만 허용한다. 셸·포트 포워딩·업로드·삭제 권한을 주지 않는다. 연결 시험은 SSH 셸 또는 SCP 대신 rsync로 한다.

### Windows PowerShell: 작은 도구 전달

```powershell
scp -P <GPU_PORT> <HELPER_ZIP> <GPU_USER>@<GPU_HOST>:~/project-transfer-tools.zip
```

### GPU 로그인 서버: 도구 준비

```bash
mkdir -p <GPU_SHARED_ROOT>/project/tools
python3 -m zipfile -e ~/project-transfer-tools.zip <GPU_SHARED_ROOT>/project/tools
cd <GPU_SHARED_ROOT>/project/tools/alo-gpu-transfer
python3 fetch.py --list
```

Python3.8 이상·SSH·rsync가 필요하다. GPU 시스템 패키지는 관리자 정책에 따라 준비한다. 저장소 전체 여유와 개인 quota를 각각 확인한다. 자료 용량은 별도 manifest로 확인하며 압축을 풀면 추가 공간이 필요하다.

키에 passphrase를 설정했다면 현재 셸에서 다음을 수행한다.

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/project_fetch_session
```

### GPU 로그인 서버: 연결만 확인

```bash
python3 fetch.py --key ~/.ssh/project_fetch_session --check-connection
```

이 단계는 ZIP을 다운로드하지 않는다. 호스트 키 불일치 시 관리자에게 확인하고, 호스트 키 검사를 끄지 않는다. GPU 네트워크에서 AWS SSH 포트로 나가는 연결이 허용돼야 한다.

### GPU 로그인 서버: 자료 다운로드

```bash
# 하나만 받기
python3 fetch.py --key ~/.ssh/project_fetch_session --site <SITE_NAME> --dest <GPU_SHARED_ROOT>/project/data

# 지정 자료 전체 받기
python3 fetch.py --key ~/.ssh/project_fetch_session --site all --dest <GPU_SHARED_ROOT>/project/data
```

파일을 .part로 받고 크기·SHA-256이 일치한 뒤 최종 ZIP으로 확정한다. 정상적인 기존 파일은 건너뛰고 다른 완료 파일을 덮어쓰지 않는다. 끊기면 같은 명령으로 이어받는다. 같은 목적지의 동시 실행은 잠금으로 거부한다.

기관의 로그인 서버 파일 전송 정책을 따른다. 모델 실행은 GPU 자원을 할당받은 뒤 수행한다. 다운로드 도구는 압축 해제·학습·DB 수정·결과 업로드를 하지 않는다.

## 5. 사용 후 정리

먼저 학습 결과와 체크포인트를 별도로 보관했는지 확인한다. 전송 중인 작업·키를 사용 중인 프로세스가 없는지 확인한다.

- GPU: 이번에 받은 자료의 정확한 경로를 확인하고 해당 자료만 정리한다. 공유 계정 전체 디렉터리를 지우지 않는다.
- GPU: ssh-agent에서 이번 키를 제거하고 이번 개인키와 .pub을 정리한다.
- AWS 관리자: authorized_keys에서 이번 공개키와 일치하는 항목만 제거한다. 다른 관리자/팀원의 키는 유지한다.
- AWS 원본 ZIP은 유지한다. GPU를 정리해도 다음에 다시 받을 수 있다.

자료 삭제는 위치와 작업 종료를 확인한 뒤 수행해야 하므로, 이 문서에는 포괄적인 삭제 명령을 넣지 않았다. 기존 도구에 자동 자료 삭제·키 회수 기능은 없다.

키에 대한 agent 등록 제거 명령은 다음과 같다. 이것만으로 키 파일이나 AWS 등록이 삭제되는 것은 아니다.

```bash
ssh-add -d ~/.ssh/project_fetch_session
```

자료까지 매번 지우면 다음 사용 때 전량을 다시 내려받는다. GPU가 공유 계정이면 같은 계정의 팀원도 개인키 파일에 접근할 수 있으므로 원격 권한을 자료 읽기로 제한한다.

## 6. 다른 에이전트에게 전달할 요청

```text
GPU 로그인 서버에서 AWS 자료를 직접 가져오도록 도와줘.
공개 문서 GPU_TRANSFER.md와 관리자가 별도로 전달한 실제 설정/도구를 먼저 확인해.
매 사용마다 새 전용 키를 만들고 공개키만 AWS 관리자에게 전달해 읽기 전용으로 등록해.
연결 확인 후 허용된 자료만 가져오고 크기와 SHA-256이 맞아야 완료로 보고해.
비밀번호와 passphrase는 내가 터미널에서 직접 입력하게 해.
AWS 관리용 개인키/구글 인증 키를 GPU에 보내거나 실제 값을 공개 Git에 기록하지 마.
같은 저장 경로의 중복 작업, 다른 팀원의 파일/작업, 기존 정상 ZIP을 건드리지 마.
GPU 모델 실행·결과 업로드·자료 삭제·키 회수를 다운로드 완료로 묶어서 자동 수행하지 마.
사용 후 정리는 결과 보관과 정확한 대상 확인을 거쳐 진행하고 AWS 공개키도 회수해.
실제 연결하지 않았다면 준비/안내 단계라고 명시하고 남은 일을 보고해.
```

## 7. 확인 범위

AWS 저장과 가져오기 도구 작성은 완료했지만 GPU 실연결·다운로드·개인 quota와 네트워크 정책은 미확인이다. 매 사용 키 등록·회수와 자료 정리 자동화도 미구현이다.

참고: [rsync 공식 문서](https://download.samba.org/pub/rsync/rsync.1), [rrsync 읽기 전용 제한](https://download.samba.org/pub/rsync/rrsync.1).

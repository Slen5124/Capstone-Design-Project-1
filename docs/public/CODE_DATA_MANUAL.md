# 4. 코드, 데이터 구축 매뉴얼

본 절은 AloYoga 공사 모니터링 시스템의 백엔드 코드 설치, 데이터베이스 초기화, 파일 저장 및 GPU 자료 전송 절차를 설명한다. 구축 담당자는 현장·촬영 장소·공정표를 등록하고, 외부 GPU 환경에서 생성한 모델 가중치와 일일 분석 결과를 동일한 기준으로 저장할 수 있다.

문서 작성 기준일은 2026년 10월 15일이다. 서버 배포와 데이터 전송 상태는 기존 구축 기록을 기준으로 구분하며, 운영 서버에 초기화 명령을 다시 실행하지 않는다.

## 4.1 시스템 구성과 코드 구조

### 가. 구성 요소

| 구성 요소 | 사용 기술 및 역할 |
|---|---|
| 백엔드 서버 | AWS Lightsail의 Ubuntu 환경에서 API와 파일 저장소 운영 |
| 데이터베이스 | PostgreSQL 16에 현장·촬영 장소·공정표·파일 메타데이터 저장 |
| API | FastAPI와 Uvicorn으로 등록·조회·다운로드 요청 처리 |
| DB 접근 | SQLAlchemy 2.x Core로 테이블 조회 및 트랜잭션 처리 |
| 파일 저장소 | 서버 디스크에 사진·JSON·Markdown·PDF·ZIP·가중치 보관 |
| 외부 GPU 환경 | 기관의 SSH·Slurm 환경에서 모델 학습·추론 수행 |

DB에는 파일 자체 대신 상대 경로, 크기, SHA-256 및 등록 상태를 보관한다. 대용량 자료는 파일 저장소에 두고 파일 ID로 연결하므로, 현장·장소·날짜·모델을 기준으로 조회한 뒤 해당 원본을 가져올 수 있다.

### 나. 주요 코드

~~~text
src/backend/
  main.py                 API 시작점과 공정표 조회
  db.py                   PostgreSQL 연결과 기존 테이블 읽기
  records.py              파일·GT·모델·일일 기록 등록
  storage.py              저장 경로·파일 형식·체크섬 검사
  comparison.py           이전 관측과 현재 JSON의 차이 비교
  drive.py                Google Drive 원본 ZIP 직접 수신
  import_schedule.py      검수된 공정표의 초기 일괄 입력
  schedule_api.py         공정표 추출 준비·검증·등록 API
  schedule_ingestion.py   공정표 구조 및 근거 검증
  sql/001_initial.sql     테이블 10개와 제약 조건 생성
~~~

백엔드 전체 소스와 전송 도구는 권한이 있는 개발자에게 별도로 제공한다. 현재 공개 문서 브랜치에는 초기 SQL과 공개 설명서만 포함되므로, 해당 브랜치만 내려받아서는 API를 실행할 수 없다. 실제 서버 IP, 접속 키, DB 비밀번호와 Drive 인증 파일은 제출 문서 및 공개 코드에 포함하지 않는다.

<!-- pagebreak -->

## 4.2 백엔드 구축 절차

### 가. 실행 환경 준비

PostgreSQL 16과 Python 3.11 이상이 설치된 Ubuntu 서버를 준비한다. Python 버전은 코드에서 사용하는 hashlib.file_digest 등을 기준으로 한다. 백엔드 폴더를 서버의 ~/alo-backend에 배치한 뒤 다음 명령을 실행한다.

~~~bash
cd ~/alo-backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
~~~

requirements.txt에는 FastAPI, Uvicorn, SQLAlchemy, psycopg, google-auth, requests와 PDF 처리 라이브러리가 정의돼 있다. 이 파일은 지원 버전 범위이므로, 설치에 성공한 환경에서는 python -m pip freeze > requirements.lock.txt로 정확한 설치 버전을 별도 기록한다.

### 나. 새 DB 초기화

다음 명령은 기존 자료가 없는 신규 환경에서만 실행한다. DB_USER와 DB_NAME은 실제 DB 계정 및 DB 이름으로 바꾸며 db.py의 연결 설정과 일치시킨다. 비밀번호는 입력 프롬프트에서 설정한다.

~~~bash
sudo -u postgres createuser --pwprompt "<DB_USER>"
sudo -u postgres createdb --owner="<DB_USER>" "<DB_NAME>"
psql -h 127.0.0.1 -U "<DB_USER>" -d "<DB_NAME>" -W \
  -v ON_ERROR_STOP=1 --single-transaction -f sql/001_initial.sql
~~~

SQL 실행으로 테이블 10개, 외래키, 유일성 제약, 인덱스와 공정표 선후 관계 검증 트리거가 생성된다. 기존 운영 DB에는 해당 SQL을 재실행하지 않는다. API는 생성된 테이블을 읽어 사용하며 테이블을 자동 생성하거나 변경하지 않는다.

### 다. 저장소 설정과 API 실행

~~~bash
export ALO_STORAGE_ROOT="$HOME/alo-data"
mkdir -p "$ALO_STORAGE_ROOT"
read -r -s -p "DB password: " DB_PASSWORD
export DB_PASSWORD
python -m uvicorn main:app --host 127.0.0.1 --port 8000
~~~

DB 연결은 서버 내부 127.0.0.1:5432를 사용한다. 비밀번호는 코드에 직접 쓰지 않는다. 위 명령은 신규 환경의 수동 실행 절차이며, systemd로 운영 중인 서버에서 Uvicorn을 중복 실행하면 포트가 충돌한다.

상시 운영은 동봉된 SERVICE.md, run_service.py와 alo-backend.service를 따른다. systemd는 보호된 자격 증명 파일을 LoadCredential로 전달한다. Drive 허용 목록과 Drive 인증 자격 증명은 별도 설정하며, 비밀 파일을 제외한 서비스 정의만으로 접속이 완료되지는 않는다. 현재 서버는 systemd 등록 상태이므로 운영 시 서비스 상태와 /health 응답부터 확인한다.

<!-- pagebreak -->

## 4.3 데이터베이스와 파일 저장 구조

### 가. 테이블 구성

| 테이블 | 주요 정보 | 연결 대상 |
|---|---|---|
| sites | 현장 코드·이름·시간대 | 촬영 장소와 공정표 |
| locations | 현장 내 고정 촬영 위치 | 현장과 일일 기록 |
| files | 상대 경로·종류·크기·SHA-256·상태 | 원본과 결과 파일 |
| gt_datasets | ZIP·manifest·라벨 버전·검수 상태 | 학습용 자료 |
| model_versions | 모델 종류·버전·가중치·설정·클래스 | GT와 일일 기록 |
| schedules | 현장 ID·공정표 버전·문서 날짜 | 원본 PDF와 작업 |
| schedule_sources | 공정표 ID·원본 파일 ID·표시 순서 | 여러 원본 PDF |
| schedule_tasks | 원본 ID·순서·WEEK·작업명·기간·선후 ID | 공정표와 작업 |
| schedule_task_locations | 작업 ID·촬영 장소 ID | 공정 작업과 관측 위치 |
| daily_records | 관측일·촬영 시각·모델·파일 ID·직전 관측 | 장소별 하루 관측 |

각 엔터티의 고유 ID는 UUID를 사용한다. 원본 간트의 작업 번호는 source_task_id, 화면 정렬 순서는 display_order로 분리한다. 공정표 버전은 schedules에 두고 작업은 schedule_id로 연결한다. 작업과 촬영 장소의 연결은 schedule_task_locations에 저장한다.

### 나. 파일 저장 경로

아래는 ALO_STORAGE_ROOT를 기준으로 하는 저장 규칙 예시다. 모든 폴더와 파일이 이미 생성됐다는 의미는 아니다.

~~~text
sites/brea/schedule/v001/originals/gantt.pdf
sites/brea/daily/2026-10-15/sales-floor-01/r001/
  photo.jpg   result.json   changes.json   report.md
gt/source-archives/brea/v001/archive.zip
gt/datasets/brea/v001/
  archive.zip   manifest.json
models/unet/v001/
  weights.pth   config.json   classes.json   metrics.json
~~~

원본 GT 후보 ZIP과 라벨·분할을 검수한 학습용 데이터셋은 구분한다. 일일 운영 자료도 GT와 별도로 관리한다. 모델 가중치의 확장자는 예시이며 실제 학습 코드의 저장 형식에 따른다. 기존 공정표의 schedules/ 복수형 경로도 지원하므로, 등록된 파일의 실제 경로는 files.relative_path를 따른다.

파일 리비전 폴더 r001은 바이트 보관 경로를 구분하기 위한 예시다. 같은 장소·날짜에 일일 기록을 여러 건 등록하거나 기존 관측을 덮어쓰는 기능을 의미하지 않는다.

<!-- pagebreak -->

## 4.4 데이터 등록 및 검증 절차

### 가. 원본 자료와 GT 구축

Google Drive의 지정 ZIP을 서버가 직접 수신한다. 관리자는 서비스 계정에 선택 파일의 읽기 권한을 부여하고 허용 목록을 설정한다. /drive/connection으로 접근을 확인한 뒤 전송하고 /drive/transfers에서 상태를 조회한다. 원본을 PC로 내려받아 다시 올리지 않는다.

서버는 크기·Google MD5·SHA-256·ZIP 형식을 확인한 후 files에 ready로 기록한다. 서버에서는 압축을 풀지 않는다. 기존 구축 기록의 대상은 ZIP 5개, 총 21,705,669,104바이트이며 현재 상태는 API로 확인한다.

학습용 GT는 ZIP과 manifest를 등록한 뒤 /gt-datasets로 연결한다. 이름·버전·라벨 스키마·ZIP 체크섬이 일치해야 한다. 등록은 draft에서 시작한다. reviewed와 학습 승인을 구분하며, 내부 라벨·클래스·분할 누수 검증 전에는 approved 전환을 차단한다.

### 나. 모델과 가중치 등록

학습과 성능 비교는 외부 GPU 환경에서 수행한다. 백엔드는 U-Net·Mask R-CNN·DeepLab V3와 ResNet-50 백본 정보를 관리한다. 가중치·설정·클래스 및 선택적인 성능 파일을 먼저 등록하고 /model-versions에서 연결한다.

모델 버전, 학습 시각, 코드·라이브러리 버전을 기록한다. gt_dataset_id를 지정하면 승인된 GT여야 한다. 모델 등록 기능과 실제 학습·성능 검증 완료는 구분한다.

### 다. 공정표 등록

최초 검수본은 import_schedule.py로 원본 PDF와 작업 JSON을 입력한다. 원본 ID·표시 순서·WEEK·작업명·기간을 보존하고 관계 ID를 UUID로 변환한다. 선행·후행은 각각 최대 한 개이며 상호 연결과 순환을 검사한다. WEEK를 달력 주차로 재계산하지 않는다.

새 공정표는 원본 업로드 → 추출 준비 → 외부 결과 수신 → 검증 → 등록 순서로 처리한다. 관계를 confirmed·none·unknown·conflict로 구분하고 판독 근거를 보관한다. 미확인을 관계 없음으로 처리하거나 복수 관계를 하나로 축소하지 않는다. 외부 모델 호출과 하네스의 운영 배포는 후속 작업이다.

### 라. 일일 사진과 분석 결과 등록

현장·촬영 장소 등록 후 장소별 현지 날짜당 대표 사진 한 장을 사용한다. 외부 모델의 결과 JSON과 날짜별 Markdown을 파일로 저장하고 /sites/{site_code}/daily-records에 연결한다.

직전 완료 관측과의 변화는 json-v001 규칙으로 비교한다. 객체의 추가·삭제·변경을 기록하고 배열은 순서가 있는 전체 값으로 비교한다. changes.json 제출 시 모델 버전·스키마·단위·클래스 및 실제 비교 결과와의 일치를 검사한다. 모델 실행과 Markdown 자동 생성은 현재 API에 포함되지 않는다.

첫 관측에는 이전 비교가 없다. 완료 기록은 사진·모델·결과 JSON·Markdown을 요구한다. 중복 날짜와 기존 관측 연결을 무효화하는 과거 삽입은 거부한다. JSON 차이를 공정률 또는 공사 완료 판정으로 간주하지 않는다.

<!-- pagebreak -->

## 4.5 GPU 서버로 학습 자료 전송

### 가. 전달 자료와 접속 준비

개발자는 비공개 패키지를 풀고 README.md·전송 도구 ZIP·승인된 SSH 키를 확인한다. PEM은 서버 접속용 개인키이므로 공개 보고서와 Git에 포함하지 않는다.

~~~text
PC에서 패키지 압축 해제
  → 작은 전송 도구 ZIP과 승인된 키를 GPU 로그인 서버로 전송
  → GPU에서 도구 준비 및 AWS 접근 확인
  → AWS 원본 ZIP을 GPU 공유 저장소로 직접 다운로드
  → 크기와 SHA-256 확인 후 학습 자료 검토
~~~

기관의 GPU 계정·접속 권한을 준비하고 제공 README의 PowerShell 절차로 도구와 키를 보낸다. 키는 소유자만 읽도록 설정한다. GPU 계정을 공유하면 같은 계정 사용자 간 접근은 파일 권한만으로 분리되지 않는다.

### 나. 도구 실행과 다운로드

GPU 로그인 서버에서 도구를 풀고 fetch.py 폴더로 이동한다. Python 3.8 이상·SSH·rsync가 필요하다. 아래 변수는 실제 키 경로·AWS 원본 디렉터리·이번 실행의 새 저장 폴더로 바꾼다.

~~~bash
KEY="/private/session/key.pem"
AWS_ARCHIVES="/absolute/aws/archive/directory"
ALO_RUN="/shared/alo/runs/this-session"
chmod 600 "$KEY"
python3 fetch.py --list
python3 fetch.py --key "$KEY" \
  --remote-root "$AWS_ARCHIVES" --check-connection
python3 fetch.py --key "$KEY" --remote-root "$AWS_ARCHIVES" \
  --site brea --dest "$ALO_RUN/archives"
~~~

위 예시는 일반 SSH 접속이 허용된 관리자 키를 사용하는 방식이다. rrsync로 지정 디렉터리만 읽도록 제한한 키는 --remote-root를 생략한다. 전체 자료가 필요하면 --site brea를 --site all로 변경한다.

다운로드는 AWS에서 GPU로 직접 진행한다. 파일 크기와 SHA-256 검증 완료 메시지를 확인한 뒤 사용한다. 전송이 끊기면 같은 저장 경로로 재시도하고, .part 파일이나 다운로드 잠금을 임의로 지우지 않는다. 호스트 키 검사를 끄지 않으며, 파일시스템 여유 공간 외에 개인 quota와 압축 해제 공간도 확인한다.

### 다. 자료 확인과 학습 환경 연결

다운로드한 ZIP의 내부 경로·사진과 라벨 대응·클래스·분할을 확인한 뒤 새 폴더에 해제한다. 기관 정책에 따라 Slurm으로 GPU를 할당받고 별도의 팀 학습 코드를 실행한다. 로그인 서버에서는 학습하지 않는다.

가중치·설정·클래스·성능 결과는 백엔드에 등록한다. 전송 도구는 학습 제출과 결과 자동 업로드를 수행하지 않는다. 다운로드 후 GPU에 복사한 키를 제거하고 AWS 원본과 다른 사용자의 자료는 보존한다.

<!-- pagebreak -->

## 4.6 운영 확인과 관리 기준

### 가. 구축 결과 확인

구축 후에는 프로세스 실행 여부와 실제 요청 성공 여부를 각각 확인한다. 운영 서버의 서비스 상태와 API 응답을 조회한다.

~~~bash
sudo systemctl status alo-backend --no-pager
curl http://127.0.0.1:8000/health
~~~

/health에서 status가 ok, database가 connected인지 확인한다. PC에서는 SSH 터널을 유지한 상태로 /docs에 접속한다. 기관과 서버의 실제 접속값은 별도 제공한다.

~~~powershell
ssh -i "개인키.pem의 경로" -N -L 127.0.0.1:18000:127.0.0.1:8000 "<AWS_USER>@<AWS_HOST>"
~~~

브라우저 주소는 http://127.0.0.1:18000/docs다. PostgreSQL에 직접 접속하는 경우에는 DB 계정·비밀번호와 5432번 포트용 터널을 별도로 준비한다.

| 확인 항목 | 정상 또는 완료 기준 |
|---|---|
| API와 DB 연결 | /health의 database가 connected |
| 공정표 | 현장·버전·작업·원본 PDF가 연결되고 순서·WEEK 조회 가능 |
| 파일 | ready 상태이며 실제 크기와 SHA-256이 등록값과 일치 |
| 일일 관측 | 현장·장소·현지 날짜·모델·필수 파일이 일관되게 연결 |
| GPU 다운로드 | 선택한 ZIP의 크기·SHA-256 검증 완료 |
| 실패 처리 | 오류 상태를 보존하고 실패 원인 확인 후 재시도 |

### 나. 구현 범위와 후속 작업

기존 구축 기록에서 PostgreSQL 테이블 생성, FastAPI 배포·DB 연결, systemd 서비스 재시작과 지정 원본 ZIP의 서버 저장이 확인됐다. 파일·GT·모델·일일 기록 등록 API와 공정표 조회 기능을 구현했으며 GPU 전송 도구와 비공개 개발자 안내를 준비했다.

GPU 실제 전송·모델 학습·일일 자동 실행·결과 자동 업로드는 통합 확인 대상이다. 공정표 추출 하네스의 운영 배포, GT 내부 검증 및 승인, 프론트엔드와 에이전트 연동도 후속 작업에 해당한다. 서버 밖 백업과 복구, 전체 인스턴스 재부팅 및 대용량 장애 복구 검증은 별도로 수행해야 한다.

### 다. 보안과 데이터 관리

API와 PostgreSQL은 서버 내부 주소에만 바인딩한다. 외부에서 사용하려면 SSH 터널을 이용하며, 공개 서비스로 전환할 경우 인증과 HTTPS를 먼저 구성한다. 관리자 키·DB 비밀번호·Drive 인증 파일은 공개 코드 및 제출 자료에서 제외한다.

DB의 ready와 일일 관측의 completed는 서로 다른 상태다. ZIP이 정상 전송됐다고 GT 검수나 모델 성능 검증이 완료된 것으로 간주하지 않는다. 파일과 DB는 신규 버전과 새 경로로 등록하고, 원본 및 기존 운영 자료를 덮어쓰거나 초기화하지 않는다.

등록 요청의 필드와 응답은 운영 API의 /docs를 따른다.

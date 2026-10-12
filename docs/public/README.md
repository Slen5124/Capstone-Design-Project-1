# 백엔드·GPU 자료 전송 문서

2026-10-12 기준 구현 상태와 운영 절차를 정리한 Git 공개용 문서 및 DB 스키마다.

| 문서 | 내용 |
|---|---|
| [백엔드 기능과 DB 구조](BACKEND.md) | FastAPI·SQLAlchemy·PostgreSQL 역할, 파일 저장, 구현 범위 |
| [GPU 연결과 자료 가져오기](GPU_TRANSFER.md) | 매 사용마다 새 키 연결, 직접 다운로드, 검사, 사용 후 정리 |
| [폴더 트리·ERD 참고도](../diagrams/README.md) | SVG 미리보기·텍스트/Mermaid·현재 SQL과의 차이 |
| [PostgreSQL 초기 스키마](../../src/backend/sql/001_initial.sql) | 테이블 10개·관계·인덱스·트리거·검증 제약 |

## 공개 범위

서버 IP·호스트 이름·로그인 계정·AWS 계정 식별자·개인 이메일·PC 경로·Drive 파일 ID/링크·실제 키·비밀번호·토큰·파일별 체크섬을 넣지 않았다. 실제 운영 설정, 데이터, 실행 도구 ZIP은 별도로 관리한다. SQL은 실제 데이터나 접속 비밀번호 없이 구조만 포함한다.

문서의 `<AWS_HOST>` 같은 값은 입력해야 하는 자리표시자다. 실제 설정값으로 바꾸기 전에는 명령을 실행하지 않는다. 실행 파일과 manifest, 알려진 SSH 호스트 키는 관리자가 별도로 제공한다.

## 확인된 상태

- PostgreSQL 초기 SQL은 운영 DB에 이미 적용됐고 테이블이 생성돼 있다.
- FastAPI·SQLAlchemy 구현과 AWS 배포를 완료했으며 DB 연결을 확인했다.
- Google Drive 서버 인증 연결 완료.
- 지정 ZIP를 AWS에 직접 저장하고 파일 크기·체크섬을 확인함.
- GPU 가져오기 도구와 절차 작성 완료.
- GPU의 읽기 전용 키 등록·실제 다운로드·사용 후 키 회수 자동화는 미완료.
- 모델 실행은 팀의 GPU 환경에서 담당함.

## SQL 적용 범위

초기 스키마는 PostgreSQL16의 **새 빈 DB**에 한 트랜잭션으로 적용한다. 기존 운영 DB에는 다시 실행하지 않는다. DB/로그인 역할 생성과 비밀번호 설정은 SQL에 포함하지 않았으며 운영자가 별도로 준비한다.

```bash
psql -h <DB_HOST> -U <DB_USER> -d <DB_NAME> -W -v ON_ERROR_STOP=1 --single-transaction -f src/backend/sql/001_initial.sql
```

비밀번호는 프롬프트에서 입력한다. SQL의 테이블 제약만으로 ZIP 내부 라벨 검수나 파일 바이트 검증, 에이전트 연동이 구현되는 것은 아니다. 운영 DB 적용은 이미 완료됐다. 이번 Git 공개 준비에서는 기존 DB에 다시 적용하거나 별도 새 DB를 만들어 실행하지 않았다.

## 이 브랜치의 범위

공개 문서와 초기 DB SQL을 공유하는 브랜치다. FastAPI·SQLAlchemy 구현 코드는 로컬과 운영 서버에 있지만 이 공개 브랜치에는 포함하지 않았다. 이 브랜치만 받아서는 API 서비스를 실행할 수 없다.

## 구조도 바로 보기

- [전체 폴더 트리](../diagrams/directory-tree.md)
- [운영 데이터 ERD](../diagrams/erd-operations.svg)
- [공정표 ERD](../diagrams/erd-schedule.svg)
- [GT·모델 ERD](../diagrams/erd-training-models.svg)

그림은 이전 설계 참고용이며 현재 구현과의 차이를 [그림 안내](../diagrams/README.md)에 정리했다.

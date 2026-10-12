
# Aloha AI Agent — RAG 기초 구현 및 동작 검증

## 1. 프로젝트 개요

본 폴더는 Aloha 팀의 「AI 기반 글로벌 리테일 매장 공사관리 지원 시스템 개발」 프로젝트에서 AI 에이전트의 RAG(Retrieval-Augmented Generation, 검색 증강 생성) 파이프라인을 검증하기 위한 예제 코드입니다.

현장 보고서와 공정 데이터를 검색하고, 검색된 근거를 바탕으로 LLM이 영어 답변을 생성하는 시스템을 목표로 합니다.

현재는 실제 현장 데이터가 아닌 가상 테스트 데이터를 이용하여 기본 기능을 검증했습니다.

### 사용 기술

| 기술 | 역할 |
|---|---|
| Ollama | 로컬 LLM 및 임베딩 모델 실행 |
| bge-m3 | 텍스트를 1024차원 임베딩 벡터로 변환 |
| ChromaDB | 벡터 저장 및 메타데이터 기반 검색 |
| Qwen3-1.7B | 검색 근거를 활용한 영어 답변 생성 |
| Python 3.12 | RAG 파이프라인 구현 |

현재 검증에 사용한 LLM은 `qwen3:1.7b`이며, 향후 GPU 서버에서 `qwen3.5:9b`를 평가할 예정입니다.

---

## 2. 파일 구성 및 역할

| 파일명 | 역할 |
|---|---|
| `chroma_probe.py` | ChromaDB의 벡터 저장·갱신·검색 기능을 독립적으로 검사 |
| `test_rag_search.py` | bge-m3 임베딩 생성, ChromaDB 저장 및 검색 |
| `test_rag_answer.py` | 검색된 근거를 Qwen에 전달하여 영어 답변 생성 |
| `requirements.txt` | Python 패키지 의존성 목록 |

기존 `src/llm_agent_module/rag_agent.py`는 별도의 Mock 구현이며, 본 예제 실행에는 사용하지 않습니다.

### 현재 구현된 RAG 흐름

사용자 질문 → bge-m3 임베딩 → ChromaDB 벡터 검색 → 검색 근거 반환 → Qwen3-1.7B → 영어 답변 생성

현재는 Python 코드에서 검색과 LLM 호출 순서를 직접 제어합니다. LLM이 자율적으로 도구를 선택하는 Agentic RAG는 아직 구현되지 않았습니다.

---

## 3. 실행 환경

- 운영체제: Windows 11에서 검증
- Python: 3.12
- ChromaDB: 1.5.9
- Ollama: 로컬 API 사용
- 임베딩 모델: `bge-m3`
- 생성 모델: `qwen3:1.7b`

Ollama 기본 API 주소:

`http://127.0.0.1:11434`

### 사전 준비

Python 3.12와 Ollama를 설치한 후, 팀 저장소 루트에서 PowerShell을 열고 `examples` 폴더로 이동합니다.

```powershell
cd src\llm_agent_module\examples
```

Python 가상환경 생성:

```powershell
py -3.12 -m venv .venv
```

필요 패키지 설치:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Ollama 모델 다운로드:

```powershell
ollama pull bge-m3
ollama pull qwen3:1.7b
```

설치된 모델 확인:

```powershell
ollama list
```

Ollama가 실행 중이어야 합니다.

---

## 4. 실행 방법

반드시 `examples` 폴더에서 실행합니다.

### 4-1. ChromaDB 독립 테스트 (선택)

```powershell
.\.venv\Scripts\python.exe chroma_probe.py
```

주요 기능:

- 테스트 벡터 2개 생성
- ChromaDB에 벡터 저장
- 동일 ID로 재저장
- 벡터 검색 결과 확인

정상 출력:

```text
PASS: save, update, search
```

이 테스트는 Ollama나 bge-m3 없이 ChromaDB 자체의 동작을 검사합니다.

실행 시 임시 DB 폴더가 생성되며 자동으로 삭제되지 않습니다.

### 4-2. bge-m3 및 ChromaDB 검색 테스트

```powershell
.\.venv\Scripts\python.exe test_rag_search.py
```

실행 과정:

1. 가상 영어 문서 3개와 질문 준비
2. Ollama에서 bge-m3 임베딩 생성
3. 1024차원 벡터를 ChromaDB에 저장
4. 매장 및 주차 메타데이터 조건 검색
5. 검색 결과 확인

정상 출력의 핵심 부분:

```text
EMBEDDING_DIM: 1024
2. Chroma storage complete. DOCUMENT_COUNT: 3
PASS: bge-m3 -> Chroma -> search
```

### 4-3. Qwen 영어 답변 생성 테스트

먼저 4-2 검색 테스트를 실행해야 합니다.

```powershell
.\.venv\Scripts\python.exe test_rag_answer.py
```

실행 과정:

1. 사용자 질문을 bge-m3로 임베딩
2. 기존 ChromaDB 컬렉션에서 관련 문서 검색
3. 검색된 문장을 Qwen3-1.7B에 전달
4. 영어 답변 생성

테스트 질문:

> What is the electrical progress at Demo Store A in Week 7?

예상 답변:

> The electrical progress at Demo Store A in Week 7 is 45%.

정상 출력:

```text
PASS: Chroma -> Qwen answer
```

실제 답변 표현은 모델 실행 결과에 따라 달라질 수 있습니다.

---

## 5. 테스트 데이터

현재 예제는 실제 Alo Yoga 현장 데이터가 아닌 가상 데이터를 사용합니다.

| 가상 매장 | 주차 | 공종 | 진행률 |
|---|---|---|---|
| Demo Store A | W6 | Electrical | 30% |
| Demo Store A | W7 | Electrical | 45% |
| Demo Store B | W7 | Electrical | 60% |

실제 공사 보고서, 도면, 현장 사진 및 비공개 데이터는 포함하지 않습니다.

테스트용 ChromaDB 저장 위치:

`examples/data/chroma_demo/`

해당 DB는 실행 과정에서 생성되며 Git에 커밋하지 않습니다.

---

## 6. 현재 구현 및 검증 범위

### 구현 완료

- Ollama 로컬 API 연결
- bge-m3 임베딩 생성
- 1024차원 벡터 생성 확인
- ChromaDB 벡터 저장 및 검색
- 매장·주차 기반 메타데이터 필터링
- Qwen3-1.7B 영어 답변 생성
- 테스트 데이터 기반 RAG 파이프라인 연결 검증

### 아직 구현되지 않은 기능

- 실제 PDF 보고서 자동 전처리 및 검색
- 실제 현장 데이터 기반 검색 정확도 평가
- Hybrid Search 및 Reranking
- Backend API 연동
- Vision 분석 결과 연동
- 자율적인 Tool Calling
- 근거 사진 기반 영어 리포트 자동 생성
- Qwen3.5-9B 서버 실행 및 성능 평가

테스트 결과의 `PASS`는 해당 파이프라인이 정상 실행되었다는 의미입니다.

검색 정확도나 답변의 사실적 정확성이 자동으로 검증되었다는 의미는 아닙니다.

---

## 7. 향후 개발 계획

1. 실제 영어 주간 공사 보고서의 텍스트 및 표 추출
2. 매장·주차·공종·문서 페이지 메타데이터 구축
3. ChromaDB 검색 정확도 평가
4. Hybrid Search 및 Reranker 적용 검토
5. Backend API를 통한 공정률·일정·RFI 조회
6. Vision 분석 결과와 현장 사진 근거 연결
7. Qwen3.5-9B GPU 서버 실행 및 평가
8. 근거 기반 영어 공사관리 리포트 생성

---

## 8. 보안 및 Git 관리

다음 파일과 데이터는 Git에 커밋하지 않습니다.

- Python 가상환경 (`.venv`, `.venv312`)
- ChromaDB 로컬 데이터
- 실제 현장 보고서 및 사진
- 비공개 공정표와 도면
- API 키, 비밀번호 및 인증정보
- 모델 가중치
- 디버깅 로그

현재 예제에는 공개 가능한 가상 테스트 데이터만 사용합니다.

실제 현장 데이터를 사용할 때는 팀의 데이터 공유 정책과 보안 기준을 확인해야 합니다.

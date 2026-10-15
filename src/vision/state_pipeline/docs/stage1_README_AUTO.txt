자동 전처리 v3 — 실행 안내

설치 위치: D:\work\project\전처리된 데이터
Python 환경: 위 폴더의 .venv (프로젝트용 독립 가상환경)
실행 파일: run_auto_preprocess.ps1
프로그램: qa_v3\auto_preprocess.py
대표 기준: automatic_reference_v3\project.json

1. 전체 실행
PowerShell에서 다음 명령을 실행합니다.

Set-Location -LiteralPath 'D:\work\project\전처리된 데이터'
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_auto_preprocess.ps1

암호는 실행 중 ZIP password 프롬프트로 입력합니다. 기존 QA_ZIP_PASSWORD
환경변수가 있으면 그 값을 사용합니다. 코드·설정·결과 파일에는 저장하지 않습니다.
웹 화면 실행이나 사진별 승인 없이 배치 처리가 진행됩니다.

2. 작은 실행
선별 대표 12장과 벽 누락 진단 사진 BR08, 합계 13장:
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_auto_preprocess.ps1 -Mode Smoke

각 현장 ZIP에서 순서대로 분산 선별한 100장:
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_auto_preprocess.ps1 -Limit 100

소수 사진에서 JSON을 낱개 파일로 받고 싶은 경우:
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_auto_preprocess.ps1 -Mode Smoke -Layout files

3. 실제 자동 처리
- LabelMe JSON·이미지 연결·실제 크기 확인, 손상 사진/JSON 분류
- 잘못된 폴리곤을 원래 그려지는 픽셀을 보존하는 유효 폴리곤 조각으로 변환
  전체 원본 픽셀 IoU >= 0.995, 좌표 보정 <= 2픽셀 조건; 실패하면 원래 도형 유지
- 동일 도형·동일 속성의 중복은 하나로 정리
- 라벨이 다른 중복은 대표 벽/천장 기준의 지지가 충분할 때 맞는 것을 남김
- 대표사진 12장의 가시 영역을 같은 현장 사진에 정합하여 벽·천장 의미 검사
- 기준 영역 내부에 충분히 포함된 잘못된 벽/천장 라벨을 자동 변경
- 누락이 큰 벽/천장 영역을 기준 폴리곤으로 자동 추가
  기본 가시 기준 영역의 해당 라벨 커버리지가 60% 미만일 때 복원을 판단
- 다른 사진에는 특징점 정합 + 표면 유사성 + 가림/변화 검사를 적용
- 전체 면이 실패해도 통과한 부분 영역은 자동 복원; 가림·변화 구간은 제외
- 각 단계에서 판단하기 어려운 문제는 수정 가능한 부분을 처리한 뒤 사유 기록

작은 진단 ROI는 전체 벽을 만드는 근거로 쓰지 않습니다.
복원 기준은 별도로 작성한 가시 표면 31개(벽 26, 천장 5)입니다.
이 기준은 요청에 따라 기존 원본을 보고 만든 AI 초안이며 사람 확정 GT가 아닙니다.
12장은 전체 구도 종류의 개수나 전 사진에 적용 가능한 정답 수를 뜻하지 않습니다.
벽·천장 이외의 construction materials/tools 등은 별도 의미 기준이 없는 경우
겹쳤다는 이유만으로 어느 라벨이 맞는지 결정하지 않습니다.

4. 최종 저장 위치와 분류
실행마다 새 폴더:
D:\work\project\전처리된 데이터\automatic_results\run_실행시각\

기본 출력은 공간 절약을 위해 다음 네 ZIP 안에 JSON을 저장합니다.
annotations\auto_fixed.zip   자동 수정했고 설정한 검사에서 미해결 문제 없음
annotations\kept.zip         어노테이션 변경 없이 설정한 검사에서 미해결 문제 없음
annotations\needs_review.zip 가능한 자동 수정 완료 + 남은 문제/적용 근거 부족
annotations\rejected.zip     손상·연결 불가 등; 유효 JSON이 없으면 목록만 기록

needs_review는 처리를 기다리는 원본이 아닙니다. 실제 수정본이 저장된 분류입니다.
auto_fixed/kept도 사람의 시각적 정답 확정이나 정확도 보증을 뜻하지 않습니다.
분류는 사진-JSON 처리 항목 단위입니다. JSON이 여러 개면 사진 하나가 여러 행입니다.
손상 사진이나 읽지 못하는 JSON에는 유효한 수정 JSON을 만들어내지 않습니다.

각 JSON ZIP 안 경로는 P0~P3/원본이름__고유ID.json입니다.
- classification.csv: UTF-8 BOM; 사진 ZIP/내부 경로, 원본 JSON, 수정본, 분류, 사유
- manifest.jsonl: 사진/JSON SHA256, 실행한 수정, 기준사진, 정합·커버리지 증거
- summary.json: 실행 범위, 처리 수, 자동 수정 수, 남은 문제, 완료/중단 여부

-Layout files를 쓰면 annotations\분류\P0~P3\*.json에 낱개 저장합니다.
이미지 RGB는 수정하거나 복제하지 않습니다. 원본 ZIP + 수정 JSON + 연결 목록이
최종 자동 전처리 데이터입니다. 사진을 별도로 전부 풀 필요가 없습니다.
JSON은 들여쓰기를 제거하고 imageData=null로 저장합니다. source JSON 검증 표본의
imageData는 원래 null이었으며, 큰 용량의 주원인은 상세 좌표와 들여쓰기였습니다.

5. 코드에서 사진+JSON 함께 읽기 (원본 ZIP에서 메모리로 읽음)

import sys
sys.path.insert(0, r'D:\work\project\전처리된 데이터\qa_v3')
from dataset_io import ExportDataset
run_dir = r'D:\work\project\전처리된 데이터\automatic_results\run_실행시각'
with ExportDataset(run_dir, categories=('auto_fixed', 'kept', 'needs_review')) as ds:
    for image, annotation, record in (ds[i] for i in range(len(ds))):
        # image: RGB PIL 이미지, annotation: 수정 LabelMe JSON dict
        # record: 분류·수정 이력·남은 문제. 필요에 맞게 categories를 선택합니다.
        pass

ExportDataset 기본 분류는 auto_fixed, kept입니다. 부분 수정본까지 자동 사용하려면
위 예시처럼 needs_review를 명시합니다. 수정 데이터는 자동 초안/pseudo-label이며
human_confirmed=False, training_eligible=False는 사람 확정 GT라는 표시를 방지하는
메타데이터입니다. 파일 읽기나 배치 실행을 막는 수동 승인 절차는 없습니다.

6. 실행 범위·공간
전체 사진 36,862개, JSON 34,962개를 ZIP 내부에서 읽습니다.
전체 실행 첫 회에는 전체 JSON 연결 정보 색인을 만듭니다. 원본 JSON의 ZIP 내부
비압축 총량이 약 53.1 GB이므로 전체를 순차적으로 읽는 데 시간이 듭니다.
디스크에 그 53.1 GB을 풀지는 않습니다. SQLite에는 연결 메타데이터만 저장합니다.
색인 이후에도 수정 단계에서 선택된 원본 JSON을 다시 읽어 SHA를 확인합니다.
기본 작업자 2개, 대기 작업 제한, 이미지 복제 0, 출력 JSON 압축을 사용합니다.
여유 공간이 512 MB 미만이 되면 중단하고 summary.json에 incomplete를 기록합니다.
그때까지 저장한 별도 결과와 원본은 보존됩니다.

Smoke/Limit는 일부 JSON만 읽으므로 전체 사진-JSON 누락 판정을 했다는 뜻이 아닙니다.
연결 증거가 부족하거나 모호하면 새 JSON 복원을 보류합니다.
원본 ZIP은 읽기 전용으로 열고, 기존 v1/v2·대표 검수 자료는 보존했습니다.
전체 데이터 정확도, 오류 30% 가정, 사람 검수 완료를 측정한 상태는 아닙니다.

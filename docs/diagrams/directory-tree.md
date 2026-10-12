# 전체 파일·데이터 구조

**이전 설계 참고도**이며, 표시된 폴더·DB가 현재 구현됐다는 의미는 아닙니다. 현재 SQL과의 차이는 [그림 안내](README.md)를 참고합니다.
공정표 정보와 작업 목록은 DB 항목입니다. 이 이전 설계는 S3 저장을 가정했으며, 현재는 Lightsail 파일 저장소를 사용합니다. 현장 이름과 날짜는 범용 예시로 바꿨습니다.

```text
/
├─ PROJECT_RULES.md                          ← 최상위 운영 규칙
├─ site/
│  ├─ site-example/
│  │  ├─ config/
│  │  │  └─ operations.md                   ← 실행 시간·촬영 장소·사용 모델
│  │  ├─ schedule/
│  │  │  └─ v001/                           ← 상위 공정표
│  │  │     ├─ 공정표 ID (schedule_id)        [DB]
│  │  │     ├─ 현장 ID (site_id)              [DB]
│  │  │     ├─ 버전 (version)                [DB]
│  │  │     ├─ originals/
│  │  │     │  ├─ gantt.pdf
│  │  │     │  └─ 추가 원본.pdf
│  │  │     └─ 작업 목록/                    [DB · 실제 폴더 아님]
│  │  │        └─ 각 작업/
│  │  │           ├─ ID (id)
│  │  │           ├─ 스케줄 ID (schedule_id)
│  │  │           ├─ 원본 ID (source_task_id)
│  │  │           ├─ 표시 순서 (display_order)
│  │  │           ├─ WEEK (week)
│  │  │           ├─ 작업명 (task_name)
│  │  │           ├─ 시작일 (start)
│  │  │           ├─ 종료일 (finish)
│  │  │           ├─ 선행 작업 ID (predecessor_id)
│  │  │           └─ 후행 작업 ID (successor_id)
│  │  └─ daily/
│  │     └─ YYYY-MM-DD/                     ← 현장 기준 관측 날짜
│  │        └─ sales-floor-01/              ← 고정 촬영 장소
│  │           ├─ photo.jpg                 ← 장소별 하루 대표 사진
│  │           └─ analyses/
│  │              ├─ unet/
│  │              │  └─ v001/               ← 학습된 모델 버전
│  │              │     └─ r001/            ← 분석 실행 차수
│  │              │        ├─ result.json
│  │              │        ├─ changes.json
│  │              │        └─ report.md
│  │              ├─ mask-rcnn/
│  │              │  └─ v001/
│  │              │     └─ r001/
│  │              │        ├─ result.json
│  │              │        ├─ changes.json
│  │              │        └─ report.md
│  │              └─ deeplab/
│  │                 └─ v001/
│  │                    └─ r001/
│  │                       ├─ result.json
│  │                       ├─ changes.json
│  │                       └─ report.md
│  └─ 다른 현장/
│     ├─ config/
│     ├─ schedule/
│     └─ daily/
├─ gt/
│  └─ site-example/
│     └─ datasets/
│        └─ v001/
│           ├─ dataset.zip                  ← GT 약 25GB 묶음
│           │  └─ ZIP 내부                 [압축 내부 내용]
│           │     ├─ images/
│           │     ├─ annotations/
│           │     ├─ masks/                 ← 필요한 경우
│           │     └─ manifest.json
│           └─ manifest.json                ← 외부에서도 목록 조회
└─ models/
   ├─ unet/
   │  └─ v001/
   │     ├─ weights.pth
   │     ├─ config.json
   │     ├─ classes.json
   │     └─ metrics.json
   ├─ mask-rcnn/
   │  └─ v001/
   │     ├─ weights.pth
   │     ├─ config.json
   │     ├─ classes.json
   │     └─ metrics.json
   └─ deeplab/
      └─ v001/
         ├─ weights.pth
         ├─ config.json
         ├─ classes.json
         └─ metrics.json
```

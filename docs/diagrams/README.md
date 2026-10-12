# 폴더 트리와 ERD 참고도

## 먼저 확인할 점

이 폴더의 그림은 **이전 설계 참고도**다. 현재 운영 SQL과 테이블 이름·저장 방식이 다르며, 그림만 보고 현재 DB를 새로 만들지 않는다.

현재 구현 기준은 [백엔드 설명](../public/BACKEND.md)과 [PostgreSQL 초기 SQL](../../src/backend/sql/001_initial.sql)이다. 실제 운영 DB에는 SQL이 이미 적용돼 있다. 파일 저장소는 현재 Lightsail이며 S3를 사용하지 않는다.

현장 이름과 날짜는 범용 예시로 바꿨고, 접속 주소·계정·키·실데이터 링크는 포함하지 않았다. SVG를 누르면 원본을 크게 볼 수 있다. 아래 Mermaid와 [폴더 트리 텍스트](directory-tree.md)는 그림 미리보기가 안 보여도 읽을 수 있다.

## 현재 구현과의 차이

| 이전 그림 | 현재 SQL·구현 |
|---|---|
| S3 bucket/object_key, stored_files | Lightsail 상대 경로, files |
| daily_observations + analysis_results | daily_records에 대표 사진·결과·직전 관측 연결 |
| 모델별 여러 분석 실행·r001 이력 | 현재 일일 대표 결과 한 건; 재분석 이력 API 미구현 |
| schedule_items | schedule_tasks |
| 공정표 원본을 stored_files.schedule_id로 연결 | schedule_sources로 공정표 버전과 원본 파일 연결 |
| schedule_item_locations | schedule_task_locations |
| gt_samples / datasets / dataset_items | gt_datasets; ZIP 내부 자료·분할은 manifest에서 관리 |
| models + model_versions | model_versions의 model_type·버전·파일 참조 |

## 전체 폴더 트리

[텍스트 트리](directory-tree.md) · [SVG 원본](directory-tree.svg)

![이전 설계 참고용 전체 폴더 트리](directory-tree.svg)


## 운영 데이터 ERD

[SVG 원본](erd-operations.svg) · [Mermaid 원문](erd-operations.mmd)

![이전 설계 참고용 운영 데이터 ERD](erd-operations.svg)

### GitHub에서 읽는 Mermaid 참고도

```mermaid
%% 이전 설계 참고도. 현재 구현 SQL과의 차이는 README.md 참고.
erDiagram
    sites ||..o{ locations : "1:N"
    locations ||..o{ daily_observations : "1:N"
    daily_observations ||..o{ analysis_results : "사진 한 장의 분석 결과"
    model_versions ||..o{ analysis_results : "1:N"
    stored_files o|..o{ analysis_results : "결과 JSON · 변화 JSON · MD의 파일 FK"
    stored_files o|..o{ daily_observations : "photo_file_id · missing 관측은 사진 없음"
    analysis_results o|..o{ analysis_results : "이전 결과"
    sites {
        uuid id PK
        string code UK
        string name
        string address
        string timezone
    }
    locations {
        uuid id PK
        uuid site_id FK
        string code
        string name
        text description
    }
    daily_observations {
        uuid id PK
        uuid location_id FK
        date observation_date
        timestamptz captured_at
        uuid photo_file_id FK
        string status
        text description
    }
    analysis_results {
        uuid id PK
        uuid observation_id FK
        uuid model_version_id FK
        int run_number
        uuid result_file_id FK
        uuid previous_result_id FK
        uuid changes_file_id FK
        uuid report_file_id FK
        string comparison_rule_version
        string comparison_status
        string status
        boolean is_current
    }
    model_versions {
        uuid id PK
        uuid model_id FK
        string version
    }
    stored_files {
        uuid id PK
        uuid schedule_id FK
        string bucket
        string object_key
        string file_kind
        bigint size_bytes
        string sha256
        string upload_status
    }
```


## 공정표 ERD

[SVG 원본](erd-schedule.svg) · [Mermaid 원문](erd-schedule.mmd)

![이전 설계 참고용 공정표 ERD](erd-schedule.svg)

### GitHub에서 읽는 Mermaid 참고도

```mermaid
%% 이전 설계 참고도. 현재 구현 SQL과의 차이는 README.md 참고.
erDiagram
    sites ||..o{ schedules : "공정표 1:N"
    schedules o|..o{ stored_files : "원본 PDF"
    schedules ||..o{ schedule_items : "작업 목록 · schedule_id"
    schedule_items o|..|o schedule_items : "선행·후행 각각 0..1"
    schedule_items ||..o{ schedule_item_locations : "장소 연결"
    locations ||..o{ schedule_item_locations : "장소별 관련 작업"
    sites {
        uuid id PK
        string code UK
        string name
    }
    schedules {
        uuid id PK
        uuid site_id FK
        string version
    }
    stored_files {
        uuid id PK
        uuid schedule_id FK
        string bucket
        string object_key
        string file_kind
        bigint size_bytes
        string sha256
        string upload_status
    }
    schedule_items {
        uuid id PK
        uuid schedule_id FK
        int source_task_id
        int display_order
        int week
        string task_name
        date start
        date finish
        uuid predecessor_id FK
        uuid successor_id FK
    }
    schedule_item_locations {
        uuid id PK
        uuid schedule_item_id FK
        uuid location_id FK
    }
    locations {
        uuid id PK
        uuid site_id FK
        string code
        string name
        text description
    }
```


## GT·모델 ERD

[SVG 원본](erd-training-models.svg) · [Mermaid 원문](erd-training-models.mmd)

![이전 설계 참고용 GT·모델 ERD](erd-training-models.svg)

### GitHub에서 읽는 Mermaid 참고도

```mermaid
%% 이전 설계 참고도. 현재 구현 SQL과의 차이는 README.md 참고.
erDiagram
    locations o|..o{ gt_samples : "촬영 장소 · 미상은 NULL"
    datasets ||..o{ dataset_items : "GT 목록"
    gt_samples ||..o{ dataset_items : "GT 버전 연결"
    models ||..o{ model_versions : "재학습 시 새 버전"
    datasets ||..o{ model_versions : "학습에 사용한 데이터셋"
    stored_files ||..o{ gt_samples : "GT ZIP 파일"
    stored_files o|..o{ datasets : "ZIP · manifest"
    stored_files o|..o{ model_versions : "가중치 · 설정 · 클래스 · 평가"
    locations {
        uuid id PK
        uuid site_id FK
        string code
    }
    gt_samples {
        uuid id PK
        uuid location_id FK
        timestamptz captured_at
        uuid archive_file_id FK
        string image_path
        string annotation_path
        string mask_path
        string label_version
        string source_tool
        string review_status
    }
    datasets {
        uuid id PK
        string name
        string version
        uuid archive_file_id FK
        uuid manifest_file_id FK
        jsonb class_map
        string status
    }
    dataset_items {
        uuid id PK
        uuid dataset_id FK
        uuid gt_sample_id FK
        string split
    }
    models {
        uuid id PK
        string name UK
        string description
    }
    model_versions {
        uuid id PK
        uuid model_id FK
        uuid dataset_id FK
        string version
        string backbone
        uuid weights_file_id FK
        uuid config_file_id FK
        uuid class_map_file_id FK
        uuid metrics_file_id FK
        string code_version
        jsonb library_versions
        jsonb metrics
        timestamptz trained_at
    }
    stored_files {
        uuid id PK
        uuid schedule_id FK
        string bucket
        string object_key
        string file_kind
        bigint size_bytes
        string sha256
        string upload_status
    }
```

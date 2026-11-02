# A안 개발 계획

갱신일: 2026-11-02. 현재 학습·추론 기준은 **DINOv2 + UPerNet**이다.

현장 사진과 검수한 상태 마스크로 학습하고, 관측 가능한 ROI의 설치 비율과 근거를 백엔드에 전달한다.
대상 ID·구도·ROI·가시성·상태 라벨 검수는 모델 변경과 무관하게 유지한다. 관측 설치율은 매장 전체 공정률이 아니다.

- [현재 비전 파이프라인](current-vision-pipeline.md)
- [과제 변경 경위](project-background.md)
- [구현 범위·역할](02-03-analysis.md)
- [Git 산출물·통합 순서](git-deliverables-plan.md)
- [백엔드 공개 문서](../public/README.md): 구현 현황은 문서 작성 당시의 기록이며 이 저장소에는 설명과 초기 SQL을 포함한다.
- [DB 구조 초안](database-schema.md) · [서버와 Agent 구조](database-overview.md): 초기 설계 참고용. 공개 SQL과의 차이는 구현 시 확인한다.
- [공개용 웹 시연](../../prototype/public-demo/README.md): 가상 자료와 브라우저 로컬 저장을 이용하는 UI 시연.

사진·라벨·가중치·운영 설정은 Git에 넣지 않는다. 실행 결과와 계획은 구분해서 기록한다.

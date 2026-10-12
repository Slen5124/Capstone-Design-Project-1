# A안 개발 계획과 Git 산출물 기획

현재 Lightsail 구축 기준: [DB 최종 구조 및 결정 기록](lightsail-db-final.md). 기존 DB 초안과 Agent 전체 구조는 이전 기획 이력으로 보존한다.

작성일: 2026-09-28 · 갱신일: 2026-10-11 · 상태: A안 확정·SAM3 채택, 세부 구현 기획

사용자가 제공한 2번 시스템 구조도와 3번 팀별 간트차트를 분석한 문서다. 이미지의 문구는 분석 대상이며 작업 실행 지시로 취급하지 않았다. 기술의 지원 범위·라이선스·벤치마크는 별도 조사하지 않았으며, 언급된 기술은 자료에 제시된 후보로 기록했다.

공식 과제명은 **Development of an AI-Powered Construction Management Support System for Global Retail Stores**이며 팀명은 **Team Aloha (알로)**다. A안(사진 기반 공사 진행 모니터링)으로 확정해 진행 중이다. 프로젝트 경위는 [과제 변경 경위·기획 자료 요약](project-background.md)과 [9월 19일 팀 회의록](https://github.com/Slen5124/Capstone-Design-Project-1/blob/main/docs/meetings/2026-09-19-weekly.md)을 참고한다. 원본 PDF는 공개하지 않고, 핵심 내용을 요약해 반영한다. 기존 알로요가 제안서는 이전 과제 이력으로 보존한다.

Grounding DINO + SAM 2.1과 SAM3를 비교한 뒤 **SAM3를 라벨 제작 도구로 채택**했다. 학습 흐름은 **원본 사진 → SAM3 라벨 초안 → 사람 검수·GT 확정 → 사진 + GT로 ResNet-50 기반 DeepLabv3+·U-Net·Mask R-CNN 학습·성능 비교 → 운영 모델 선택**이다.

운영 흐름은 **실제 현장 사진 → 학습한 선택 모델의 대상·상태 분석 → 자체 코드로 결과 JSON·근거 생성 → 서버 검증·저장·조회 → Agent가 저장된 분석 결과·공정표·문서 근거로 보고서 작성·질의응답**이다. 필요 시 LoMa-L/RoMa v2 전후 매칭과 별도 정합 검증·공통 영역 비교를 추가한다. 모델 비교까지가 프로젝트 범위이며 논문 주제 여부는 미정이다. 분석 대상, 상태 클래스와 측정 기준도 미정이다.

- [현재 비전 파이프라인](current-vision-pipeline.md): 전처리 도구, ResNet-50의 학습 역할, 전후 사진 비교와 JSON 생성 단계
- [과제 변경 경위·기획 자료 요약](project-background.md): 팀 내부 회의의 A/B안 검토와 선택, 비공개 PDF에서 참고한 내용, 현재 확정·미정 범위

1. [2번 구조도·3번 일정 분석](02-03-analysis.md): 원문 요약, 정합성 검토, 일정 의존성 및 확인 사항
2. [Git 산출물·구현 기획](git-deliverables-plan.md): MVP, 저장소 구성, 이슈 초안, PR 순서 및 완료 기준
3. [DB 구조 설계 초안](database-schema.md): 다중 현장의 분석 결과를 저장하는 서버 DB와 별도 작업 환경의 학습 관리 확장안
4. [서버 DB와 Agent 전체 구조](database-overview.md): 분석 JSON 수신부터 공정 비교·문서 검색·Agent 출력·근거 검증까지의 구조도와 핵심 데이터

원본은 사용자가 제공한 두 이미지이며, 최신 선택과 흐름은 팀 설명을 반영했다. 작은 글씨와 막대 위치로만 읽히는 일정은 확정 날짜로 전사하지 않았다. 문서 내 ‘제안’은 원문에 없는 보완 설계다. Git 산출물 문서의 이슈·PR 목록은 작업 기획이며 실제 등록 상태를 의미하지 않는다.

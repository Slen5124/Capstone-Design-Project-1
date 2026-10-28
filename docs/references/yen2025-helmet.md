# HELMET: How to Evaluate Long-Context Language Models Effectively and Thoroughly

| 항목 | 내용 |
| --- | --- |
| 저자 | Howard Yen, Tianyu Gao, Minmin Hou, Ke Ding, Daniel Fleischer, Peter Izsak, Moshe Wasserblat, Danqi Chen |
| 발표 연도 / 학회·저널 | ICLR 2025. arXiv 최초 공개는 2024년 |
| 링크 / DOI | [논문 안내](https://arxiv.org/abs/2410.02694) · [프로젝트 페이지](https://princeton-nlp.github.io/HELMET/) |
| 검토한 원문 | [PDF](papers/yen2025-helmet.pdf), ICLR 2025 표기가 있는 52쪽 문서 |
| 공식 코드·데이터 | [princeton-nlp/HELMET](https://github.com/princeton-nlp/HELMET) |
| 태그 | `long-context`, `agent-evaluation` |
| 정리 | 원준 공유 자료 기반 팀 리뷰 노트 |
| 정리 날짜 | 2026-10-26 |

## 한 줄 요약

긴 입력에서 정보를 찾는 능력만으로 실제 활용 능력을 판단하지 않고, 질문 응답·근거 인용·요약 등 여러 과제로 긴 문맥 언어모델을 평가하는 벤치마크(공통 평가 과제와 채점 체계)다.

## 다루는 문제

NIAH(Needle-in-a-Haystack, 긴 글 속에 숨긴 특정 정보를 찾는 과제)에서 높은 점수를 받더라도 여러 자료를 종합하거나 정확한 근거를 붙여 답하는 능력까지 보장되지는 않는다. 기존 평가의 과제 범위, 입력 길이, 채점 방식, 기본 모델에 대한 적용 가능성의 차이 때문에 모델 간 비교도 어려웠다.

## 핵심 아이디어 / 방법

본문 §2와 Table 3은 다음 7개 범주를 다룬다.

| 범주 | 평가하는 능력 |
| --- | --- |
| Retrieval-augmented generation (RAG) | 검색한 자료를 읽고 질문에 답하기 |
| Generation with citations | 답변에 실제로 뒷받침하는 자료의 출처를 붙이기 |
| Passage re-ranking | 여러 글 조각을 질문과 관련 높은 순서로 재정렬하기 |
| Many-shot in-context learning | 입력에 제공한 많은 예시를 보고 과제를 수행하기 |
| Long-document QA | 긴 문서의 내용을 이용해 질문에 답하기 |
| Summarization | 긴 문서나 여러 문서를 요약하기 |
| Synthetic recall | 인위적으로 구성한 입력에서 특정 키·값 등 정보를 찾기 |

입력 길이는 8K, 16K, 32K, 64K, 128K 토큰으로 조절해 비교한다. 논문의 K는 1,024를 뜻하며, 모델이 지원하는 길이에 따라 평가 가능한 구간이 다르다. 모든 모델이 모든 길이를 지원한다는 의미는 아니다.

RAG에서는 정답을 포함하는 글과 관련은 있지만 정답이 없는 글을 섞고, 정답 자료의 위치도 바꾼다. 긴 문서 QA·요약에서는 단어 겹침만 보는 평가를 보완하기 위해 reference-based model evaluation(정답 자료를 기준으로 다른 모델이 채점하는 방식)을 사용한다. 과제에 따라 정확 일치, 순위 지표, 인용 품질 등 채점 방법을 구분한다.

## 실험 및 결과

59개 긴 문맥 언어모델을 평가했다. 초록과 본문 §3의 주요 결론은 다음과 같다.

- NIAH 등 합성 과제의 성적만으로 실제 활용 과제의 성적을 안정적으로 예측하기 어렵다.
- 평가 범주 간 상관관계가 낮고 양상이 달라 한 종류의 점수로 전체 능력을 대신하기 어렵다.
- 논문에서 평가한 모델들에서는 긴 입력 전체를 종합하거나 복잡한 지시를 따라야 할 때 공개 모델과 비공개 모델의 격차가 컸으며, 길이가 늘수록 격차가 커지는 경향이 있었다.
- 개발 중 빠른 점검에는 RAG 평가를 권하지만, 최종 판단에는 여러 범주를 함께 평가할 것을 권한다.

이 결과는 당시 평가한 모델과 과제에 대한 결론이다. 현재 사용하는 Qwen3.5-9B의 성능이나 안전한 입력 길이가 이 논문에서 확인됐다는 뜻은 아니다.

## 우리 프로젝트에 적용할 점

아래는 **HELMET의 평가 관점을 참고한 자체 시험 제안이며, HELMET 공식 벤치마크 결과가 아니다.**

1. Vision 결과, 공정표, 이전 기록을 읽고 답해야 하는 질문과 사람이 검수한 기준 답안을 준비한다.
2. 관련 기록 수를 늘리고 핵심 근거의 위치를 앞·중간·뒤로 바꿔, 입력 길이와 배치의 영향을 따로 확인한다.
3. 현장·날짜·대상·revision 선택, 수치 보존, 근거 ID 인용, 판단불가 상태 보존을 각각 평가한다.
4. `held` 또는 `null`인 관측을 0이나 완료로 바꾸지 않는지 확인한다. 관측 가능한 영역의 설치 비율을 매장 전체 공정률로 확대하지 않는지도 별도로 본다.
5. 실행 성공 여부·시간·토큰 수와 답변의 사실성·인용 적절성을 구분해 기록한다.

모델·실행 환경의 실제 컨텍스트 한도 안에서 입력, 이미지 토큰(이미지를 함께 넣는 경우), 출력에 필요한 여유를 고려해 시험한다. 논문이 128K를 평가했다고 해서 현재 8,192 컨텍스트 설정에 같은 양을 넣을 수 있는 것은 아니다.

## 한계 및 주의점

- 텍스트 기반 긴 문맥 평가 연구다. VLM의 작은 물체 인식, 이미지 해상도, 이미지 토큰 한도를 직접 검증하는 논문은 아니다.
- 모델의 컨텍스트 용량을 늘리거나 할루시네이션을 자동 수정하는 기법이 아니다.
- 논문의 모델 기반 채점에는 GPT-4o가 사용되며, 그 채점 자체도 완전한 정답은 아니다. 우리 프로젝트에서는 숫자·ID 등 자동 비교와 사람 검수를 구분해 사용한다.
- 자체 평가에서 자료 배치 효과나 적정 입력량을 확인하기 전에는 특정 길이가 안전하다고 단정할 수 없다.
- Woodpecker가 이미지 설명의 사후 교정 방법이라면, HELMET은 제공된 텍스트 자료를 얼마나 잘 활용하는지 측정하는 평가 체계다.

## 원문 및 공유 정보

팀원이 제공한 ICLR 2025 표기 PDF를 내용 변경 없이 첨부했다. PDF의 저작권과 배포 조건은 원저작물에 귀속되며, 프로젝트 코드의 이용 조건과 동일하다고 해석하지 않는다. [arXiv 원문 PDF](https://arxiv.org/pdf/2410.02694)와 [학회 페이지](https://openreview.net/forum?id=293V3bJbmE)에서도 원문을 확인할 수 있다.

## 인용 (BibTeX)

```bibtex
@inproceedings{yen2025helmet,
  title={HELMET: How to Evaluate Long-Context Language Models Effectively and Thoroughly},
  author={Howard Yen and Tianyu Gao and Minmin Hou and Ke Ding and Daniel Fleischer and Peter Izsak and Moshe Wasserblat and Danqi Chen},
  year={2025},
  booktitle={International Conference on Learning Representations (ICLR)},
  url={https://openreview.net/forum?id=293V3bJbmE}
}
```

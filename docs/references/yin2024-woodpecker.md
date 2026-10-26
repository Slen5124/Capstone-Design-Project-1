# Woodpecker: Hallucination Correction for Multimodal Large Language Models

| 항목 | 내용 |
| --- | --- |
| 저자 | Shukang Yin, Chaoyou Fu, Sirui Zhao, Tong Xu, Hao Wang, Dianbo Sui, Yunhang Shen, Ke Li, Xing Sun, Enhong Chen |
| 발표 연도 / 학회·저널 | Science China Information Sciences, 67, 220105 (2024). arXiv 최초 공개는 2023년 |
| 링크 / DOI | [논문 안내](https://arxiv.org/abs/2310.16045) · [저널 DOI](https://doi.org/10.1007/s11432-024-4251-x) |
| 검토한 원문 | [PDF](papers/yin2024-woodpecker.pdf), arXiv:2310.16045v2, 2024-12-11, 16쪽 |
| 공식 코드 | [BradyFU/Woodpecker](https://github.com/BradyFU/Woodpecker) |
| 태그 | `vlm-hallucination`, `agent-evaluation` |
| 정리 | 원준 공유 자료 기반 팀 리뷰 노트 |
| 정리 날짜 | 2026-10-26 |

## 한 줄 요약

VLM(이미지와 글을 함께 처리하는 모델)이 이미 생성한 답변을 이미지에서 얻은 근거와 대조해, 할루시네이션(사진과 맞지 않는 설명)을 찾아 수정하는 후처리 방법이다.

## 다루는 문제

논문은 오류를 object-level hallucination(없는 물체를 있다고 설명하는 등 물체 수준의 오류)과 attribute-level hallucination(물체의 색상·위치·관계 등을 잘못 설명하는 속성 수준의 오류)으로 나눈다. 원래 VLM을 재학습하지 않고 이미 학습된 모델들을 조합하는 training-free 방식이다. 추가 모델 실행이 필요 없다는 뜻은 아니다.

## 핵심 아이디어 / 방법

본문 §3과 Figure 3의 처리 순서는 다음과 같다.

1. **Key Concept Extraction:** 기존 답변에서 확인할 핵심 물체를 추출한다.
2. **Question Formulation:** 물체의 존재·개수 및 속성을 확인할 질문을 만든다.
3. **Visual Knowledge Validation:** 이미지에 질문을 대조한다. 구현에서는 Grounding DINO로 물체를 탐지해 위치·개수의 근거를 얻고, BLIP-2의 VQA(이미지 질문 응답)로 속성을 확인한다.
4. **Visual Claim Generation:** 질문과 답을 물체·속성에 대한 문장으로 정리해 structured visual knowledge base(구조화된 시각 근거 모음)를 만든다.
5. **Hallucination Correction:** 원래 답변을 이 근거 모음과 대조해 수정한다.

여기서 탐지 모델이나 VQA가 내놓은 결과도 오류를 포함할 수 있다. 모델의 답변을 단순히 다시 믿는 것으로 검증이 끝나는 것은 아니다.

## 실험 및 결과

POPE, MME, LLaVA-QA90에서 정량·정성 평가를 수행했다. 다음 수치는 첨부 PDF 6쪽 Table 1의 **POPE Random 조건**이다.

| 모델 / 지표 | 적용 전 | Woodpecker 적용 후 | 차이 |
| --- | ---: | ---: | ---: |
| MiniGPT-4 / Accuracy | 54.67% | 85.33% | +30.66%p |
| mPLUG-Owl / Accuracy | 62.00% | 86.33% | +24.33%p |

30.66은 위 정확도의 차이인 **퍼센트포인트(%p)**다. 모든 평가 조건의 평균이나 일반적인 이미지 설명 정확도, 우리 Qwen의 개선 수치가 아니다. 같은 표에서 LLaVA의 Random 조건 Recall은 84.00에서 78.67로 낮아지는 등, 모든 지표가 일괄 상승하지는 않는다.

## 우리 프로젝트에 적용할 점

아래는 **논문을 참고한 적용 제안이며, Woodpecker 전체를 구현·검증했다는 뜻은 아니다.**

- Qwen의 공사 물체 관측을 곧바로 공정 완료로 해석하지 않고, 물체 설명 → 확인할 주장 → 근거 대조 → 연결 또는 보류 순서로 검토한다.
- 일반 케이블의 존재와 그 용도가 전기·IT·보안·소방 중 무엇인지는 별도 주장으로 다룬다. 구분 근거가 없으면 용도와 공정 매핑을 보류한다.
- 모델이 최초에 낸 답, 검증에 사용한 근거, 수정·보류 이유를 각각 보존한다. JSON 형식 검증만으로 시각적 사실까지 맞다고 판단하지 않는다.
- Vision이 제공하는 벽·바닥·천장 분석 결과는 Backend를 통해 받아 활용한다. Agent에 Grounding DINO·BLIP-2를 추가하는 것은 구현 비용과 중복 처리를 검토한 뒤 결정한다.
- 효과를 확인하려면 같은 사진과 사람이 확인한 기준으로 검증 전후의 잘못된 주장, 누락, 보류를 비교해야 한다.

## 한계 및 주의점

- 이 논문은 Qwen3.5-9B나 우리 공사 현장 사진에서 효과를 검증한 연구가 아니다.
- 고정 카메라의 화면 밖, 가려진 곳, 너무 작은 설비는 추가 검증 모델을 사용해도 확인하지 못할 수 있다. 미검출을 실제 부재로 단정하면 안 된다.
- 원래 답변에서 뽑은 개념을 중심으로 확인하므로, 처음부터 언급하지 않은 물체의 누락을 모두 해결하는 방법은 아니다.
- 별도 탐지·VQA·언어모델 호출 때문에 지연과 자원 사용이 늘 수 있다.
- 물체 관측은 실제 공정 완료·검사 통과·매장 전체 공정률을 증명하지 않는다.

## 원문 및 공유 정보

팀원이 제공한 PDF를 내용 변경 없이 첨부했다. 리뷰는 첨부한 arXiv v2 본문을 기준으로 작성했고, 저널 발표 정보는 위 DOI에서 확인했다. PDF의 저작권과 배포 조건은 원저작물에 귀속되며, 프로젝트 코드의 이용 조건과 동일하다고 해석하지 않는다. [arXiv 원문 PDF](https://arxiv.org/pdf/2310.16045v2)도 함께 제공한다.

## 인용 (BibTeX)

```bibtex
@article{yin2024woodpecker,
  title={Woodpecker: hallucination correction for multimodal large language models},
  author={Yin, Shukang and Fu, Chaoyou and Zhao, Sirui and Xu, Tong and Wang, Hao and Sui, Dianbo and Shen, Yunhang and Li, Ke and Sun, Xing and Chen, Enhong},
  journal={Science China Information Sciences},
  volume={67},
  pages={220105},
  year={2024},
  doi={10.1007/s11432-024-4251-x}
}
```

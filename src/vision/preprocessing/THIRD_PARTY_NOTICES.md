# Third-party provenance and asset terms

This folder separates local preprocessing code from external model assets. It does not redistribute weights, ONNX exports, tokenizer vocabularies, source images, or annotation settings. A source-code license does not by itself establish the permitted use of a separately obtained model checkpoint or export.

## X-AnyLabeling adapters

`dino_detector.py` and `person_detector.py` adapt the public X-AnyLabeling preprocessing, input construction, class mapping, and coordinate conversion. These are modified standalone adapters, rather than an unmodified distribution of X-AnyLabeling. DINO text attention handling is simplified for a single batch, and the person adapter adds validation and per-axis coordinate recovery. Local modifications also include explicit asset arguments and separate draft-label provenance. The exact historical upstream commit has not been established. Do not describe these adapters as proprietary, unrestricted, or covered solely by an upstream model's Apache license.

X-AnyLabeling is published under **GNU GPL version 3**. The modified adapters are marked `GPL-3.0-only`. The upstream license text is retained in `LICENSE.x-anylabeling.txt`; upstream notices and applicable GPL obligations must be preserved when distributing derived adapter code and combined works. This notice does not assign a new license to unrelated repository files.

- [X-AnyLabeling license](https://github.com/CVHub520/X-AnyLabeling/blob/main/LICENSE)
- [Base Grounding DINO adapter](https://github.com/CVHub520/X-AnyLabeling/blob/main/anylabeling/services/auto_labeling/__base__/grounding_dino.py)
- [Grounding DINO adapter](https://github.com/CVHub520/X-AnyLabeling/blob/main/anylabeling/services/auto_labeling/grounding_dino.py)
- [DEIMv2 adapter](https://github.com/CVHub520/X-AnyLabeling/blob/main/anylabeling/services/auto_labeling/deimv2.py)
- [DEIMv2 COCO class mapping](https://github.com/CVHub520/X-AnyLabeling/blob/main/anylabeling/configs/auto_labeling/deimv2_hgnetv2_n_coco.yaml)

## Model projects

Checked on 2026-10-11 against the primary repositories:

| Project | Source repository license and relevant boundary |
| --- | --- |
| [Grounding DINO](https://github.com/IDEA-Research/GroundingDINO/blob/main/LICENSE) | Apache License 2.0 for the repository code; retain the terms supplied with the particular weights/export. |
| [SAM 2](https://github.com/facebookresearch/sam2/blob/main/LICENSE) | Apache License 2.0 for the repository code; retain the terms supplied with the particular weights/export. |
| [DEIMv2](https://github.com/Intellindust-AI-Lab/DEIMv2/blob/main/LICENSE.md) | The current repository license permits noncommercial uses and states that commercial rights require a separate grant. The current license also addresses model-weight use. |

The exact license applicable to a previously cached DEIMv2 ONNX export was **not independently established** during this packaging step. Consequently this package is not evidence of commercial clearance for that asset. Record its source, release, checksum, and distributed terms locally before using it in a company deployment. The published preprocessing code currently expects that export's interface; replacing the asset can require an adapter change.

Tokenizer files are supplied by the caller and must match the DINO model. Their origin and applicable terms are also managed separately. No bundled tokenizer is included.

## Dependency notices

Python dependencies remain separate distributions with their own licenses and notices. `requirements.txt` records versions observed in the existing local CPU environment, not a redistribution of those packages.

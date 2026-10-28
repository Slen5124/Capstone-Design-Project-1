# SITE_B 대표 사진의 작은 실행 확인용 데이터 helper입니다.
# V2의 제외 상태를 적용한 뒤 V3 수정본을 같은 image_id에 교체하여 버전 중복을 막습니다.
# 원본 폴더, metadata의 checked/training_eligible 값, 사진과 마스크는 수정하지 않습니다.
# allow_drafts=True는 미검수 초안의 실행 확인을 허용할 뿐, GT 승인이나 정확도 검증을 뜻하지 않습니다.

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset


NATIVE_SIZE = (2688, 1512)  # PIL 크기는 (가로, 세로), 배열 크기는 (세로, 가로)입니다.
MASK_IDS = {1, 2, 3, 4, 5, 255}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_hash(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError("{}: SHA-256 문자열이 없습니다".format(label))
    value = value.lower()
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError("{}: 올바른 SHA-256이 아닙니다".format(label))
    return value


def _read_manifest(bundle: Path) -> List[Dict]:
    records = []
    seen = set()
    path = bundle / "manifest.jsonl"
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        image_id = record.get("image_id")
        if not isinstance(image_id, str) or not image_id or "/" in image_id or "\\" in image_id:
            raise ValueError("{}:{}: 잘못된 image_id".format(path, line_number))
        if image_id in seen:
            raise ValueError("{}: 중복 image_id {}".format(path, image_id))
        seen.add(image_id)
        records.append(record)
    if not records:
        raise ValueError("{}: manifest가 비어 있습니다".format(path))
    return records


def _native_check(image_path: Path, mask_path: Path, image_id: str) -> None:
    # 마스크를 축소하기 전에 원본 해상도와 ID를 검사합니다. 이 단계에서 255를 other로 바꾸지 않습니다.
    with Image.open(image_path) as image:
        if image.size != NATIVE_SIZE:
            raise ValueError("{}: 사진 크기 {} != {}".format(image_id, image.size, NATIVE_SIZE))
    with Image.open(mask_path) as mask:
        if mask.mode != "L" or mask.size != NATIVE_SIZE:
            raise ValueError("{}: 마스크는 L 모드 {}여야 합니다".format(image_id, NATIVE_SIZE))
        values = set(np.unique(np.asarray(mask)).tolist())
        if not values.issubset(MASK_IDS):
            raise ValueError("{}: 허용되지 않은 마스크 ID {}".format(image_id, sorted(values - MASK_IDS)))


def _make_entry(bundle: Path, record: Dict) -> Dict:
    image_id = record["image_id"]
    filename = record.get("filename")
    if filename != image_id + ".jpg":
        raise ValueError("{}: filename과 image_id가 일치하지 않습니다".format(image_id))
    if tuple(record.get("image_size", ())) != NATIVE_SIZE:
        raise ValueError("{}: metadata 원본 크기가 다릅니다".format(image_id))
    image_path = bundle / "images" / filename
    mask_path = bundle / "semantic_drafts" / (image_id + ".png")
    source_hash = _expected_hash(record.get("source_sha256"), image_id)
    if _sha256(image_path) != source_hash:
        raise ValueError("{}: 사진 SHA-256이 metadata와 다릅니다".format(image_id))
    _native_check(image_path, mask_path, image_id)
    captured_at = record.get("captured_at_candidate")
    if not isinstance(captured_at, str):
        raise ValueError("{}: 날짜 후보가 없습니다".format(image_id))
    day = date.fromisoformat(captured_at.split("T", 1)[0]).isoformat()
    checked = record.get("checked", False)
    training_eligible = record.get("training_eligible", False)
    if not isinstance(checked, bool) or not isinstance(training_eligible, bool):
        raise ValueError("{}: 검수/학습 승인 값은 bool이어야 합니다".format(image_id))
    # 경로는 문자열로 반환하므로 entries와 audit를 JSON에 그대로 기록할 수 있습니다.
    return {
        "image_id": image_id,
        "image_path": str(image_path),
        "mask_path": str(mask_path),
        "day": day,
        "bundle": str(bundle),
        "source_sha256": source_hash,
        "mask_sha256": _sha256(mask_path),
        "checked": checked,
        "training_eligible": training_eligible,
    }


def _select(entries: Dict[str, Dict], suffixes: Sequence[str], split: str) -> List[Dict]:
    if not suffixes or len(set(suffixes)) != len(suffixes):
        raise ValueError("{}: suffix 목록은 비어 있거나 중복될 수 없습니다".format(split))
    selected = []
    selected_ids = set()
    for suffix in suffixes:
        if not isinstance(suffix, str) or not suffix:
            raise ValueError("{}: suffix는 비어 있지 않은 문자열이어야 합니다".format(split))
        matches = [entry for image_id, entry in entries.items() if image_id.endswith("-" + suffix)]
        if len(matches) != 1:
            raise ValueError("{}: suffix {}가 활성 사진 {}개와 일치합니다; 정확히 1개 필요".format(split, suffix, len(matches)))
        if matches[0]["image_id"] in selected_ids:
            raise ValueError("{}: 서로 다른 suffix가 같은 사진을 중복 선택합니다".format(split))
        selected_ids.add(matches[0]["image_id"])
        selected.append(dict(matches[0]))
    return selected


def load_pilot(
    v2: Path,
    v3: Path,
    train_suffixes: Optional[List[str]] = None,
    val_suffixes: Optional[List[str]] = None,
    allow_drafts: bool = False,
) -> Tuple[List[Dict], List[Dict], Dict]:
    # V2 manifest에는 보존용 18번도 남습니다. active_candidate=False를 먼저 제외해야 합니다.
    v2 = Path(v2).resolve()
    v3 = Path(v3).resolve()
    parent_records = _read_manifest(v2)
    revision_records = _read_manifest(v3)
    parent_by_id = {record["image_id"]: record for record in parent_records}
    effective = {}
    excluded = []
    for record in parent_records:
        status = record.get("v2", {}).get("active_candidate")
        if not isinstance(status, bool):
            raise ValueError("{}: V2 active_candidate bool 값이 없습니다".format(record["image_id"]))
        if not status:
            excluded.append({
                "image_id": record["image_id"],
                "reason": record.get("v2", {}).get("exclusion_reason"),
            })
            continue
        effective[record["image_id"]] = _make_entry(v2, record)
    active_count = len(effective)
    if not effective:
        raise ValueError("활성 V2 사진이 없습니다")

    replacements = []
    for record in revision_records:
        image_id = record["image_id"]
        if image_id not in effective:
            raise ValueError("{}: V3가 활성 V2 부모를 교체하지 않습니다".format(image_id))
        parent_record = parent_by_id[image_id]
        parent_entry = effective[image_id]
        if record.get("parent_annotation_version") != parent_record.get("annotation_version"):
            raise ValueError("{}: V3의 부모 annotation_version이 다릅니다".format(image_id))
        parent_hash = _expected_hash(record.get("parent_semantic_sha256"), image_id + " parent mask")
        if parent_hash != parent_entry["mask_sha256"]:
            raise ValueError("{}: V3 부모 마스크 SHA-256이 V2와 다릅니다".format(image_id))
        # V3 수정팩 안에 남긴 수정 전 사본도 실제 V2 부모와 같은지 확인합니다.
        if _sha256(v3 / "parent_masks" / (image_id + ".png")) != parent_hash:
            raise ValueError("{}: V3 parent_masks 사본이 다릅니다".format(image_id))
        new_entry = _make_entry(v3, record)
        if new_entry["source_sha256"] != parent_entry["source_sha256"]:
            raise ValueError("{}: V3와 V2가 같은 원본 사진이 아닙니다".format(image_id))
        effective[image_id] = new_entry  # 추가하지 않고 같은 ID의 마스크/경로를 교체합니다.
        replacements.append({
            "image_id": image_id,
            "parent_mask_sha256": parent_hash,
            "replacement_mask_sha256": new_entry["mask_sha256"],
            "source_sha256": new_entry["source_sha256"],
        })

    # 다른 이름으로 저장된 동일 사진도 중복 샘플로 사용하지 않습니다.
    hashes = [entry["source_sha256"] for entry in effective.values()]
    if len(hashes) != len(set(hashes)):
        raise ValueError("활성 자료에 같은 사진 SHA-256이 중복됩니다")
    # 실제 현장 파일 목록은 Git에 넣지 않는 별도 로컬 선택 설정에서 전달합니다.
    if train_suffixes is None or val_suffixes is None:
        raise ValueError("train/validation suffix 목록을 로컬 설정에서 명시해야 합니다")
    train_requested = list(train_suffixes)
    val_requested = list(val_suffixes)
    train_entries = _select(effective, train_requested, "train")
    val_entries = _select(effective, val_requested, "validation")
    for key in ("image_id", "day", "source_sha256"):
        overlap = {entry[key] for entry in train_entries} & {entry[key] for entry in val_entries}
        if overlap:
            raise ValueError("train/validation {} 중복: {}".format(key, sorted(overlap)))
    selected = train_entries + val_entries
    drafts = [entry for entry in selected if not (entry["checked"] and entry["training_eligible"])]
    if drafts and not allow_drafts:
        raise ValueError("미검수 또는 학습 미승인 마스크 {}개입니다; 실행 확인에 사용하려면 allow_drafts=True 필요".format(len(drafts)))

    audit = {
        "purpose": "small_execution_smoke_not_ground_truth_accuracy",
        "v2_bundle": str(v2),
        "v3_bundle": str(v3),
        "v2_retained_count": len(parent_records),
        "v2_active_count": active_count,
        "excluded_count": len(excluded),
        "excluded": excluded,
        "v3_replacement_count": len(replacements),
        "v3_replacements": replacements,
        "effective_count": len(effective),
        "selected_count": len(selected),
        "selection": {"train": train_entries, "validation": val_entries},
        "requested_suffixes": {"train": train_requested, "validation": val_requested},
        "drafts_allowed": bool(allow_drafts),
        "selected_draft_count": len(drafts),
        "selected_draft_image_ids": [entry["image_id"] for entry in drafts],
        "selected_all_checked_and_training_eligible": not drafts,
        "original_metadata_flags_preserved": True,
        "native_size": list(NATIVE_SIZE),
        "native_pairs_validated": active_count + len(revision_records),
        "native_allowed_mask_ids": sorted(MASK_IDS),
        "image_and_parent_mask_sha256_verified": True,
        "split_image_day_sha_overlap": False,
        "grouping": "captured_at_candidate_day; camera/session and timezone remain unverified",
        "training_ids": {"1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "255": 255},
    }
    return train_entries, val_entries, audit


class SmokeDataset(Dataset):
    def __init__(self, entries: Sequence[Dict], size: Tuple[int, int] = (512, 288)):
        # 작은 3+2장 묶음의 텐서를 미리 준비합니다. 캐시는 입력 파일이 아닌 메모리에만 남습니다.
        # size는(가로, 세로)입니다. 클래스 경계를 보존하기 위해 마스크는 반드시 nearest로 줄입니다.
        if len(size) != 2 or any(not isinstance(value, int) or value <= 0 for value in size):
            raise ValueError("size는 양의 정수(가로, 세로) 두 값이어야 합니다")
        if not entries:
            raise ValueError("SmokeDataset의 entries가 비어 있습니다")
        self.entries = [dict(entry) for entry in entries]
        self.size = tuple(size)
        self.histogram = np.zeros(5, dtype=np.int64)
        self._cache = []
        valid_pixels = 0
        mean = torch.tensor([0.485, 0.456, 0.406], dtype=torch.float32).view(3, 1, 1)
        std = torch.tensor([0.229, 0.224, 0.225], dtype=torch.float32).view(3, 1, 1)
        for entry in self.entries:
            image_id = entry["image_id"]
            image_path = Path(entry["image_path"])
            mask_path = Path(entry["mask_path"])
            # load_pilot 이후 사진/마스크가 바뀌지 않았는지 텐서를 만들기 직전에도 확인합니다.
            if _sha256(image_path) != entry["source_sha256"] or _sha256(mask_path) != entry["mask_sha256"]:
                raise ValueError("{}: 선택 이후 사진/마스크의 SHA-256이 바뀌었습니다".format(image_id))
            _native_check(image_path, mask_path, image_id)
            with Image.open(image_path) as image:
                resized = image.convert("RGB").resize(self.size, Image.Resampling.BILINEAR)
                rgb = np.asarray(resized, dtype=np.float32) / np.float32(255.0)
            with Image.open(mask_path) as mask:
                labels = np.asarray(mask.resize(self.size, Image.Resampling.NEAREST))
            # 원본 semantic의 1..5를 손실용 0..4로 바꾸고 unknown/ignore=255는 그대로 남깁니다.
            target = np.full(labels.shape, 255, dtype=np.int64)
            for original_id in range(1, 6):
                target[labels == original_id] = original_id - 1
            valid = target != 255
            count = int(valid.sum())
            if count == 0:
                raise ValueError("{}: 축소한 target의 모든 픽셀이 255입니다".format(image_id))
            self.histogram += np.bincount(target[valid], minlength=5).astype(np.int64)
            valid_pixels += count
            # ImageNet 정규화한 RGB float32 C,H,W와 long target H,W를 반환할 형태로 저장합니다.
            image_tensor = torch.from_numpy(np.ascontiguousarray(rgb.transpose(2, 0, 1)))
            image_tensor = (image_tensor - mean) / std
            target_tensor = torch.from_numpy(target).long()
            self._cache.append((image_tensor, target_tensor))
        self.valid_coverage = float(valid_pixels) / (len(self.entries) * self.size[0] * self.size[1])

    def __len__(self) -> int:
        return len(self._cache)

    def __getitem__(self, index: int):
        return self._cache[index]

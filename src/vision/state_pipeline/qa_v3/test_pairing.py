"""Pairing ambiguity must not authorize automatic semantic edits or recovery."""
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from PIL import Image

from auto_preprocess import atomic_json, run


class PairingRegressionTest(unittest.TestCase):
    def _run(self, root, photo_entries, json_entries, teacher_entry):
        workspace = root / "workspace"
        workspace.mkdir()
        photo_zip, json_zip = root / "photos.zip", root / "annotations.zip"
        stream = io.BytesIO()
        Image.new("RGB", (100, 80), "gray").save(stream, format="PNG")
        pixels = stream.getvalue()
        with zipfile.ZipFile(photo_zip, "w") as archive:
            for entry in photo_entries:
                archive.writestr(entry, pixels)
        with zipfile.ZipFile(json_zip, "w") as archive:
            for entry, doc in json_entries.items():
                archive.writestr(entry, json.dumps(doc) if isinstance(doc, dict) else doc)
        teacher = {
            "id": "pairing_teacher",
            "source": {"zip": str(photo_zip), "entry": teacher_entry,
                       "sha256": hashlib.sha256(pixels).hexdigest()},
            "size": [100, 80], "reference_origin": "assistant_authorized_surface_draft",
            "human_confirmed": False,
            "regions": [{"id": "known_wall", "label": "wall", "role": "complete_surface",
                         "points": [[5, 5], [90, 5], [90, 70], [5, 70]],
                         "min_coverage": .6, "conflict_labels": ["ceiling"]}],
        }
        atomic_json(workspace / "project.json", {
            "photos": [str(photo_zip)], "json_zip": str(json_zip), "references": [teacher]})
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (photo_zip, json_zip)]
        args = argparse.Namespace(workspace=workspace, out=root / "outputs", entries_file=None,
                                  limit=0, workers=1, max_references=2, layout="files", min_free_mb=0)
        with contextlib.redirect_stdout(io.StringIO()):
            output = run(args)
        records = [json.loads(line) for line in (output / "manifest.jsonl").read_text(
            encoding="utf-8").splitlines()]
        self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in (photo_zip, json_zip)])
        return output, records

    @staticmethod
    def _doc(image_path):
        return {"imagePath": image_path, "imageWidth": 100, "imageHeight": 80,
                "imageData": None, "flags": {}, "shapes": [
                    {"label": "ceiling", "shape_type": "polygon", "group_id": None,
                     "flags": {}, "points": [[5, 5], [90, 5], [90, 70], [5, 70]]}]}

    def test_proven_json_plus_ambiguous_basename_withholds_all_semantic_edits(self):
        temp_root = Path(__file__).resolve().parent.parent / "tmp"
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as name:
            original_doc = self._doc("a/target.png")
            output, records = self._run(Path(name), ["a/target.png", "b/target.png"], {
                "unique_annotation.json": original_doc,
                "ambiguous_annotation.json": self._doc("target.png")}, "a/target.png")
            involved = next(record for record in records
                            if record["source_photo"]["entry"] == "a/target.png")
            self.assertEqual(involved["classification"], "needs_review")
            self.assertEqual(involved["actions"], [])
            self.assertFalse(involved["changed"])
            self.assertIn("JSON_PAIR_AMBIGUITY_AUTOMATIC_EDIT_WITHHELD",
                          {item["code"] for item in involved["unresolved"]})
            # The existing wrong-label shape is preserved; ambiguity cannot
            # authorize relabeling merely because an exact teacher is available.
            exported = json.loads(Path(involved["annotation_output"]["path"]).read_text(encoding="utf-8"))
            self.assertEqual(exported["shapes"], original_doc["shapes"])
            second = next(record for record in records
                          if record["source_photo"]["entry"] == "b/target.png")
            self.assertEqual(second["classification"], "needs_review")
            self.assertIsNone(second["annotation_output"])
            self.assertFalse(any(p.suffix.lower() in {".png", ".jpg"} for p in output.rglob("*")))

    def test_unreadable_unmatched_json_does_not_prove_missing_json_recovery(self):
        temp_root = Path(__file__).resolve().parent.parent / "tmp"
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as name:
            output, records = self._run(Path(name), ["target.png"], {
                # Actual intended imagePath cannot be recovered by strict JSON
                # parsing; its unrelated filename cannot prove this photo missing.
                "unrelated_annotation_name.json": b'{"imagePath":"target.png", malformed'}, "target.png")
            photo = next(record for record in records
                         if record["source_photo"]["entry"] == "target.png")
            self.assertEqual(photo["classification"], "needs_review")
            self.assertEqual(photo["actions"], [])
            self.assertIsNone(photo["annotation_output"])
            self.assertIn("JSON_PAIR_NOT_PROVEN_RECOVERY_WITHHELD",
                          {item["code"] for item in photo["unresolved"]})
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["exported_json"], 0)


if __name__ == "__main__":
    unittest.main()

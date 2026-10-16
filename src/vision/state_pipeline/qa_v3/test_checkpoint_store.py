import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from checkpoint_store import CheckpointStore


def record(photo, suffix="", changed=True):
    return {"id": photo + suffix, "classification": "auto_fixed" if changed else "kept",
            "source_photo": {"zip": "original.zip", "entry": photo + ".jpg", "sha256": "abc"},
            "source_json": {"entry": photo + ".json"}, "source_json_zip": "original_json.zip",
            "changed": changed, "actions": [{"action": "repair"}] if changed else [],
            "unresolved": [], "human_confirmed": False, "training_eligible": False}


class CheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parent.parent / "tmp"
        temp_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.run = Path(self.temp.name) / "run"

    def tearDown(self):
        self.temp.cleanup()

    def test_zip_resume_catalogs_and_multiple_records_per_photo(self):
        store = CheckpointStore(self.run, checkpoint_every=2)
        for photo in ("P0:one", "P0:two"):
            for n in range(2):
                saved = store.write_record(record(photo, ":" + str(n)), b'{"shapes":[]}', f"P0/{photo[-3:]}_{n}.json")
                self.assertIn("part_000001", saved["annotation_output"]["zip"])
            store.complete_photo(photo)
        store.close()
        again = CheckpointStore(self.run, resume=True, checkpoint_every=2)
        self.assertEqual(again.recover()["completed_photo_ids"], {"P0:one", "P0:two"})
        self.assertEqual(again.recover()["counters"]["exported_json"], 4)
        again.regenerate_catalogs()
        records = [json.loads(line) for line in (self.run / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(records), 4)
        self.assertEqual(len({r["id"] for r in records}), 4)
        self.assertTrue((self.run / "classification.csv").read_bytes().startswith(b"\xef\xbb\xbf"))
        for result in records:
            with zipfile.ZipFile(result["annotation_output"]["zip"]) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.read(result["annotation_output"]["entry"]), b'{"shapes":[]}')
        again.close()

    def test_pending_chunk_is_quarantined_and_does_not_skip_photos(self):
        store = CheckpointStore(self.run, checkpoint_every=3)
        store.write_record(record("P0:one"), b"{}", "one.json")
        store.complete_photo("P0:one")
        # Close handles only, leaving the same on-disk pending state a killed
        # process would leave. It has no completed.json commit marker.
        store._close_handles(store._active)
        again = CheckpointStore(self.run, resume=True)
        self.assertEqual(again.recover()["completed_photo_ids"], set())
        self.assertTrue(any(p.name.startswith(".abandoned_") for p in (self.run / "chunks").iterdir()))
        again.write_record(record("P0:one"), b"{}", "one.json")
        again.complete_photo("P0:one")
        again.close()
        final = CheckpointStore(self.run, resume=True)
        self.assertEqual(final.recover()["completed_photo_ids"], {"P0:one"})
        self.assertEqual(len(list(final.iter_records())), 1)
        final.close()

    def test_half_photo_is_never_committed_on_close(self):
        store = CheckpointStore(self.run, checkpoint_every=2)
        store.write_record(record("P0:one"), b"{}", "one.json")
        store.complete_photo("P0:one")
        store.write_record(record("P0:two"), b"{}", "two.json")
        with self.assertRaisesRegex(RuntimeError, "incomplete photo"):
            store.commit()
        store.close()
        resumed = CheckpointStore(self.run, resume=True)
        self.assertEqual(resumed.recover()["completed_photo_ids"], set())
        resumed.close()

    def test_corrupted_zip_fails_resume_without_skipping(self):
        with CheckpointStore(self.run, checkpoint_every=1) as store:
            store.write_record(record("P0:one"), b"{}", "one.json")
            store.complete_photo("P0:one")
        archive = self.run / "chunks" / "part_000001" / "annotations" / "auto_fixed.zip"
        original = archive.read_bytes()
        archive.write_bytes(b"broken" + original)
        with self.assertRaisesRegex(ValueError, "hash changed"):
            CheckpointStore(self.run, resume=True)
        self.assertTrue(archive.read_bytes().startswith(b"broken"))

    def test_files_layout_and_recovery(self):
        with CheckpointStore(self.run, layout="files", checkpoint_every=1) as store:
            saved = store.write_record(record("P0:one", changed=False), b"{}", "P0/one.json")
            store.complete_photo("P0:one")
        self.assertEqual(Path(saved["annotation_output"]["path"]).read_bytes(), b"{}")
        again = CheckpointStore(self.run, layout="files", resume=True)
        self.assertEqual(again.recover()["counters"]["classes"], {"kept": 1})
        again.close()

    def test_output_traversal_and_layout_change_rejected(self):
        with CheckpointStore(self.run, checkpoint_every=1) as store:
            for name in ("../escape.json", "/absolute.json", "C:/escape.json", "folder\\escape.json", "folder//file.json"):
                with self.assertRaises(ValueError):
                    store.write_record(record("P0:one"), b"{}", name)
            store.write_record(record("P0:one"), b"{}", "one.json")
            store.complete_photo("P0:one")
        with self.assertRaisesRegex(ValueError, "layout"):
            CheckpointStore(self.run, layout="files", resume=True)

    def test_repeated_completed_photo_id_across_chunks_rejected(self):
        with CheckpointStore(self.run, checkpoint_every=1) as store:
            for photo in ("P0:one", "P0:two"):
                store.write_record(record(photo), b"{}", photo[-3:] + ".json")
                store.complete_photo(photo)
        second = self.run / "chunks" / "part_000002"
        metadata = json.loads((second / "completed.json").read_text(encoding="utf-8"))
        records = [json.loads(line) for line in (second / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
        records[0]["checkpoint_photo_id"] = "P0:one"
        raw = "".join(json.dumps(value) + "\n" for value in records).encode()
        (second / "manifest.jsonl").write_bytes(raw)
        metadata["completed_photo_ids"] = ["P0:one"]
        metadata["files_sha256"]["manifest.jsonl"] = hashlib.sha256(raw).hexdigest()
        (second / "completed.json").write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate completed photo IDs"):
            CheckpointStore(self.run, resume=True)

    def test_forged_outside_annotation_path_rejected(self):
        with CheckpointStore(self.run, checkpoint_every=1) as store:
            store.write_record(record("P0:one"), b"{}", "one.json")
            store.complete_photo("P0:one")
        chunk = self.run / "chunks" / "part_000001"
        manifest = chunk / "manifest.jsonl"
        metadata = json.loads((chunk / "completed.json").read_text(encoding="utf-8"))
        value = json.loads(manifest.read_text(encoding="utf-8"))
        value["annotation_output"]["zip"] = str(self.run / "outside.zip")
        raw = (json.dumps(value) + "\n").encode()
        manifest.write_bytes(raw)
        metadata["files_sha256"]["manifest.jsonl"] = hashlib.sha256(raw).hexdigest()
        (chunk / "completed.json").write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "escaped"):
            CheckpointStore(self.run, resume=True)

    def test_no_document_rejected_record_counts_and_resume(self):
        value = record("J:7")
        value.update(classification="rejected", changed=False, actions=[], unresolved=[{"code": "ORPHAN_JSON"}])
        with CheckpointStore(self.run, checkpoint_every=1) as store:
            store.write_record(value, None, "unused.json")
            store.complete_photo("J:7")
        again = CheckpointStore(self.run, resume=True)
        self.assertEqual(again.recover()["counters"]["exported_json"], 0)
        self.assertEqual(again.recover()["counters"]["unresolved"], {"ORPHAN_JSON": 1})
        again.close()

    def test_forged_extra_completed_photo_id_rejected(self):
        with CheckpointStore(self.run, checkpoint_every=1) as store:
            store.write_record(record("P0:one"), b"{}", "one.json")
            store.complete_photo("P0:one")
        completed = self.run / "chunks" / "part_000001" / "completed.json"
        metadata = json.loads(completed.read_text(encoding="utf-8"))
        metadata["completed_photo_ids"].append("P0:unprocessed")
        completed.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exactly match"):
            CheckpointStore(self.run, resume=True)

    def test_photo_without_record_cannot_be_marked_complete(self):
        with CheckpointStore(self.run) as store:
            with self.assertRaisesRegex(ValueError, "without output records"):
                store.complete_photo("P0:unprocessed")
        resumed = CheckpointStore(self.run, resume=True)
        self.assertEqual(resumed.completed_photo_ids, set())
        resumed.close()

    def test_unmanifested_zip_member_cannot_be_committed(self):
        store = CheckpointStore(self.run, checkpoint_every=20)
        store.write_record(record("P0:one"), b"{}", "one.json")
        store.complete_photo("P0:one")
        # Mimics interruption after ZIP bytes are written, before the result
        # enters write_record's uncompleted-record list.
        store._active["archives"]["auto_fixed"].writestr("half_photo.json", b"{}")
        with self.assertRaisesRegex(ValueError, "Unexpected or duplicated"):
            store.commit()
        self.assertEqual(store.completed_photo_ids, set())
        self.assertFalse((self.run / "chunks" / "part_000001").exists())
        store.close(commit=False)
        resumed = CheckpointStore(self.run, resume=True)
        self.assertEqual(resumed.completed_photo_ids, set())
        resumed.close()

    def test_interrupted_writestr_poisoned_batch_cannot_be_committed(self):
        store = CheckpointStore(self.run, checkpoint_every=20)
        store.write_record(record("P0:one"), b"{}", "one.json")
        store.complete_photo("P0:one")
        archive = store._active["archives"]["auto_fixed"]
        original = archive.writestr
        def interrupt_after_write(name, data):
            original(name, data)
            raise KeyboardInterrupt
        with patch.object(archive, "writestr", side_effect=interrupt_after_write):
            with self.assertRaises(KeyboardInterrupt):
                store.write_record(record("P0:two"), b"{}", "two.json")
        with self.assertRaisesRegex(RuntimeError, "failed annotation write"):
            store.commit()
        store.close(commit=False)
        resumed = CheckpointStore(self.run, resume=True)
        self.assertEqual(resumed.completed_photo_ids, set())
        resumed.close()

    def test_retry_commit_after_marker_written(self):
        store = CheckpointStore(self.run, checkpoint_every=20)
        store.write_record(record("P0:one"), b"{}", "one.json")
        store.complete_photo("P0:one")
        with patch.object(store, "_validate_chunk", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                store.commit()
        store.commit()
        self.assertEqual(store.completed_photo_ids, {"P0:one"})
        store.close()

    def test_unmanifested_file_cannot_be_committed(self):
        store = CheckpointStore(self.run, layout="files", checkpoint_every=20)
        store.write_record(record("P0:one"), b"{}", "one.json")
        store.complete_photo("P0:one")
        (store._active["path"] / "annotations" / "auto_fixed" / "half_photo.json").write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "without manifest records"):
            store.commit()
        store.close(commit=False)
        resumed = CheckpointStore(self.run, layout="files", resume=True)
        self.assertEqual(resumed.completed_photo_ids, set())
        resumed.close()

    def test_interrupt_after_rename_keeps_committed_chunk_and_cached_totals(self):
        store = CheckpointStore(self.run, checkpoint_every=20)
        store.write_record(record("P0:one"), b"{}", "one.json")
        store.complete_photo("P0:one")
        original = Path.rename
        def interrupt_after_rename(path, target):
            result = original(path, target)
            if path.name.startswith(".pending_"):
                raise KeyboardInterrupt
            return result
        with patch("checkpoint_store.Path.rename", side_effect=interrupt_after_rename, autospec=True):
            with self.assertRaises(KeyboardInterrupt):
                store.commit()
        self.assertEqual(store.completed_photo_ids, {"P0:one"})
        self.assertEqual(store.chunk_count, 1)
        self.assertEqual(store.counters["exported_json"], 1)
        self.assertGreater(store.bytes_written, 0)
        archive = self.run / "chunks" / "part_000001" / "annotations" / "auto_fixed.zip"
        before = archive.read_bytes()
        store.close(commit=False)
        self.assertEqual(archive.read_bytes(), before)
        self.assertFalse(any(p.name.startswith(".abandoned_") for p in (self.run / "chunks").iterdir()))
        resumed = CheckpointStore(self.run, resume=True)
        self.assertEqual(resumed.completed_photo_ids, {"P0:one"})
        self.assertEqual(resumed.counters["exported_json"], 1)
        resumed.close()


if __name__ == "__main__":
    unittest.main()

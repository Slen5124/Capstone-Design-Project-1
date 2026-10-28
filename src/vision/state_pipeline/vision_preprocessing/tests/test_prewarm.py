"""prewarm: decrypt/resize every photo once into the review cache; the review server then never reads the ZIP."""
import contextlib
import io
import unittest

from vision_preprocessing.prewarm import cache_path, prewarm
from vision_preprocessing.tests.test_pipeline import PipelineCase


class TestPrewarm(PipelineCase):
    def test_prewarm_then_server_uses_cache(self):
        with contextlib.redirect_stdout(io.StringIO()):
            r = prewarm(self.ctx, workers=2)
            again = prewarm(self.ctx, workers=2)
        self.assertGreater(r["written"], 0)
        self.assertEqual(r["errors"], 0)
        self.assertEqual(again.get("written", 0), 0)                 # resumable: nothing twice
        im = self.v1[0]
        self.assertTrue(cache_path(self.ctx.run_dir, im["image_id"]).exists())
        from vision_preprocessing.review_app import ReviewService
        from vision_preprocessing.runctx import RunContext
        svc = ReviewService(RunContext.open(self.ctx.run_dir))
        try:
            svc.reader.photo_bytes = lambda *a, **k: (_ for _ in ()).throw(AssertionError("ZIP read"))
            self.assertTrue(svc.jpeg(im["image_id"], 1600))
            self.assertTrue(svc.jpeg(im["image_id"], 960))           # derived from the cached 1600 file
        finally:
            svc.reader.close()


if __name__ == "__main__":
    unittest.main()

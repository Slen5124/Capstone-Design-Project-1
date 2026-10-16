"""State-label preprocessing for construction-site images (wall / floor / ceiling).

This package *adds* a physical-target, activity-state and review layer on top of
the existing qa_v2 / qa_v3 automatic annotation results. It never modifies the
original photo ZIPs, the source JSON ZIP, or ``automatic_results``.

Everything produced automatically is a draft. Only approvals recorded by a
named human reviewer in the review store make a (target, activity, label
version) usable as training ground truth.
"""

SCHEMA_VERSION = "vision_preprocessing/1.0"
__all__ = ["SCHEMA_VERSION"]

"""The vocabulary a training corpus is partitioned with — spec §32.

One closed list. Spec §32 requires the train/validation/test split to avoid
leakage by grouping on the physical card, the source, the card instance or the
slab; *what* the groups are is the splitter's question, and *what a group may be
assigned to* is this one. Three members, and every one of them has code that
reads it: a fourth would be a change to how a model is evaluated, not a fact
about a corpus.

Here rather than in `tcg_api.datasets.tables` because two things need it and
they are on opposite sides of the service boundary. The database CHECK on
`dataset_members.split` is one; spec §32's splitter is the other, and epic #7
describes it as "a pure function over grouping keys" — a pure function that had
to import `services/api` to name a split would not be one.

Members are `str`, as :class:`~tcg_domain.analysis.AnalysisStatus`'s are, so the
schema stores the value and never the member's repr.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["DatasetSplit"]


class DatasetSplit(StrEnum):
    """Which partition of a dataset version a member image belongs to — spec §32.

    Declared in the order a corpus is conventionally reported, which is also the
    order the split proportions are quoted in.
    """

    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"

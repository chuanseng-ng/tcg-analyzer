"""The vocabularies an analysis is described with — spec §65, §19, §11 and §52.

Four closed lists, and the graph of edges between one of them. An analysis has a
state, a session has a state, an image has a side and a quality verdict; what an
analysis *is* belongs to the issues that give it behaviour. This module fixes
the words and which word may follow which, so that the database's CHECK
constraints, the API and the job runner cannot each spell them differently or
disagree about what may happen next.

**Why these are enums when `Game` and `Language` deliberately are not.**
`card.py` argues that a game is "a validated field, not an enum" because a
second TCG must be a row of data rather than a migration. The reasoning
inverts here. A game nobody wrote code for is still a perfectly good game; an
analysis state nobody wrote code for is a row no transition can leave. Spec §65
closes the list at nine, §19 at four, and §11 plus §52 at six, and every member
of each has code that enters it. A tenth would be a feature, not a fact.

Members are `str`, exactly as :class:`~tcg_domain.card.Game`'s are, so they may
be passed anywhere a `str` is wanted and compare equal to their value. The
schema stores the value, never the member's repr.

**`queued` is not a state.** Spec §65 shows ``POST /analyses/{id}/run``
answering ``status = queued``, and then lists nine states without it. It is a
transport word for "accepted, not started" that #35's endpoint returns; no row
ever holds it. Do not add a tenth member for it.

**`SessionStatus` is this project's, not the specification's.** §12 gives
`analysis_sessions` a `status` column and never says what may go in it — the
nine states above are an *analysis's*. The three below are the smallest
vocabulary that says something true: a session is usable, or it is past its
`expires_at`, or the retention job (§54) has deleted its images and kept the row
so the deletion itself is auditable.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Final

__all__ = [
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "V1_SIDES",
    "AnalysisStatus",
    "ImageSide",
    "QualityStatus",
    "SessionStatus",
    "can_transition",
    "legal_predecessors",
]


class AnalysisStatus(StrEnum):
    """Spec §65's nine states, declared in the order the specification lists them.

    The order is the happy path, and it is worth keeping: `identifying` precedes
    `awaiting_confirmation` because spec §20 forbids acting on an identification
    the user has not confirmed, and `analyzing` precedes `calculating` because
    the master architectural rule keeps grading separate from economics.
    """

    CREATED = "created"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    IDENTIFYING = "identifying"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    ANALYZING = "analyzing"
    CALCULATING = "calculating"
    COMPLETED = "completed"
    FAILED = "failed"


class SessionStatus(StrEnum):
    """Whether an anonymous session is usable, lapsed, or emptied.

    Spec §53 gives V1 anonymous sessions and no accounts, so nothing here
    identifies a person: a session is a container with a lifetime.
    """

    ACTIVE = "active"
    #: Neither of these is written in V1, and that is #41's decision rather than
    #: an omission. The retention sweep *deletes* an expired session — the row's
    #: `anonymous_session_id` is the browser's bearer token, so keeping it to
    #: record that its images were deleted keeps a per-browser identifier
    #: forever, which is what spec §53 and §54 argue against. What is audited is
    #: the `retention.swept` log line. They stay in the vocabulary because
    #: "lapsed" and "emptied" are states this schema admits, and because
    #: removing a value from the CHECK costs a migration and buys nothing.
    EXPIRED = "expired"
    PURGED = "purged"


class ImageSide(StrEnum):
    """Which view of the card an image is — spec §11.

    All six exist from the first migration. Only :data:`V1_SIDES` are written in
    V1; the other four are spec §52's guided-photography flow, which the V1
    image pipeline "must be compatible with". Admitting them now costs a longer
    CHECK constraint and saves a migration against a table holding user data.
    """

    FRONT = "front"
    BACK = "back"
    ANGLED_FRONT = "angled_front"
    ANGLED_BACK = "angled_back"
    SURFACE_FRONT = "surface_front"
    SURFACE_BACK = "surface_back"


class QualityStatus(StrEnum):
    """What the image-quality gate concluded — spec §19.

    §19 also fixes what each means for the pipeline: `unusable` stops the
    analysis, `poor` continues but the user must be told. `good` and
    `acceptable` proceed silently. The gate itself is an OpenCV heuristic in M2
    and a model in M7; this vocabulary does not change when it does.
    """

    GOOD = "good"
    ACCEPTABLE = "acceptable"
    POOR = "poor"
    UNUSABLE = "unusable"


#: The states an analysis does not leave. `completed_at` is only meaningful for
#: one of these, which the schema enforces, and #35's state machine allows no
#: transition out of either.
TERMINAL_STATUSES: Final = frozenset({AnalysisStatus.COMPLETED, AnalysisStatus.FAILED})

#: The sides V1 actually captures — front and back upload, per the V1 scope.
#: The other four members of :class:`ImageSide` are storable and unwritten.
V1_SIDES: Final = (ImageSide.FRONT, ImageSide.BACK)


# ---------------------------------------------------------------------------
# The state machine — spec §65, in the order §65 lists the states.
#
# Data rather than code, and here rather than beside the job runner, for the
# reason the vocabularies are here: the runner is one of three things that has
# to agree about what may happen next. The API refuses a `run` on an analysis
# with no images by consulting this; the runner claims one by consulting this;
# a test asserts against this rather than against either.
#
# Enforcement is emphatically *not* here. `tcg_api.analysis.state.transition`
# turns :func:`legal_predecessors` into a `WHERE` clause, so an illegal move is
# refused by PostgreSQL in the same statement that performs a legal one. A
# `can_transition` check followed by an `UPDATE` would be two statements with a
# race between them, and the race is exactly the duplicate delivery an
# at-least-once queue guarantees will happen.
# ---------------------------------------------------------------------------

#: Which states each state may move to. Every non-terminal state may also move
#: to `failed`, which is folded in below rather than repeated nine times: a
#: failure is not a step in the pipeline and does not deserve to read like one.
#:
#: There are no self-edges. A transition that changes nothing is not something
#: the runner should be able to mistake for progress, and its `UPDATE` would
#: report a row it had not moved.
_FORWARD: Final[Mapping[AnalysisStatus, AnalysisStatus]] = {
    AnalysisStatus.CREATED: AnalysisStatus.UPLOADING,
    AnalysisStatus.UPLOADING: AnalysisStatus.UPLOADED,
    AnalysisStatus.UPLOADED: AnalysisStatus.IDENTIFYING,
    AnalysisStatus.IDENTIFYING: AnalysisStatus.AWAITING_CONFIRMATION,
    AnalysisStatus.AWAITING_CONFIRMATION: AnalysisStatus.ANALYZING,
    AnalysisStatus.ANALYZING: AnalysisStatus.CALCULATING,
    AnalysisStatus.CALCULATING: AnalysisStatus.COMPLETED,
}

TRANSITIONS: Final[Mapping[AnalysisStatus, frozenset[AnalysisStatus]]] = {
    status: (
        frozenset()
        if status in TERMINAL_STATUSES
        else frozenset({_FORWARD[status], AnalysisStatus.FAILED})
    )
    for status in AnalysisStatus
}

#: The inverse, computed once, because a `WHERE status IN (...)` is the shape
#: every caller actually needs: a transition is applied by naming the states it
#: is allowed to be applied *from*.
_PREDECESSORS: Final[Mapping[AnalysisStatus, frozenset[AnalysisStatus]]] = {
    target: frozenset(source for source, targets in TRANSITIONS.items() if target in targets)
    for target in AnalysisStatus
}


def legal_predecessors(target: AnalysisStatus) -> frozenset[AnalysisStatus]:
    """The states an analysis may be in for a move to `target` to be legal.

    Empty for :data:`AnalysisStatus.CREATED`, which is where a row starts rather
    than somewhere it moves to — so a transition *to* `created` is refused by
    the same `IN ()` that refuses every other illegal move, with no special case.
    """
    return _PREDECESSORS[target]


def can_transition(current: AnalysisStatus, target: AnalysisStatus) -> bool:
    """Whether an analysis in `current` may move to `target`.

    The readable form of the same fact :func:`legal_predecessors` gives the
    database. Use it to *explain* a refusal, never to authorise a write — see
    the note above.
    """
    return target in TRANSITIONS[current]

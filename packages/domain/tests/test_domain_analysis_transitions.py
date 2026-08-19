"""Unit tests for the analysis state machine — issue #35, spec §65.

Same discipline as `test_domain_analysis.py`: the expectation is transcribed
rather than derived. The graph below is written out by hand from §65's ordered
list, so a test failure means the code moved and not that the code agrees with
itself.

The one property worth stating plainly, because it is what the rest of the
system leans on: **the graph is a line with an escape hatch.** Every state has
exactly one successor along the pipeline, plus `failed`; nothing branches, and
nothing goes backwards. That is what makes `legal_predecessors` a short `IN`
list and what makes a duplicate job delivery a no-op rather than a rewind.
"""

from __future__ import annotations

import pytest
from tcg_domain.analysis import (
    TERMINAL_STATUSES,
    TRANSITIONS,
    AnalysisStatus,
    can_transition,
    legal_predecessors,
)

#: Spec §65's happy path, transcribed. Each pair is "and then".
PIPELINE = (
    (AnalysisStatus.CREATED, AnalysisStatus.UPLOADING),
    (AnalysisStatus.UPLOADING, AnalysisStatus.UPLOADED),
    (AnalysisStatus.UPLOADED, AnalysisStatus.IDENTIFYING),
    (AnalysisStatus.IDENTIFYING, AnalysisStatus.AWAITING_CONFIRMATION),
    (AnalysisStatus.AWAITING_CONFIRMATION, AnalysisStatus.ANALYZING),
    (AnalysisStatus.ANALYZING, AnalysisStatus.CALCULATING),
    (AnalysisStatus.CALCULATING, AnalysisStatus.COMPLETED),
)

NON_TERMINAL = tuple(status for status in AnalysisStatus if status not in TERMINAL_STATUSES)


@pytest.mark.parametrize(("current", "target"), PIPELINE, ids=lambda status: status.value)
def test_each_step_of_the_pipeline_is_legal(
    current: AnalysisStatus, target: AnalysisStatus
) -> None:
    assert can_transition(current, target)


@pytest.mark.parametrize("current", NON_TERMINAL, ids=lambda status: status.value)
def test_anything_still_running_can_fail(current: AnalysisStatus) -> None:
    """A failure is reachable from every state that has not finished.

    Otherwise a stage that raises would have nowhere to put the analysis, and
    the job runner's dead-letter path would leave the row mid-pipeline forever.
    """
    assert can_transition(current, AnalysisStatus.FAILED)


@pytest.mark.parametrize("current", sorted(TERMINAL_STATUSES), ids=lambda status: status.value)
def test_a_terminal_state_is_not_left(current: AnalysisStatus) -> None:
    """`completed` and `failed` are the end. A retry starts a new analysis."""
    assert TRANSITIONS[current] == frozenset()
    for target in AnalysisStatus:
        assert not can_transition(current, target)


@pytest.mark.parametrize("status", tuple(AnalysisStatus), ids=lambda status: status.value)
def test_no_state_moves_to_itself(status: AnalysisStatus) -> None:
    """A move that changes nothing must not be mistakable for progress.

    `state.transition` reports whether it moved a row; a self-edge would make
    "already there" and "just arrived" the same answer, and that answer is what
    tells a duplicate delivery to stop.
    """
    assert not can_transition(status, status)


def test_the_pipeline_never_runs_backwards() -> None:
    """No state reaches an earlier one, so an analysis cannot be rewound.

    Asserted over the whole graph rather than per edge: it is a property of the
    order §65 declares, and the enum's own order is that order.
    """
    order = {status: index for index, status in enumerate(AnalysisStatus)}

    for current, targets in TRANSITIONS.items():
        for target in targets:
            assert order[target] > order[current], f"{current} -> {target}"


def test_every_state_is_reachable_from_created() -> None:
    """A state nothing can enter is a row no transition can produce."""
    reached = {AnalysisStatus.CREATED}
    frontier = [AnalysisStatus.CREATED]
    while frontier:
        for target in TRANSITIONS[frontier.pop()]:
            if target not in reached:
                reached.add(target)
                frontier.append(target)

    assert reached == set(AnalysisStatus)


def test_created_is_where_a_row_starts_and_never_somewhere_it_moves() -> None:
    """So a transition *to* `created` is refused by the empty `IN ()`, unspecially."""
    assert legal_predecessors(AnalysisStatus.CREATED) == frozenset()


@pytest.mark.parametrize(("current", "target"), PIPELINE, ids=lambda status: status.value)
def test_the_predecessors_of_a_pipeline_step_are_exactly_one_state(
    current: AnalysisStatus, target: AnalysisStatus
) -> None:
    assert legal_predecessors(target) == frozenset({current})


def test_everything_unfinished_precedes_failure() -> None:
    """The one target with more than one predecessor, which is why it is folded in."""
    assert legal_predecessors(AnalysisStatus.FAILED) == frozenset(NON_TERMINAL)

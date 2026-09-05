"""The evaluation runner's pure seams — pairing, the envelope, the log file.

The IO orchestration (storage reads, analyzer runs) follows
`tcg_api.datasets.normalization`'s shape and is exercised against the live
corpus by hand; what is asserted here is everything that decides what a
committed experiment record says.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from tcg_api.datasets.evaluation import (
    EXPERIMENTS_DIR,
    experiment_path,
    pair_copies,
    render_experiment,
    write_experiment,
)
from tcg_ml_evaluation import EVALUATION_VERSION

REPO_ROOT = Path(__file__).resolve().parents[3]

FRONT = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
BACK = uuid.UUID("00000000-0000-0000-0000-0000000000ab")
COPY = uuid.UUID("00000000-0000-0000-0000-00000000c0c0")


def test_two_sides_of_one_copy_pair_up_for_the_compose_replay() -> None:
    pairs = pair_copies({FRONT: ("front", COPY), BACK: ("back", COPY)})

    assert pairs == [(FRONT, BACK)]


def test_an_image_naming_no_copy_has_no_pair() -> None:
    assert pair_copies({FRONT: ("front", None), BACK: ("back", None)}) == []


def test_a_copy_with_one_side_does_not_pair() -> None:
    assert pair_copies({FRONT: ("front", COPY)}) == []


def test_a_duplicate_side_on_one_copy_keeps_the_first_image_seen() -> None:
    """A re-photographed side must not make the compose replay nondeterministic.

    The runner's select is ordered by image id, so first-seen is a stable
    choice rather than a query-plan accident.
    """
    second_front = uuid.UUID("00000000-0000-0000-0000-0000000000ac")

    pairs = pair_copies(
        {FRONT: ("front", COPY), second_front: ("front", COPY), BACK: ("back", COPY)}
    )

    assert pairs == [(FRONT, BACK)]


def test_an_analyzer_exception_excludes_the_image_rather_than_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One bad artifact must cost one image, never the whole record."""
    from tcg_api.datasets import evaluation

    def explode(data: bytes, frame: object, *, side: object) -> object:
        raise ValueError("cv2 surprise")

    monkeypatch.setattr(evaluation, "_predict", explode)
    excluded: dict[uuid.UUID, str] = {}

    result = evaluation.predict_or_exclude(
        FRONT,
        b"bytes",
        None,
        side=None,
        excluded=excluded,  # type: ignore[arg-type]
    )

    assert result is None
    assert excluded == {FRONT: "analyzer_error"}


def test_the_experiment_file_is_named_by_dataset_and_harness_version() -> None:
    path = experiment_path("pokemon-condition-v0.1.0")

    assert path.parent == EXPERIMENTS_DIR
    assert path.name == f"pokemon-condition-v0.1.0+{EVALUATION_VERSION}.json"
    assert EXPERIMENTS_DIR == REPO_ROOT / "ml" / "evaluation" / "experiments"


def test_the_envelope_carries_the_versions_the_thresholds_and_the_commit() -> None:
    """Spec §61: dataset, model, hyperparameters, metrics, git commit.

    For a scored heuristic the model is the version constants and the
    hyperparameters are the thresholds records; hardware and training
    duration do not apply and are deliberately absent.
    """
    text = render_experiment({"dataset_version": "pokemon-condition-v0.1.0"}, commit="abc123")

    payload = json.loads(text)
    assert payload["dataset_version"] == "pokemon-condition-v0.1.0"
    assert payload["git_commit"] == "abc123"
    assert payload["condition_version"].startswith("condition-compose-")
    assert payload["analyzer_thresholds"]["surface_stain_max_value"] is not None
    assert "hardware" not in payload
    assert text.endswith("\n")
    assert text == render_experiment(
        {"dataset_version": "pokemon-condition-v0.1.0"}, commit="abc123"
    )


def test_an_existing_experiment_record_is_never_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "run.json"
    write_experiment("{}\n", target)

    with pytest.raises(FileExistsError):
        write_experiment("{}\n", target)


def test_written_bytes_use_line_feeds_on_every_platform(tmp_path: Path) -> None:
    target = tmp_path / "run.json"
    write_experiment('{\n  "a": 1\n}\n', target)

    assert b"\r" not in target.read_bytes()


def test_a_second_harness_names_its_own_record_family() -> None:
    """The grade runner (#242) shares this path but not this version constant."""
    path = experiment_path("pokemon-condition-v0.2.0", harness="grade-evaluation-v0.1.0")

    assert path == EXPERIMENTS_DIR / "pokemon-condition-v0.2.0+grade-evaluation-v0.1.0.json"

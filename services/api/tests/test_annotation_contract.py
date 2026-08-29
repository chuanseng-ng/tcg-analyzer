"""What the annotation surface promises, asserted without a database — #160.

`test_annotation_endpoint.py` needs a live PostgreSQL because the rules it checks
are CHECK constraints. Nothing here does: these are claims about the *contract* —
which vocabularies reach a client, and what the write module is not allowed to
contain — and they must fail on a machine with no database, because a
vocabulary that stopped reaching `apps/annotation` would otherwise be discovered
by an annotator rather than by CI.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import pytest
from pydantic import BaseModel
from tcg_api.app import create_app
from tcg_api.routers.annotation import (
    CornerMarkerRequest,
    EdgeMarkerRequest,
    SurfaceMarkerRequest,
)
from tcg_domain.annotation import LABELS_BY_KIND, REGIONS_BY_KIND, AnnotationKind

MODELS: dict[AnnotationKind, type[BaseModel]] = {
    AnnotationKind.CORNER: CornerMarkerRequest,
    AnnotationKind.EDGE: EdgeMarkerRequest,
    AnnotationKind.SURFACE: SurfaceMarkerRequest,
}


def _members(model: type[BaseModel], field: str) -> set[str]:
    """The enum members a field admits, or nothing where it has no field."""
    info = model.model_fields.get(field)
    if info is None:
        return set()
    vocabulary = info.annotation
    assert isinstance(vocabulary, type) and issubclass(vocabulary, StrEnum)
    return {member.value for member in vocabulary}


@pytest.mark.parametrize("kind", list(AnnotationKind), ids=lambda kind: kind.value)
def test_each_kind_offers_exactly_the_labels_its_specification_section_lists(
    kind: AnnotationKind,
) -> None:
    """The request models and the schema's CHECK read the same three lists.

    `image_annotations`' constraint is composed from `LABELS_BY_KIND`; these
    models are typed from the enums that mapping is built from. A label added to
    one and not the other would be a label the database takes and the endpoint
    refuses, or the reverse, and either is discovered here rather than in front of
    somebody with several hundred cards to get through.
    """
    assert _members(MODELS[kind], "label") == set(LABELS_BY_KIND[kind])


@pytest.mark.parametrize("kind", list(AnnotationKind), ids=lambda kind: kind.value)
def test_each_kind_offers_exactly_the_regions_its_specification_section_lists(
    kind: AnnotationKind,
) -> None:
    """And a surface offers none at all, because §16 names no positions.

    `SurfaceMarkerRequest` has no `region` field, so `_members` answers with the
    empty set — which is what `REGIONS_BY_KIND` holds for it, deliberately: the
    mapping stays total so the constraint reading it needs no special case.
    """
    assert _members(MODELS[kind], "region") == set(REGIONS_BY_KIND[kind])


def test_the_three_vocabularies_reach_the_openapi_schema_separately() -> None:
    """ADR 0001 makes the schema the only way `apps/annotation` learns a shape.

    One model with `label: str` would have compiled and passed every test above
    while leaving the tool to keep its own copy of twenty-eight strings — free to
    drift, and free to offer `rough_cut` for a corner. This asserts the partition
    survives into the document a client generates from.
    """
    schemas = create_app().openapi()["components"]["schemas"]

    corner = schemas["CornerMarkerRequest"]["properties"]["label"]
    edge = schemas["EdgeMarkerRequest"]["properties"]["label"]

    # Both are `$ref`s to the enum schemas, which is what carries the members.
    corner_labels = set(schemas[corner["$ref"].rsplit("/", 1)[-1]]["enum"])
    edge_labels = set(schemas[edge["$ref"].rsplit("/", 1)[-1]]["enum"])

    assert "rough_cut" in edge_labels
    assert "rough_cut" not in corner_labels
    assert "crease" in corner_labels
    assert "crease" not in edge_labels


def test_nothing_in_the_write_module_updates_or_deletes_a_row() -> None:
    """There is no edit path, and a source-level assertion keeps it that way.

    `trg_image_annotations_immutable` refuses an `UPDATE` at runtime, so an "edit
    annotation" function would 500 in front of somebody who had just lost an
    afternoon. The trigger is the guarantee; this is what stops one being written
    in the first place — `test_nothing_here_adds_a_member_to_an_existing_version`'s
    pattern.
    """
    source = (Path(__file__).resolve().parents[1] / "src/tcg_api/datasets/annotation.py").read_text(
        encoding="utf-8"
    )

    assert "sa.update(" not in source
    assert "sa.delete(" not in source

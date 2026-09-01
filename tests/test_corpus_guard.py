"""The guard that stops `uv run pytest` from truncating the training corpus.

Issue #196: every integration fixture runs `TRUNCATE ... RESTART IDENTITY
CASCADE` against whatever database `TCG_API_DATABASE_URL` names, and since #181
one such database also holds the corpus. It happened once — rows, copies and
fingerprints to zero — and the only thing that stopped it costing the
annotations was that none existed yet. The images re-ingest from disk; hours of
a person's judgement do not.

What is tested here is the *decision*, not the counting: given what a database
holds, does the session stop and does the message tell the operator enough to
act. The count itself is one `SELECT count(*)` per table and needs no test.
"""

from __future__ import annotations

from conftest import CORPUS_TABLES, _corpus_rows, corpus_guard_message

_URL = "postgresql+asyncpg://tcg:hunter2@localhost:5432/tcg_corpus"


def test_an_empty_database_is_not_the_corpus():
    """The dev database on a normal run. Nothing to protect, nothing to say."""
    assert corpus_guard_message(_URL, {}) is None


def test_a_zero_count_is_not_a_finding():
    """A migrated but empty corpus domain is the ordinary case, not a hazard."""
    assert corpus_guard_message(_URL, dict.fromkeys(CORPUS_TABLES, 0)) is None


def test_rows_stop_the_session_and_name_what_is_at_stake():
    message = corpus_guard_message(_URL, {"training_images": 28, "image_annotations": 47})

    assert message is not None
    # The database, so the operator knows which URL they exported.
    assert "tcg_corpus" in message
    # Every populated table with its count, so the loss is legible before it happens.
    assert "training_images" in message
    assert "28" in message
    assert "image_annotations" in message
    assert "47" in message
    # The issue, so a reader of the abort can find out why this exists.
    assert "#196" in message


def test_the_message_does_not_leak_the_password():
    """The URL carries credentials and an abort message is printed and pasted."""
    message = corpus_guard_message(_URL, {"training_images": 1})

    assert message is not None
    assert "hunter2" not in message


def test_the_annotation_tables_are_guarded():
    """The images re-ingest from disk; the annotations are the unrecoverable half."""
    assert "image_annotations" in CORPUS_TABLES
    assert "centering_measurements" in CORPUS_TABLES


def test_a_database_that_is_not_running_counts_as_empty():
    """A developer with the variable exported and no Postgres gets the usual skip.

    A refused connection arrives as `ConnectionRefusedError`, an `OSError`, with
    no driver exception wrapping it — so catching `SQLAlchemyError` alone turns
    a stopped container into a crash at session start.
    """
    assert _corpus_rows("postgresql+asyncpg://tcg:tcg@127.0.0.1:59999/nope") == {}

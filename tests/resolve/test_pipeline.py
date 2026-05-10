"""End-to-end pipeline tests: stub embeddings, stub Anthropic, stub DB.

Focuses on idempotence (two runs over same input produce the same claim set)
and the overall orchestration flow.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, call, patch

import pytest

from fundprint.resolve.pipeline import RunResult, run
from fundprint.resolve.version import RESOLVER_VERSION


def _staging_row(name: str, city: str = "Austin", state: str = "TX") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "raw_name": name,
        "city": city,
        "state": state,
        "source_record_id": str(uuid.uuid4()),
    }


def _make_candidate_rows(table: str, n: int = 1) -> list[tuple]:
    """Generate fake candidate rows for get_candidates mocking."""
    rows = []
    for i in range(n):
        rows.append((f"{table}-id-{i}", table, 0.10 + i * 0.05, 0.90 - i * 0.05))
    return rows


def _make_verify_claim(link: bool = True, confidence: float = 0.80, snippets: list[str] | None = None):
    """Build a stub VerificationClaim."""
    from fundprint.resolve.verify import VerificationClaim
    from fundprint.resolve.version import PROMPT_VERSION, RESOLVER_VERSION
    from fundprint.resolve.verify import RESOLVER_MODEL

    return VerificationClaim(
        link=link,
        confidence=confidence,
        supporting_snippets=snippets or ["confirmed by portfolio page"],
        flags=[],
        prompt_version=PROMPT_VERSION,
        resolver_version=RESOLVER_VERSION,
        model=RESOLVER_MODEL,
    )


@pytest.fixture
def mock_conn():
    conn = MagicMock()
    # _claim_exists returns None (not yet processed)
    conn.execute.return_value.fetchone.return_value = None
    conn.execute.return_value.fetchall.return_value = []
    return conn


@pytest.fixture
def patch_embed():
    with patch("fundprint.resolve.candidate.embed") as m:
        m.return_value = ([[0.1] * 1024], "stub-0")
        yield m


@pytest.fixture
def patch_candidates():
    """Patch get_candidates to return one high-similarity owner_entity candidate."""
    from fundprint.resolve.candidate import Candidate

    cand = Candidate(
        target_id="owner-entity-uuid",
        target_table="owner_entity",
        similarity=0.88,
        distance=0.12,
    )
    with patch("fundprint.resolve.pipeline.get_candidates") as m:
        m.return_value = [cand]
        yield m


@pytest.fixture
def patch_verify_linked():
    with patch("fundprint.resolve.pipeline.verify") as m:
        m.return_value = _make_verify_claim(link=True, confidence=0.80)
        yield m


@pytest.fixture
def patch_chain():
    with patch("fundprint.resolve.pipeline.walk_chain") as m:
        m.return_value = MagicMock(clinic_id="some-clinic")
        yield m


class TestPipelineHappyPath:
    def test_processes_staging_rows_and_writes_claims(
        self, mock_conn, patch_candidates, patch_verify_linked, patch_chain
    ):
        row = _staging_row("Sunshine ABA")

        # _fetch_unprocessed_rows returns one row; _claim_exists returns False
        call_results = [
            MagicMock(fetchall=MagicMock(return_value=[
                (row["id"], row["raw_name"], row["city"], row["state"], row["source_record_id"])
            ])),
            MagicMock(fetchone=MagicMock(return_value=None)),  # _claim_exists
            MagicMock(fetchone=MagicMock(return_value=None)),  # _write_claim INSERT
        ]
        mock_conn.execute.side_effect = call_results

        result = run(conn=mock_conn)

        assert result.staging_rows_processed == 1
        assert result.claims_written == 1

    def test_returns_run_result(self, mock_conn, patch_candidates, patch_verify_linked, patch_chain):
        mock_conn.execute.return_value.fetchall.return_value = []
        result = run(conn=mock_conn)
        assert isinstance(result, RunResult)


class TestPipelineIdempotence:
    def test_second_run_skips_already_processed_rows(
        self, mock_conn, patch_candidates, patch_verify_linked, patch_chain
    ):
        """If _claim_exists returns True, the row is counted as skipped."""
        row = _staging_row("Repeat Clinic")

        # First execute: fetch staging rows; second: _claim_exists returns True
        call_results = [
            MagicMock(fetchall=MagicMock(return_value=[
                (row["id"], row["raw_name"], row["city"], row["state"], row["source_record_id"])
            ])),
            MagicMock(fetchone=MagicMock(return_value=(1,))),  # already exists
        ]
        mock_conn.execute.side_effect = call_results

        result = run(conn=mock_conn)

        assert result.claims_skipped_idempotent == 1
        assert result.claims_written == 0
        # verify should not have been called at all
        patch_verify_linked.assert_not_called()

    def test_same_input_produces_same_claim_set(
        self, patch_candidates, patch_verify_linked, patch_chain
    ):
        """Two sequential runs with identical unprocessed rows produce identical claim counts."""
        row = _staging_row("Consistent Clinic")

        def _make_run_conn():
            conn = MagicMock()
            conn.execute.side_effect = [
                MagicMock(fetchall=MagicMock(return_value=[
                    (row["id"], row["raw_name"], row["city"], row["state"], row["source_record_id"])
                ])),
                MagicMock(fetchone=MagicMock(return_value=None)),
                MagicMock(fetchone=MagicMock(return_value=None)),
            ]
            return conn

        result_1 = run(conn=_make_run_conn())
        result_2 = run(conn=_make_run_conn())

        assert result_1.claims_written == result_2.claims_written


class TestPipelineVerifyRejection:
    def test_no_link_from_verify_does_not_write_claim(
        self, mock_conn, patch_candidates, patch_chain
    ):
        row = _staging_row("No Match Clinic")
        call_results = [
            MagicMock(fetchall=MagicMock(return_value=[
                (row["id"], row["raw_name"], row["city"], row["state"], row["source_record_id"])
            ])),
            MagicMock(fetchone=MagicMock(return_value=None)),
        ]
        mock_conn.execute.side_effect = call_results

        with patch("fundprint.resolve.pipeline.verify") as mock_verify:
            mock_verify.return_value = _make_verify_claim(link=False, snippets=[])
            result = run(conn=mock_conn)

        assert result.claims_written == 0

    def test_below_similarity_floor_skips_verify(self, mock_conn, patch_chain):
        """Candidates below the similarity floor never reach the verify step."""
        from fundprint.resolve.candidate import Candidate

        row = _staging_row("Low Similarity Clinic")
        low_sim_candidate = Candidate(
            target_id="owner-uuid",
            target_table="owner_entity",
            similarity=0.50,  # below _SIMILARITY_FLOOR
            distance=0.50,
        )
        call_results = [
            MagicMock(fetchall=MagicMock(return_value=[
                (row["id"], row["raw_name"], row["city"], row["state"], row["source_record_id"])
            ])),
            MagicMock(fetchone=MagicMock(return_value=None)),
        ]
        mock_conn.execute.side_effect = call_results

        with patch("fundprint.resolve.pipeline.get_candidates") as mock_cands:
            mock_cands.return_value = [low_sim_candidate]
            with patch("fundprint.resolve.pipeline.verify") as mock_verify:
                run(conn=mock_conn)
                mock_verify.assert_not_called()

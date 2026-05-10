"""Tests for the ValidationRun lifecycle.

Uses mock connections to verify append-only behavior without requiring a
live Postgres or SQLite instance. The SQL queries use %s placeholders
(psycopg style); adapting them to SQLite would couple the tests to the
adapter choice, not the behavior.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from fundprint.validate.audit import close_run, open_run, record_decision


def _fake_conn():
    conn = MagicMock()
    conn.execute.return_value = MagicMock()
    return conn


def _fake_claim(claim_type: str = "clinic_to_owner", confidence: float = 0.90):
    return SimpleNamespace(
        id=uuid.uuid4(),
        claim_type=claim_type,
        confidence_score=confidence,
        llm_flags=[],
    )


class TestRunLifecycle:
    def test_open_run_calls_execute(self):
        conn = _fake_conn()
        run_id = open_run(
            conn,
            methodology_version="v1.0",
            resolver_version="0.1.0",
        )
        assert isinstance(run_id, uuid.UUID)
        conn.execute.assert_called_once()
        # The INSERT query should mention the run id we got back.
        call_args = conn.execute.call_args
        params = call_args[0][1]  # second positional arg is the params tuple
        assert str(run_id) in params
        assert "0.1.0" in params
        assert "v1.0" in params

    def test_open_run_returns_uuid(self):
        conn = _fake_conn()
        run_id = open_run(conn, methodology_version="v1", resolver_version="0.1")
        assert isinstance(run_id, uuid.UUID)

    def test_close_run_calls_update(self):
        conn = _fake_conn()
        run_id = uuid.uuid4()
        close_run(
            conn,
            run_id=run_id,
            passed=True,
            counts={"evaluated": 10, "passed": 10, "failed": 0, "quarantined": 0},
        )
        conn.execute.assert_called_once()
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "validation_run" in sql

    def test_close_run_passes_counts(self):
        conn = _fake_conn()
        run_id = uuid.uuid4()
        close_run(
            conn,
            run_id=run_id,
            passed=False,
            counts={"evaluated": 5, "passed": 3, "failed": 2, "quarantined": 0},
        )
        params = conn.execute.call_args[0][1]
        # evaluated, passed, failed, quarantined appear in order in the params
        assert 5 in params
        assert 3 in params
        assert 2 in params

    def test_record_decision_passed(self):
        conn = _fake_conn()
        run_id = uuid.uuid4()
        claim = _fake_claim("clinic_to_owner", 0.95)
        record_decision(conn, run_id=run_id, claim=claim, passed=True)

        conn.execute.assert_called_once()
        params = conn.execute.call_args[0][1]
        assert "passed" in params
        assert "verified" in params

    def test_record_decision_failed(self):
        conn = _fake_conn()
        run_id = uuid.uuid4()
        claim = _fake_claim("owner_to_pe_firm", 0.50)
        record_decision(conn, run_id=run_id, claim=claim, passed=False)

        params = conn.execute.call_args[0][1]
        assert "failed" in params
        assert "unverified" in params

    def test_record_decision_quarantined(self):
        conn = _fake_conn()
        run_id = uuid.uuid4()
        claim = _fake_claim("owner_to_pe_firm", 0.50)
        record_decision(conn, run_id=run_id, claim=claim, passed=False, quarantined=True)

        params = conn.execute.call_args[0][1]
        assert "quarantined" in params
        assert "unverified" in params

    def test_deciding_rule_contains_claim_type(self):
        conn = _fake_conn()
        run_id = uuid.uuid4()
        claim = _fake_claim("clinic_to_owner")
        record_decision(conn, run_id=run_id, claim=claim, passed=True)

        params = conn.execute.call_args[0][1]
        # deciding_rule is in params; find the one containing the claim type
        rule_param = next((p for p in params if isinstance(p, str) and "clinic_to_owner" in p), None)
        assert rule_param is not None


class TestAppendOnly:
    def test_two_open_runs_produce_different_ids(self):
        """Each call to open_run produces a new UUID - not an upsert."""
        conn_a = _fake_conn()
        conn_b = _fake_conn()
        id_1 = open_run(conn_a, methodology_version="v1", resolver_version="0.1")
        id_2 = open_run(conn_b, methodology_version="v1", resolver_version="0.1")
        assert id_1 != id_2

    def test_record_decision_never_updates(self):
        """record_decision must INSERT, never UPDATE."""
        conn = _fake_conn()
        run_id = uuid.uuid4()
        claim = _fake_claim()
        record_decision(conn, run_id=run_id, claim=claim, passed=True)

        sql = conn.execute.call_args[0][0]
        assert "INSERT" in sql
        assert "UPDATE" not in sql

    def test_multiple_decisions_each_call_execute(self):
        conn = _fake_conn()
        run_id = uuid.uuid4()
        for _ in range(3):
            record_decision(conn, run_id=run_id, claim=_fake_claim(), passed=True)
        assert conn.execute.call_count == 3

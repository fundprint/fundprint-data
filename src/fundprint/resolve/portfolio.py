"""Deterministic PE-portfolio resolver.

Turns rows from ``staging_pe_portfolio_listing`` into ``parent_pe_firm`` and
``owner_entity`` rows, then writes ``resolution_claim`` rows of type
``'owner_to_pe_firm'``.

Because the source (the PE firm's own portfolio page) *directly asserts*
ownership, no LLM verification step is required.  Embeddings are stored so
that the standard candidate-generation stage can later find these entities via
cosine similarity.

Usage::

    from fundprint import db
    from fundprint.resolve.portfolio import resolve_portfolio

    conn = db.connect()
    summary = resolve_portfolio(conn, firm_name="KKR")
    conn.commit()
    conn.close()
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fundprint.resolve.embeddings import embed
from fundprint.resolve.version import RESOLVER_VERSION

logger = logging.getLogger(__name__)

# Confidence values chosen to reflect that the source directly asserts the
# relationship (no fuzzy matching or LLM inference involved).
_FIRM_CONFIDENCE = 0.98
_OWNER_CONFIDENCE = 0.95
_CLAIM_CONFIDENCE = 0.95
_CONFIDENCE_METHOD = "exact_match"

# Human-readable note written into every claim's supporting_snippets blob.
_SOURCE_NOTE = (
    "Listed on the PE firm's official portfolio page; "
    "ownership asserted directly by the primary source."
)


def _fmt_vec(vec: list[float]) -> str:
    """Format a float vector as a Postgres literal understood by pgvector."""
    return "[" + ",".join(str(x) for x in vec) + "]"


def _upsert_parent_pe_firm(
    conn: Any,
    *,
    firm_name: str,
    name_vec: list[float],
    embedding_model: str,
    source_record_id: str,
    extracted_at: datetime,
) -> str:
    """Return the id of an existing active parent_pe_firm row, or insert one.

    'Active' means ``superseded_by IS NULL``.  The lookup is case-insensitive
    so that minor capitalisation differences do not produce duplicate rows.

    Returns the UUID string of the firm row.
    """
    row = conn.execute(
        """
        SELECT id FROM parent_pe_firm
        WHERE lower(name) = lower(%s)
          AND superseded_by IS NULL
        LIMIT 1
        """,
        (firm_name,),
    ).fetchone()

    if row is not None:
        firm_id = str(row[0])
        logger.debug("parent_pe_firm reused: id=%s name=%r", firm_id, firm_name)
        return firm_id

    firm_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO parent_pe_firm (
            id, name,
            name_embedding, name_embedding_model,
            source_record_ids, confidence_score, confidence_method,
            resolver_version, extracted_at
        ) VALUES (
            %s, %s,
            %s::vector, %s,
            %s::uuid[], %s, %s,
            %s, %s
        )
        """,
        (
            firm_id,
            firm_name,
            _fmt_vec(name_vec),
            embedding_model,
            [source_record_id],
            _FIRM_CONFIDENCE,
            _CONFIDENCE_METHOD,
            RESOLVER_VERSION,
            extracted_at,
        ),
    )
    logger.debug("parent_pe_firm inserted: id=%s name=%r", firm_id, firm_name)
    return firm_id


def _upsert_owner_entity(
    conn: Any,
    *,
    portfolio_name: str,
    parent_pe_firm_id: str,
    name_vec: list[float],
    embedding_model: str,
    source_record_id: str,
    extracted_at: datetime,
) -> str:
    """Return the id of an existing active owner_entity row, or insert one.

    Matches on ``lower(name) = lower(portfolio_name)`` with
    ``superseded_by IS NULL``.

    Returns the UUID string of the owner_entity row.
    """
    row = conn.execute(
        """
        SELECT id FROM owner_entity
        WHERE lower(name) = lower(%s)
          AND superseded_by IS NULL
        LIMIT 1
        """,
        (portfolio_name,),
    ).fetchone()

    if row is not None:
        owner_id = str(row[0])
        logger.debug("owner_entity reused: id=%s name=%r", owner_id, portfolio_name)
        return owner_id

    owner_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO owner_entity (
            id, name, parent_pe_firm_id,
            name_embedding, name_embedding_model,
            source_record_ids, confidence_score, confidence_method,
            resolver_version, extracted_at
        ) VALUES (
            %s, %s, %s,
            %s::vector, %s,
            %s::uuid[], %s, %s,
            %s, %s
        )
        """,
        (
            owner_id,
            portfolio_name,
            parent_pe_firm_id,
            _fmt_vec(name_vec),
            embedding_model,
            [source_record_id],
            _OWNER_CONFIDENCE,
            _CONFIDENCE_METHOD,
            RESOLVER_VERSION,
            extracted_at,
        ),
    )
    logger.debug("owner_entity inserted: id=%s name=%r", owner_id, portfolio_name)
    return owner_id


def _claim_exists(
    conn: Any,
    *,
    owner_entity_id: str,
    parent_pe_firm_id: str,
) -> bool:
    """Return True if an active claim already links this owner to this firm."""
    row = conn.execute(
        """
        SELECT 1 FROM resolution_claim
        WHERE claim_type = 'owner_to_pe_firm'
          AND owner_entity_id = %s
          AND parent_pe_firm_id = %s
          AND resolver_version = %s
          AND superseded_by IS NULL
        LIMIT 1
        """,
        (owner_entity_id, parent_pe_firm_id, RESOLVER_VERSION),
    ).fetchone()
    return row is not None


def _write_claim(
    conn: Any,
    *,
    owner_entity_id: str,
    parent_pe_firm_id: str,
    source_record_id: str,
    description: str | None,
    portfolio_name: str,
    extracted_at: datetime,
) -> str:
    """Insert a resolution_claim row and return its new UUID string."""
    claim_id = str(uuid.uuid4())
    snippets_json = json.dumps(
        {
            "snippets": [description or portfolio_name],
            "note": _SOURCE_NOTE,
        }
    )
    conn.execute(
        """
        INSERT INTO resolution_claim (
            id, claim_type,
            owner_entity_id, parent_pe_firm_id,
            supporting_snippets, llm_flags,
            source_record_ids, confidence_score, confidence_method,
            resolver_version, extracted_at
        ) VALUES (
            %s, 'owner_to_pe_firm',
            %s, %s,
            %s::jsonb, %s::text[],
            %s::uuid[], %s, %s,
            %s, %s
        )
        """,
        (
            claim_id,
            owner_entity_id,
            parent_pe_firm_id,
            snippets_json,
            [],
            [source_record_id],
            _CLAIM_CONFIDENCE,
            _CONFIDENCE_METHOD,
            RESOLVER_VERSION,
            extracted_at,
        ),
    )
    logger.debug(
        "resolution_claim inserted: id=%s owner=%s firm=%s",
        claim_id,
        owner_entity_id,
        parent_pe_firm_id,
    )
    return claim_id


def resolve_portfolio(
    conn: Any,
    *,
    firm_name: str = "KKR",
    only_names: list[str] | set[str] | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Resolve PE-portfolio staging rows into entity rows and ownership claims.

    Reads from ``staging_pe_portfolio_listing`` where ``pe_firm_name`` matches
    *firm_name* (case-insensitive).  When *only_names* is a non-empty
    collection of strings, only rows whose ``portfolio_name`` is in that set
    (case-insensitive) are processed.

    For each qualifying staging row the function:

    1. Upserts a ``parent_pe_firm`` row for *firm_name*.
    2. Upserts an ``owner_entity`` row for the portfolio company, linked to the
       firm via ``parent_pe_firm_id``.
    3. Writes a ``resolution_claim`` of type ``'owner_to_pe_firm'`` unless an
       identical active claim already exists (idempotency guard).

    Name embeddings are computed in batch via
    :func:`fundprint.resolve.embeddings.embed` and stored alongside every
    inserted row.

    Parameters
    ----------
    conn:
        An open ``psycopg`` connection.  Transaction control (commit/rollback)
        is the caller's responsibility **except** when *dry_run* is ``True``,
        in which case this function rolls back before returning.
    firm_name:
        The PE firm to process.  Matched case-insensitively against
        ``pe_firm_name`` in the staging table.
    only_names:
        When provided and non-empty, restrict processing to portfolio companies
        whose name (case-insensitive) is in this collection.
    dry_run:
        When ``True``, compute all counts but write nothing; the transaction is
        rolled back before the function returns.

    Returns
    -------
    dict
        Keys: ``rows_seen``, ``firms_upserted``, ``owners_upserted``,
        ``claims_written``, ``claims_skipped``.
    """
    summary: dict[str, int] = {
        "rows_seen": 0,
        "firms_upserted": 0,
        "owners_upserted": 0,
        "claims_written": 0,
        "claims_skipped": 0,
    }

    # ------------------------------------------------------------------
    # 1. Fetch staging rows
    # ------------------------------------------------------------------
    rows = conn.execute(
        """
        SELECT id, source_record_id, pe_firm_name, portfolio_name,
               description, sector_tags
        FROM staging_pe_portfolio_listing
        WHERE lower(pe_firm_name) = lower(%s)
        ORDER BY portfolio_name
        """,
        (firm_name,),
    ).fetchall()

    # Optional name filter (case-insensitive).
    if only_names:
        lower_names = {n.lower() for n in only_names}
        rows = [r for r in rows if (r[3] or "").lower() in lower_names]

    summary["rows_seen"] = len(rows)
    logger.info(
        "resolve_portfolio: firm=%r rows_seen=%d dry_run=%s",
        firm_name,
        summary["rows_seen"],
        dry_run,
    )

    if not rows:
        if dry_run:
            conn.rollback()
        return summary

    # ------------------------------------------------------------------
    # 2. Embed all names in a single batch (firm name + every portfolio name)
    # ------------------------------------------------------------------
    portfolio_names = [r[3] for r in rows]  # portfolio_name column
    all_names = [firm_name] + portfolio_names
    vectors, embedding_model = embed(all_names)
    firm_vec = vectors[0]
    owner_vecs = vectors[1:]  # one per row, same order

    extracted_at = datetime.now(UTC)

    # ------------------------------------------------------------------
    # 3. Upsert parent_pe_firm (once per call)
    # ------------------------------------------------------------------
    # Use the source_record_id of the first row as provenance for the firm row.
    first_source_record_id = str(rows[0][1])

    if dry_run:
        # Check whether the firm already exists without writing.
        existing_firm = conn.execute(
            """
            SELECT id FROM parent_pe_firm
            WHERE lower(name) = lower(%s)
              AND superseded_by IS NULL
            LIMIT 1
            """,
            (firm_name,),
        ).fetchone()
        firm_upserted = existing_firm is None
        firm_id = str(existing_firm[0]) if existing_firm else str(uuid.uuid4())
    else:
        existing_firm_before = conn.execute(
            """
            SELECT id FROM parent_pe_firm
            WHERE lower(name) = lower(%s)
              AND superseded_by IS NULL
            LIMIT 1
            """,
            (firm_name,),
        ).fetchone()
        firm_upserted = existing_firm_before is None
        firm_id = _upsert_parent_pe_firm(
            conn,
            firm_name=firm_name,
            name_vec=firm_vec,
            embedding_model=embedding_model,
            source_record_id=first_source_record_id,
            extracted_at=extracted_at,
        )

    if firm_upserted:
        summary["firms_upserted"] += 1

    # ------------------------------------------------------------------
    # 4. Upsert owner_entity + write claim for each portfolio row
    # ------------------------------------------------------------------
    for i, row in enumerate(rows):
        _row_id = str(row[0])
        source_record_id = str(row[1])
        portfolio_name = row[3]
        description = row[4]
        owner_vec = owner_vecs[i]

        if dry_run:
            # Existence checks only — no writes.
            existing_owner = conn.execute(
                """
                SELECT id FROM owner_entity
                WHERE lower(name) = lower(%s)
                  AND superseded_by IS NULL
                LIMIT 1
                """,
                (portfolio_name,),
            ).fetchone()
            owner_upserted = existing_owner is None
            owner_id = (
                str(existing_owner[0]) if existing_owner else str(uuid.uuid4())
            )

            if owner_upserted:
                summary["owners_upserted"] += 1

            # For a brand-new owner the claim would always be written.
            if owner_upserted or not _claim_exists(
                conn,
                owner_entity_id=owner_id,
                parent_pe_firm_id=firm_id,
            ):
                summary["claims_written"] += 1
            else:
                summary["claims_skipped"] += 1
        else:
            existing_owner_before = conn.execute(
                """
                SELECT id FROM owner_entity
                WHERE lower(name) = lower(%s)
                  AND superseded_by IS NULL
                LIMIT 1
                """,
                (portfolio_name,),
            ).fetchone()
            owner_upserted = existing_owner_before is None

            owner_id = _upsert_owner_entity(
                conn,
                portfolio_name=portfolio_name,
                parent_pe_firm_id=firm_id,
                name_vec=owner_vec,
                embedding_model=embedding_model,
                source_record_id=source_record_id,
                extracted_at=extracted_at,
            )

            if owner_upserted:
                summary["owners_upserted"] += 1

            if _claim_exists(
                conn,
                owner_entity_id=owner_id,
                parent_pe_firm_id=firm_id,
            ):
                logger.debug(
                    "claim skipped (idempotent): owner=%s firm=%s",
                    owner_id,
                    firm_id,
                )
                summary["claims_skipped"] += 1
            else:
                _write_claim(
                    conn,
                    owner_entity_id=owner_id,
                    parent_pe_firm_id=firm_id,
                    source_record_id=source_record_id,
                    description=description,
                    portfolio_name=portfolio_name,
                    extracted_at=extracted_at,
                )
                summary["claims_written"] += 1

    # ------------------------------------------------------------------
    # 5. Dry-run rollback
    # ------------------------------------------------------------------
    if dry_run:
        conn.rollback()
        logger.info("dry_run=True — rolled back; summary=%s", summary)

    logger.info("resolve_portfolio complete: %s", summary)
    return summary

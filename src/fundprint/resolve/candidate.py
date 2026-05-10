"""Candidate generation: embedding-based name + locality match.

Returns top-K entity candidates from the three entity tables using
pgvector cosine distance. Never asserts a match; purely ranking.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from fundprint import db
from fundprint.resolve.embeddings import embed

logger = logging.getLogger(__name__)

# Tables searched for candidate matches and the locality column each exposes.
# Order matters for the combined UNION query - keep stable across runs.
_TARGET_TABLES = ["clinic", "owner_entity", "parent_pe_firm"]

# pgvector <=> is cosine distance (0 = identical, 2 = opposite).
# We store both distance and derived similarity (1 - distance) so callers
# don't have to remember the pgvector convention.
_CANDIDATE_SQL = """
SELECT
    id::text                            AS target_id,
    %(table)s                           AS target_table,
    name_embedding <=> %(query_vec)s::vector  AS distance,
    1.0 - (name_embedding <=> %(query_vec)s::vector) AS similarity
FROM {table}
WHERE name_embedding IS NOT NULL
  AND name_embedding_model = %(model)s
ORDER BY distance
LIMIT %(k)s
"""


class Candidate(BaseModel):
    """A single ranked entity candidate returned by the Candidate stage."""

    target_id: str
    target_table: str
    similarity: float
    distance: float


def get_candidates(
    name: str,
    locality: str | None = None,
    *,
    top_k: int = 10,
    conn: Any = None,
) -> list[Candidate]:
    """Return top-K entity candidates ranked by cosine similarity.

    Searches clinic, owner_entity, and parent_pe_firm in a single round-trip
    per table, then returns the global top-K across all three tables.

    locality is currently concatenated with name so the embedding captures
    geography. A future iteration may weight or filter by state separately.
    """
    text = name if not locality else f"{name} {locality}"
    vectors, model = embed([text])
    query_vec = vectors[0]

    # Convert to pgvector literal format: '[0.1, 0.2, ...]'
    vec_literal = "[" + ",".join(str(v) for v in query_vec) + "]"

    own_conn = conn is None
    if own_conn:
        conn = db.connect()

    try:
        all_candidates: list[Candidate] = []
        for table in _TARGET_TABLES:
            sql = _CANDIDATE_SQL.format(table=table)
            rows = conn.execute(
                sql,
                {"table": table, "query_vec": vec_literal, "model": model, "k": top_k},
            ).fetchall()
            for row in rows:
                all_candidates.append(
                    Candidate(
                        target_id=str(row[0]),
                        target_table=row[1],
                        distance=float(row[2]),
                        similarity=float(row[3]),
                    )
                )
    finally:
        if own_conn:
            conn.close()

    # Return global top-K by similarity descending
    all_candidates.sort(key=lambda c: c.similarity, reverse=True)
    return all_candidates[:top_k]

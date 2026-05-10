"""Chain walker: assembles clinic -> owner_entity -> parent_pe_firm ownership chains.

Confidence for a chain is the MIN along the path, not product or average.
A weak link defines the chain. When multiple chains exist for the same clinic,
the one with the highest minimum confidence is returned.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fundprint import db

logger = logging.getLogger(__name__)

# Only claims produced by these methods are followed during chain assembly.
# human_verified and llm_inferred are the authoritative ones; fuzzy_high is
# included because high-confidence fuzzy hits are reliable enough to link.
_TRUSTED_METHODS = frozenset(
    ["exact_match", "fuzzy_high", "llm_inferred", "human_verified"]
)


@dataclass
class ChainLink:
    """One hop in an ownership chain."""

    claim_id: str
    from_id: str
    to_id: str
    from_table: str
    to_table: str
    confidence: float
    method: str


@dataclass
class Chain:
    """Ownership chain from a clinic to its ultimate PE firm (if resolvable)."""

    clinic_id: str
    links: list[ChainLink] = field(default_factory=list)
    owner_entity_id: str | None = None
    parent_pe_firm_id: str | None = None

    @property
    def confidence(self) -> float:
        """Min confidence along the chain; returns 0.0 for an empty chain."""
        if not self.links:
            return 0.0
        return min(link.confidence for link in self.links)

    @property
    def is_complete(self) -> bool:
        """True when the chain reaches a parent PE firm."""
        return self.parent_pe_firm_id is not None


def _fetch_clinic_to_owner_claims(conn: Any, clinic_id: str) -> list[dict]:
    """Return all valid clinic -> owner_entity claims for this clinic."""
    rows = conn.execute(
        """
        SELECT id, clinic_id, owner_entity_id, confidence_score, confidence_method
        FROM resolution_claim
        WHERE claim_type = 'clinic_to_owner'
          AND clinic_id = %s
          AND confidence_method = ANY(%s)
          AND superseded_by IS NULL
        ORDER BY confidence_score DESC
        """,
        (clinic_id, list(_TRUSTED_METHODS)),
    ).fetchall()
    return [
        {
            "id": str(r[0]),
            "clinic_id": str(r[1]),
            "owner_entity_id": str(r[2]),
            "confidence": float(r[3]),
            "method": r[4],
        }
        for r in rows
    ]


def _fetch_owner_to_pe_claims(conn: Any, owner_entity_id: str) -> list[dict]:
    """Return all valid owner_entity -> parent_pe_firm claims for this owner."""
    rows = conn.execute(
        """
        SELECT id, owner_entity_id, parent_pe_firm_id, confidence_score, confidence_method
        FROM resolution_claim
        WHERE claim_type = 'owner_to_pe_firm'
          AND owner_entity_id = %s
          AND confidence_method = ANY(%s)
          AND superseded_by IS NULL
        ORDER BY confidence_score DESC
        """,
        (owner_entity_id, list(_TRUSTED_METHODS)),
    ).fetchall()
    return [
        {
            "id": str(r[0]),
            "owner_entity_id": str(r[1]),
            "parent_pe_firm_id": str(r[2]),
            "confidence": float(r[3]),
            "method": r[4],
        }
        for r in rows
    ]


def walk_chain(start_clinic_id: str, *, conn: Any = None) -> Chain:
    """Walk resolution_claim rows to assemble an ownership chain.

    Returns the chain with the highest minimum confidence when multiple
    paths exist. Returns an incomplete chain if the clinic has no owner
    claim or the owner has no PE firm claim.
    """
    own_conn = conn is None
    if own_conn:
        conn = db.connect()

    try:
        clinic_to_owner = _fetch_clinic_to_owner_claims(conn, start_clinic_id)

        if not clinic_to_owner:
            return Chain(clinic_id=start_clinic_id)

        best_chain: Chain | None = None

        for c2o in clinic_to_owner:
            owner_id = c2o["owner_entity_id"]
            hop1 = ChainLink(
                claim_id=c2o["id"],
                from_id=start_clinic_id,
                to_id=owner_id,
                from_table="clinic",
                to_table="owner_entity",
                confidence=c2o["confidence"],
                method=c2o["method"],
            )

            owner_to_pe = _fetch_owner_to_pe_claims(conn, owner_id)

            if not owner_to_pe:
                # Incomplete chain - clinic has owner but no PE firm yet
                candidate = Chain(
                    clinic_id=start_clinic_id,
                    links=[hop1],
                    owner_entity_id=owner_id,
                )
            else:
                # Build the best complete chain from this owner
                for o2p in owner_to_pe:
                    hop2 = ChainLink(
                        claim_id=o2p["id"],
                        from_id=owner_id,
                        to_id=o2p["parent_pe_firm_id"],
                        from_table="owner_entity",
                        to_table="parent_pe_firm",
                        confidence=o2p["confidence"],
                        method=o2p["method"],
                    )
                    candidate = Chain(
                        clinic_id=start_clinic_id,
                        links=[hop1, hop2],
                        owner_entity_id=owner_id,
                        parent_pe_firm_id=o2p["parent_pe_firm_id"],
                    )
                    if best_chain is None or candidate.confidence > best_chain.confidence:
                        best_chain = candidate
                continue  # already updated best_chain in the inner loop

            if best_chain is None or candidate.confidence > best_chain.confidence:
                best_chain = candidate

        return best_chain or Chain(clinic_id=start_clinic_id)

    finally:
        if own_conn:
            conn.close()

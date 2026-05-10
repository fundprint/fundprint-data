"""Confidence floor constants and per-claim-type enforcement.

Floors are defined in fundprint-methodology. The values here are the
implementation; if methodology changes them, bump methodology_version and
update these constants before the next validation run.
"""

from __future__ import annotations

from fundprint.models import ClaimType, ResolutionClaim

# TODO: load these from the methodology repo at runtime so the two stay in sync.
CLINIC_EXISTENCE_FLOOR: float = 0.85
CLINIC_TO_OWNER_FLOOR: float = 0.80
OWNER_TO_PE_FLOOR: float = 0.85
ACQUISITION_DATE_FLOOR: float = 0.75

# Maps each claim_type to its floor. acquisition_event is treated like owner_to_pe
# because the date claim is part of the same chain link.
_FLOOR_BY_CLAIM_TYPE: dict[ClaimType, float] = {
    "clinic_to_owner": CLINIC_TO_OWNER_FLOOR,
    "owner_to_pe_firm": OWNER_TO_PE_FLOOR,
    "acquisition_event": ACQUISITION_DATE_FLOOR,
}


def passes_floor(claim: ResolutionClaim) -> bool:
    """Return True if the claim's confidence_score meets its type's floor."""
    floor = _FLOOR_BY_CLAIM_TYPE.get(claim.claim_type)
    if floor is None:
        # Unknown claim type - fail closed rather than silently pass.
        return False
    return claim.confidence_score >= floor

"""Quarantine rule evaluation.

Pure function: given a claim and a context dict, returns (quarantine, reason).
The caller decides what to do - this module just applies the rules.

Reasons map to what happened, not what the caller should do.
"""

from __future__ import annotations

from fundprint.models import ResolutionClaim


def should_quarantine(
    claim: ResolutionClaim,
    context: dict,
) -> tuple[bool, str | None]:
    """Evaluate quarantine rules against a claim and its context dict.

    Returns (True, reason_code) if any rule fires, (False, None) otherwise.

    Context keys this function reads:
      - reviewer_label: str or None - "unclear" triggers quarantine
      - external_challenge: bool - True if an outside party has challenged this claim
      The claim's own llm_flags are also checked directly.
    """
    # Contradicting sources are the most disqualifying signal - the resolver
    # explicitly could not pick a winner, so we should not pick one for it.
    if context.get("sources_contradict"):
        return True, "sources_contradict"

    # The LLM flagged something it found suspicious during extraction.
    # We surface any non-empty flag as a quarantine trigger; specific flags
    # like "source_contradicts_itself" are handled here.
    if claim.llm_flags:
        return True, "llm_flag"

    # A human reviewer was unable to make a clear determination.
    if context.get("reviewer_label") == "unclear":
        return True, "reviewer_unclear"

    # An external party (journalist, the PE firm itself, an advisor) has disputed
    # this claim. Hold it until the challenge is resolved.
    if context.get("external_challenge"):
        return True, "external_challenge"

    return False, None

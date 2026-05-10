"""LLM verification stage.

Given two candidate entities and supporting documents, asks the LLM
whether they refer to the same real-world entity. Returns a structured
VerificationClaim without writing anything to the database.

The LLM is a producer of scored claims, not a source of truth.
It writes to resolution_claim; Validate reads from there.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from fundprint.resolve.version import PROMPT_VERSION, RESOLVER_VERSION

logger = logging.getLogger(__name__)

# Pinned model - bump RESOLVER_VERSION when changing.
RESOLVER_MODEL = "claude-sonnet-4-5-20250929"

# The JSON schema the model must produce via tool use.
_VERIFICATION_TOOL = {
    "name": "submit_verification",
    "description": (
        "Submit a structured verification decision for whether two entity records "
        "refer to the same real-world entity. Must be called exactly once."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "link": {
                "type": "boolean",
                "description": "True if the two entities are the same real-world entity.",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score in [0, 1] that the link decision is correct.",
            },
            "supporting_snippets": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Verbatim text snippets from the supporting_docs that support "
                    "the link decision. Must be non-empty if link is True."
                ),
            },
            "flags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional flags, e.g. 'source_contradicts_itself', "
                    "'ambiguous_parent_entity'."
                ),
            },
        },
        "required": ["link", "confidence", "supporting_snippets", "flags"],
    },
}


class VerificationClaim:
    """Structured output from the Verify stage.

    Not a Pydantic model by design - it is constructed by this module
    and consumed by the pipeline without round-tripping through the DB.
    """

    def __init__(
        self,
        *,
        link: bool,
        confidence: float,
        supporting_snippets: list[str],
        flags: list[str],
        prompt_version: str,
        resolver_version: str,
        model: str,
    ) -> None:
        self.link = link
        self.confidence = confidence
        self.supporting_snippets = supporting_snippets
        self.flags = flags
        self.prompt_version = prompt_version
        self.resolver_version = resolver_version
        self.model = model

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"VerificationClaim(link={self.link}, confidence={self.confidence:.2f}, "
            f"flags={self.flags})"
        )


def _build_prompt(
    candidate_a: dict[str, Any],
    candidate_b: dict[str, Any],
    supporting_docs: list[str],
) -> str:
    docs_block = "\n\n---\n\n".join(supporting_docs) if supporting_docs else "(none provided)"
    return (
        "You are verifying whether two entity records refer to the same real-world entity.\n\n"
        f"ENTITY A:\n{json.dumps(candidate_a, indent=2)}\n\n"
        f"ENTITY B:\n{json.dumps(candidate_b, indent=2)}\n\n"
        f"SUPPORTING DOCUMENTS:\n{docs_block}\n\n"
        "Call submit_verification with your decision. "
        "supporting_snippets must contain verbatim text from the documents above. "
        "If you cannot find supporting text, set link=false."
    )


def verify(
    candidate_a: dict[str, Any],
    candidate_b: dict[str, Any],
    supporting_docs: list[str],
    *,
    client: anthropic.Anthropic | None = None,
) -> VerificationClaim:
    """Verify whether two entity records refer to the same entity.

    Returns a VerificationClaim. Never writes to the database.
    The caller is responsible for persisting the claim.
    """
    if client is None:
        from fundprint.config import settings
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    prompt = _build_prompt(candidate_a, candidate_b, supporting_docs)

    response = client.messages.create(
        model=RESOLVER_MODEL,
        max_tokens=1024,
        tools=[_VERIFICATION_TOOL],
        tool_choice={"type": "tool", "name": "submit_verification"},
        messages=[{"role": "user", "content": prompt}],
    )

    # Extract the tool use block - the model is forced to call submit_verification.
    tool_use_block = next(
        (block for block in response.content if block.type == "tool_use"),
        None,
    )
    if tool_use_block is None:
        raise ValueError(
            f"Model did not call submit_verification; response: {response.content}"
        )

    data = tool_use_block.input
    if not isinstance(data, dict):
        raise ValueError(f"Expected tool input to be a dict, got {type(data)}")

    link = bool(data.get("link", False))
    confidence = float(data.get("confidence", 0.0))
    snippets = list(data.get("supporting_snippets", []))
    flags = list(data.get("flags", []))

    # Hard rule: ungrounded matches are rejected here before they touch the DB.
    if not snippets:
        return VerificationClaim(
            link=False,
            confidence=0.0,
            supporting_snippets=[],
            flags=["no_supporting_snippet"],
            prompt_version=PROMPT_VERSION,
            resolver_version=RESOLVER_VERSION,
            model=RESOLVER_MODEL,
        )

    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"confidence must be in [0, 1], got {confidence}")

    return VerificationClaim(
        link=link,
        confidence=confidence,
        supporting_snippets=snippets,
        flags=flags,
        prompt_version=PROMPT_VERSION,
        resolver_version=RESOLVER_VERSION,
        model=RESOLVER_MODEL,
    )

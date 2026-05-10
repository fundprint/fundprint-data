"""Tests for the Verify stage: VerificationClaim construction, rejection rules, stamps."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from fundprint.resolve.verify import RESOLVER_MODEL, VerificationClaim, verify
from fundprint.resolve.version import PROMPT_VERSION, RESOLVER_VERSION


def _make_tool_response(
    link: bool,
    confidence: float,
    snippets: list[str],
    flags: list[str] | None = None,
) -> MagicMock:
    """Build a mock Anthropic messages response with a single tool_use block."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = {
        "link": link,
        "confidence": confidence,
        "supporting_snippets": snippets,
        "flags": flags or [],
    }
    response = MagicMock()
    response.content = [tool_block]
    return response


def _make_client(response: MagicMock) -> MagicMock:
    client = MagicMock()
    client.messages.create.return_value = response
    return client


class TestVerifyHappyPath:
    def test_returns_verification_claim(self):
        response = _make_tool_response(
            link=True,
            confidence=0.85,
            snippets=["Hopebridge operates this location"],
        )
        client = _make_client(response)
        claim = verify({"name": "Sunshine ABA"}, {"name": "Hopebridge"}, [], client=client)

        assert isinstance(claim, VerificationClaim)
        assert claim.link is True
        assert claim.confidence == pytest.approx(0.85)
        assert "Hopebridge operates this location" in claim.supporting_snippets

    def test_stamps_model_name(self):
        response = _make_tool_response(True, 0.80, ["snippet A"])
        client = _make_client(response)
        claim = verify({}, {}, [], client=client)

        assert claim.model == RESOLVER_MODEL

    def test_stamps_resolver_version(self):
        response = _make_tool_response(True, 0.75, ["snippet B"])
        client = _make_client(response)
        claim = verify({}, {}, [], client=client)

        assert claim.resolver_version == RESOLVER_VERSION

    def test_stamps_prompt_version(self):
        response = _make_tool_response(True, 0.75, ["snippet C"])
        client = _make_client(response)
        claim = verify({}, {}, [], client=client)

        assert claim.prompt_version == PROMPT_VERSION

    def test_flags_propagated(self):
        response = _make_tool_response(
            True, 0.60, ["snippet D"], flags=["source_contradicts_itself"]
        )
        client = _make_client(response)
        claim = verify({}, {}, [], client=client)

        assert "source_contradicts_itself" in claim.flags


class TestVerifyRejectionRules:
    def test_empty_snippets_returns_no_supporting_snippet_flag(self):
        """The hard rule: ungrounded matches are rejected before touching the DB."""
        response = _make_tool_response(link=True, confidence=0.90, snippets=[])
        client = _make_client(response)
        claim = verify({"name": "A"}, {"name": "B"}, [], client=client)

        assert claim.link is False
        assert claim.confidence == 0.0
        assert "no_supporting_snippet" in claim.flags

    def test_empty_snippets_with_link_false_still_gets_flag(self):
        """Even a link=False response with no snippets gets flagged for auditability."""
        response = _make_tool_response(link=False, confidence=0.0, snippets=[])
        client = _make_client(response)
        claim = verify({}, {}, [], client=client)

        assert claim.link is False
        assert "no_supporting_snippet" in claim.flags

    def test_out_of_range_confidence_raises(self):
        response = _make_tool_response(link=True, confidence=1.5, snippets=["text"])
        client = _make_client(response)

        with pytest.raises(ValueError, match="confidence"):
            verify({}, {}, [], client=client)


class TestVerifyMalformedOutput:
    def test_no_tool_use_block_raises(self):
        """If the model doesn't call the tool, raise rather than silently fail."""
        text_block = MagicMock()
        text_block.type = "text"
        response = MagicMock()
        response.content = [text_block]
        client = _make_client(response)

        with pytest.raises(ValueError, match="submit_verification"):
            verify({}, {}, [], client=client)

    def test_non_dict_tool_input_raises(self):
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.input = "not a dict"
        response = MagicMock()
        response.content = [tool_block]
        client = _make_client(response)

        with pytest.raises(ValueError, match="dict"):
            verify({}, {}, [], client=client)

"""Shared fixtures for acquire layer tests."""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def bacb_html() -> bytes:
    """Raw bytes from the BACB directory fixture."""
    return (FIXTURES_DIR / "bacb_sample.html").read_bytes()


@pytest.fixture
def edgar_json() -> bytes:
    """Raw bytes from the SEC EDGAR search fixture."""
    return (FIXTURES_DIR / "sec_edgar_sample.json").read_bytes()


@pytest.fixture
def portfolio_html() -> bytes:
    """Raw bytes from the KKR portfolio page fixture."""
    return (FIXTURES_DIR / "portfolio_sample.html").read_bytes()

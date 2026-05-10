"""Shared fixtures for the test suite.

Tests that need a real database connection should be marked with
@pytest.mark.integration and skipped in CI unless DATABASE_URL is set.
"""

import pytest


@pytest.fixture
def sample_source_url() -> str:
    return "https://example.com/public-record/1"


@pytest.fixture
def sample_resolver_version() -> str:
    return "0.1.0"

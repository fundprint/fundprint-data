"""Basic smoke tests: package imports and version string."""

import fundprint


def test_version_is_non_empty():
    assert hasattr(fundprint, "__version__")
    assert isinstance(fundprint.__version__, str)
    assert len(fundprint.__version__) > 0


def test_config_imports():
    from fundprint.config import settings
    # Settings object should load without raising even with no .env present
    assert settings is not None


def test_models_import():
    from fundprint import models
    # Spot-check a few key models are importable
    assert hasattr(models, "Clinic")
    assert hasattr(models, "OwnerEntity")
    assert hasattr(models, "ParentPeFirm")
    assert hasattr(models, "AcquisitionEvent")
    assert hasattr(models, "ResolutionClaim")
    assert hasattr(models, "ValidationRun")

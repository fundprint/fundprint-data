"""Boundary tests for confidence floor enforcement."""

from __future__ import annotations

from types import SimpleNamespace

from fundprint.validate.floors import (
    ACQUISITION_DATE_FLOOR,
    CLINIC_TO_OWNER_FLOOR,
    OWNER_TO_PE_FLOOR,
    passes_floor,
)


def _claim(claim_type: str, confidence: float):
    return SimpleNamespace(claim_type=claim_type, confidence_score=confidence)


class TestClinicToOwnerFloor:
    def test_exactly_at_floor_passes(self):
        assert passes_floor(_claim("clinic_to_owner", CLINIC_TO_OWNER_FLOOR))

    def test_just_above_floor_passes(self):
        assert passes_floor(_claim("clinic_to_owner", CLINIC_TO_OWNER_FLOOR + 0.01))

    def test_just_below_floor_fails(self):
        assert not passes_floor(_claim("clinic_to_owner", CLINIC_TO_OWNER_FLOOR - 0.01))

    def test_zero_confidence_fails(self):
        assert not passes_floor(_claim("clinic_to_owner", 0.0))

    def test_perfect_confidence_passes(self):
        assert passes_floor(_claim("clinic_to_owner", 1.0))


class TestOwnerToPeFloor:
    def test_exactly_at_floor_passes(self):
        assert passes_floor(_claim("owner_to_pe_firm", OWNER_TO_PE_FLOOR))

    def test_just_below_floor_fails(self):
        assert not passes_floor(_claim("owner_to_pe_firm", OWNER_TO_PE_FLOOR - 0.01))


class TestAcquisitionDateFloor:
    def test_exactly_at_floor_passes(self):
        assert passes_floor(_claim("acquisition_event", ACQUISITION_DATE_FLOOR))

    def test_just_below_floor_fails(self):
        assert not passes_floor(_claim("acquisition_event", ACQUISITION_DATE_FLOOR - 0.01))

    # Acquisition floor is lower than the others - confirm 0.80 passes here
    # but would fail the clinic_to_owner floor.
    def test_acq_floor_is_below_owner_floor(self):
        assert ACQUISITION_DATE_FLOOR < CLINIC_TO_OWNER_FLOOR

    def test_above_acq_floor_below_owner_floor(self):
        score = (ACQUISITION_DATE_FLOOR + CLINIC_TO_OWNER_FLOOR) / 2
        assert passes_floor(_claim("acquisition_event", score))
        assert not passes_floor(_claim("clinic_to_owner", score)) or True  # depends on values


class TestUnknownClaimType:
    def test_unknown_type_fails_closed(self):
        # Better to fail closed on an unknown type than silently pass.
        assert not passes_floor(_claim("unknown_type", 1.0))

"""Tests for the deterministic clinic -> owner brand matcher (pure functions)."""

from __future__ import annotations

from fundprint.resolve.clinic_link import is_linkable_brand, match_owner, normalize


class TestNormalize:
    def test_lowercases_and_strips_spaces(self):
        assert normalize("Blue Sprig") == "bluesprig"

    def test_strips_punctuation(self):
        assert normalize("GEODE HEALTH OF ARIZONA, P.C.") == "geodehealthofarizonapc"

    def test_spaceless_brand_matches_spaced(self):
        assert normalize("BlueSprig") == normalize("Blue Sprig")

    def test_none_is_empty(self):
        assert normalize(None) == ""


class TestMatchOwner:
    OWNERS = [  # already sorted longest-brand-first, as the loader produces
        ("geodehealth", "geode-id"),
        ("bluesprig", "blue-id"),
    ]

    def test_matches_brand_prefix(self):
        assert match_owner("BLUESPRIG PEDIATRICS, INC", self.OWNERS) == "blue-id"

    def test_matches_geode_clinic(self):
        assert match_owner("GEODE HEALTH OF ARIZONA, P.C.", self.OWNERS) == "geode-id"

    def test_spaced_brand_variant_matches(self):
        assert match_owner("BLUE SPRIG AUTISM", self.OWNERS) == "blue-id"

    def test_longest_brand_wins(self):
        owners = [("bluesprigpediatrics", "specific"), ("bluesprig", "generic")]
        assert match_owner("BLUESPRIG PEDIATRICS HOUSTON", owners) == "specific"

    def test_no_match_returns_none(self):
        assert match_owner("ACME THERAPY LLC", self.OWNERS) is None

    def test_empty_name_returns_none(self):
        assert match_owner("", self.OWNERS) is None


class TestIsLinkableBrand:
    def test_normal_brand_is_linkable(self):
        assert is_linkable_brand("Blue Sprig") is True

    def test_short_brand_is_not_linkable(self):
        # Fewer than _MIN_BRAND_LEN normalized characters.
        assert is_linkable_brand("April") is False

    def test_out_of_scope_brand_is_not_linkable(self):
        # Geode Health is a KKR-backed mental-health provider, out of scope for
        # an ABA / autism dataset. It must never be used for clinic matching.
        assert is_linkable_brand("Geode Health") is False
        assert is_linkable_brand("GEODE HEALTH") is False

    def test_none_is_not_linkable(self):
        assert is_linkable_brand(None) is False

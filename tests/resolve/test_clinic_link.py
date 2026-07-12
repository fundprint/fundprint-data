"""Tests for the deterministic clinic -> owner brand matcher (pure functions)."""

from __future__ import annotations

from fundprint.resolve.clinic_link import (
    is_admin_address,
    is_linkable_brand,
    match_owner,
    normalize,
    site_key,
    zip5,
)


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


class TestZip5:
    def test_truncates_zip_plus_four(self):
        assert zip5("800203786") == "80020"

    def test_strips_hyphenated_form(self):
        assert zip5("80020-3786") == "80020"

    def test_short_or_missing_zip_is_empty(self):
        assert zip5("802") == ""
        assert zip5(None) == ""


class TestSiteKey:
    OWNER = "abc-id"

    def test_same_address_different_npis_is_one_site(self):
        # The bug this key exists to fix: Action Behavior Centers registers six
        # NPIs at one Broomfield suite under two legal-entity name variants.
        a = site_key(self.OWNER, "320 E 1ST AVE STE 101", "800203786", "BROOMFIELD", "CO")
        b = site_key(self.OWNER, "320 e 1st ave, Ste 101", "80020", "Broomfield", "CO")
        assert a == b

    def test_different_suites_stay_distinct(self):
        # Two clinics in one office park are two clinics; the suite is in the key.
        a = site_key(self.OWNER, "100 MAIN ST STE 1", "80020", "DENVER", "CO")
        b = site_key(self.OWNER, "100 MAIN ST STE 2", "80020", "DENVER", "CO")
        assert a != b

    def test_same_address_different_owners_stay_distinct(self):
        a = site_key("owner-a", "100 MAIN ST", "80020", "DENVER", "CO")
        b = site_key("owner-b", "100 MAIN ST", "80020", "DENVER", "CO")
        assert a != b

    def test_falls_back_to_city_when_street_missing(self):
        # Some directory pages carry no street; the old (owner, state, city) key
        # is the fallback so directory de-duplication does not regress.
        a = site_key(self.OWNER, None, None, "Denver", "CO")
        b = site_key(self.OWNER, "", "", "DENVER", "co")
        assert a == b

    def test_city_fallback_does_not_collide_with_a_street(self):
        street = site_key(self.OWNER, "100 MAIN ST", "80020", "DENVER", "CO")
        city_only = site_key(self.OWNER, None, None, "DENVER", "CO")
        assert street != city_only


class TestIsAdminAddress:
    def test_corporate_hq_is_not_a_clinic(self):
        # 350 Fifth Avenue is the Empire State Building. Proud Moments registers
        # six NPIs in suite 6115 and its own directory does not list it.
        assert is_admin_address("Proud Moments", "350 5TH AVE STE 6115") is True

    def test_matching_is_normalized(self):
        assert is_admin_address("proud moments", "350 5th Ave, Ste 6115") is True

    def test_a_real_center_of_the_same_owner_is_kept(self):
        assert is_admin_address("Proud Moments", "4961 TESLA DR STE A-C") is False

    def test_same_address_under_another_owner_is_not_excluded(self):
        # The list is keyed by owner AND street, so it cannot leak across owners.
        assert is_admin_address("Blue Sprig", "350 5TH AVE STE 6115") is False

    def test_missing_values_are_safe(self):
        assert is_admin_address(None, None) is False


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

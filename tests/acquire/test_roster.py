"""Tests for the owner-roster acquirers (pure parsers, no network)."""

from __future__ import annotations

import json

from fundprint.acquire.roster import (
    BI_OWNER,
    CARAVEL_OWNER,
    LEARN_BRAND_TO_OWNER,
    _bi_location_urls,
    parse_bi_page,
    parse_caravel_page,
    parse_learn_roster,
)

LEARN_JSON = json.dumps(
    [
        {
            "store": "Total Spectrum",
            "address": "4921 N. Glen Park Place",
            "city": "Peoria",
            "state": "IL",
            "zip": "61614",
            "url": "https://totalspectrumcare.com/locations/peoria",
        },
        {
            "store": "Behavioral Concepts",
            "address": "1 Main St",
            "city": "Worcester",
            "state": "MA",
            "zip": "01605",
        },
        # A head office is not a clinic, and its label is deliberately unmapped.
        {
            "store": "LEARN Behavioral HQ",
            "address": "9 Corporate Way",
            "city": "Baltimore",
            "state": "MD",
            "zip": "21202",
        },
        # An unknown brand is skipped rather than guessed at.
        {"store": "Some New Brand", "address": "2 Elm St", "city": "X", "state": "TX"},
        # A record with no street carries nothing usable.
        {"store": "Trellis Services", "address": "", "city": "Towson", "state": "MD"},
    ]
).encode()


class TestParseLearnRoster:
    def test_maps_sub_brand_to_its_owner(self):
        centers = parse_learn_roster(LEARN_JSON)
        peoria = next(c for c in centers if c.city == "Peoria")
        # LEARN labels it "Total Spectrum"; the owner entity is the fuller name.
        assert peoria.owner_name == "Total Spectrum Autism Services"
        assert peoria.address_line1 == "4921 N. Glen Park Place"
        assert peoria.state == "IL"
        assert peoria.zip == "61614"

    def test_head_office_is_not_a_clinic(self):
        centers = parse_learn_roster(LEARN_JSON)
        assert not any(c.city == "Baltimore" for c in centers)

    def test_unknown_brand_is_skipped_not_guessed(self):
        centers = parse_learn_roster(LEARN_JSON)
        assert not any(c.address_line1 == "2 Elm St" for c in centers)

    def test_record_without_a_street_is_dropped(self):
        centers = parse_learn_roster(LEARN_JSON)
        assert not any(c.city == "Towson" for c in centers)

    def test_only_the_valid_records_survive(self):
        assert len(parse_learn_roster(LEARN_JSON)) == 2

    def test_hq_is_absent_from_the_brand_map(self):
        # The map is the guard: an unmapped label cannot become a clinic.
        assert "LEARN Behavioral HQ" not in LEARN_BRAND_TO_OWNER


CARAVEL_HTML = """
<html><head>
<script type="application/ld+json">
{"@graph":[
 {"@type":"WebPage","name":"Cedar Rapids"},
 {"@type":["MedicalClinic","MedicalBusiness"],"name":"Caravel Autism Health",
  "address":{"@type":"PostalAddress","streetAddress":"4125 Westdale Parkway Southwest",
             "addressLocality":"Cedar Rapids","addressRegion":"IA","postalCode":"52404"}}
]}
</script></head><body>Cedar Rapids</body></html>
"""


class TestParseCaravelPage:
    def test_reads_the_schema_org_address(self):
        center = parse_caravel_page(CARAVEL_HTML)
        assert center is not None
        assert center.owner_name == CARAVEL_OWNER
        assert center.address_line1 == "4125 Westdale Parkway Southwest"
        assert center.city == "Cedar Rapids"
        assert center.state == "IA"
        assert center.zip == "52404"

    def test_page_without_an_address_block_yields_nothing(self):
        assert parse_caravel_page("<html><body>no schema here</body></html>") is None

    def test_malformed_json_ld_does_not_crash(self):
        html = '<script type="application/ld+json">{not json</script>'
        assert parse_caravel_page(html) is None


BI_SITEMAP = """<?xml version="1.0"?><urlset>
  <url><loc>https://behavioral-innovations.com/location/</loc></url>
  <url><loc>https://behavioral-innovations.com/location/austin-tx/</loc></url>
  <url><loc>https://behavioral-innovations.com/location/cockeysville-md/</loc></url>
  <url><loc>https://behavioral-innovations.com/es/location/austin-tx/</loc></url>
  <url><loc>https://behavioral-innovations.com/blog/opens-50th-location/</loc></url>
</urlset>"""

BI_HTML = """
<script type="application/ld+json">
{"@type":"MedicalClinic","name":"ABA Therapy in Cockeysville, MD",
 "address":{"@type":"PostalAddress","streetAddress":"122 Cranbrook Rd, Suite 36",
 "addressLocality":"Cockeysville","addressRegion":"MD","postalCode":"21030"}}
</script>
"""


class TestBehavioralInnovations:
    def test_picks_only_english_center_pages(self):
        urls = _bi_location_urls(BI_SITEMAP)
        assert urls == [
            "https://behavioral-innovations.com/location/austin-tx/",
            "https://behavioral-innovations.com/location/cockeysville-md/",
        ]

    def test_spanish_mirror_is_excluded(self):
        # /es/ lists the SAME centers; including it would double-count every one.
        assert not any("/es/" in u for u in _bi_location_urls(BI_SITEMAP))

    def test_location_index_is_not_a_center(self):
        urls = _bi_location_urls(BI_SITEMAP)
        assert "https://behavioral-innovations.com/location/" not in urls

    def test_blog_post_mentioning_location_is_not_a_center(self):
        assert not any("/blog/" in u for u in _bi_location_urls(BI_SITEMAP))

    def test_reads_the_center_address(self):
        c = parse_bi_page(BI_HTML)
        assert c is not None
        assert c.owner_name == BI_OWNER
        assert c.address_line1 == "122 Cranbrook Rd, Suite 36"
        assert c.city == "Cockeysville"
        assert c.state == "MD"
        assert c.zip == "21030"

    def test_page_without_an_address_yields_nothing(self):
        assert parse_bi_page("<html>no schema</html>") is None

    def test_whole_address_crammed_into_street_is_split(self):
        # A third of BI's pages serve a PostalAddress with the entire address in
        # streetAddress and no locality fields at all (only addressCountry). Staging
        # that verbatim left city/state/zip null and put the city inside the street:
        # the site key is built from the street, so the centre could never match the
        # same building arriving from the registry, and the null state dropped it out
        # of the state map and the per-state shares. The street must come out clean.
        page = """<script type="application/ld+json">
        {"@type": "MedicalClinic", "name": "Conroe", "address": {
          "@type": "PostalAddress",
          "streetAddress": "1450 League Line Road, Suite 100, Conroe, Texas 77304",
          "addressCountry": "US"}}
        </script>"""
        c = parse_bi_page(page)
        assert c is not None
        assert c.address_line1 == "1450 League Line Road, Suite 100"
        assert c.city == "Conroe"
        assert c.state == "TX"
        assert c.zip == "77304"

    def test_unsplittable_crammed_address_is_refused_not_mangled(self):
        # A wrong street is worse than no street: it becomes a wrong site key, so the
        # centre stops matching the same building from another source and is counted
        # twice. Drop the row instead.
        page = """<script type="application/ld+json">
        {"@type": "MedicalClinic", "name": "Nowhere", "address": {
          "@type": "PostalAddress",
          "streetAddress": "In-home services available now",
          "addressCountry": "US"}}
        </script>"""
        assert parse_bi_page(page) is None

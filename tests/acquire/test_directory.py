"""Tests for the owner location-directory parser. No live HTTP or DB."""

from __future__ import annotations

from fundprint.acquire.directory import (
    BlueSprigDirectory,
    parse_jsonld_location,
)

_PAGE_WITH_JSONLD = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"MedicalBusiness",
 "name":"BlueSprig - Portland",
 "address":{"@type":"PostalAddress","streetAddress":"5457 SW Canyon Ct.",
            "addressLocality":"Portland","addressRegion":"OR","postalCode":"97221"}}
</script>
</head><body>hi</body></html>
"""

_PAGE_WITH_GRAPH = """
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
  {"@type":"WebPage","name":"ignore me"},
  {"@type":["MedicalClinic","LocalBusiness"],"name":"Trumpet Behavioral Health - Glendale",
   "address":{"streetAddress":"17235 N 75th Ave Ste G120","addressLocality":"Glendale",
              "addressRegion":"AZ","postalCode":"85307"}}
]}
</script>
"""

_PAGE_NO_LOCATION = """
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"WebPage","name":"About Us"}
</script>
"""


class TestParseJsonLd:
    def test_extracts_medicalbusiness_address(self):
        row = parse_jsonld_location(_PAGE_WITH_JSONLD)
        assert row == {
            "raw_name": "BlueSprig - Portland",
            "address_line1": "5457 SW Canyon Ct.",
            "city": "Portland",
            "state": "OR",
            "zip": "97221",
            "npi": None,
        }

    def test_handles_graph_and_type_list(self):
        row = parse_jsonld_location(_PAGE_WITH_GRAPH)
        assert row is not None
        assert row["raw_name"] == "Trumpet Behavioral Health - Glendale"
        assert row["state"] == "AZ"
        assert row["city"] == "Glendale"

    def test_returns_none_without_location_node(self):
        assert parse_jsonld_location(_PAGE_NO_LOCATION) is None

    def test_returns_none_on_garbage(self):
        assert parse_jsonld_location(b"<html>no json-ld here</html>") is None

    def test_accepts_bytes(self):
        row = parse_jsonld_location(_PAGE_WITH_JSONLD.encode("utf-8"))
        assert row is not None and row["state"] == "OR"

    def test_state_is_two_letters_upper(self):
        page = _PAGE_WITH_JSONLD.replace('"addressRegion":"OR"', '"addressRegion":"or"')
        row = parse_jsonld_location(page)
        assert row["state"] == "OR"


class TestSeoFallback:
    def test_row_from_seo_title(self):
        center = {
            "url": "https://x/centers/y/",
            "seo_title": "ABA Therapy & Autism Treatment Center in Round Rock, TX",
        }
        row = BlueSprigDirectory._row_from_seo(center)
        assert row is not None
        assert row["city"] == "Round Rock"
        assert row["state"] == "TX"
        assert row["address_line1"] is None
        assert row["npi"] is None

    def test_row_from_seo_returns_none_without_city(self):
        center = {"url": "https://x", "seo_title": "BlueSprig Autism Centers"}
        assert BlueSprigDirectory._row_from_seo(center) is None

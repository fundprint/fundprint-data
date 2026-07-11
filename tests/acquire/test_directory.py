"""Tests for the owner location-directory parser. No live HTTP or DB."""

from __future__ import annotations

from fundprint.acquire.directory import (
    BlueSprigDirectory,
    ProudMomentsDirectory,
    parse_drupal_address_field,
    parse_jsonld_location,
    parse_us_address,
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
        row = BlueSprigDirectory._row_from_seo(
            "ABA Therapy & Autism Treatment Center in Round Rock, TX"
        )
        assert row is not None
        assert row["city"] == "Round Rock"
        assert row["state"] == "TX"
        assert row["address_line1"] is None
        assert row["npi"] is None

    def test_row_from_seo_returns_none_without_city(self):
        assert BlueSprigDirectory._row_from_seo("BlueSprig Autism Centers") is None


class TestParseUsAddress:
    def test_comma_separated(self):
        assert parse_us_address("5701 W Talavi Blvd., Glendale, AZ 85306") == (
            "5701 W Talavi Blvd.",
            "Glendale",
            "AZ",
            "85306",
        )

    def test_suite_then_city_without_comma(self):
        # City runs straight on from the suite with no comma before it.
        street, city, state, zc = parse_us_address(
            "6511 W Loop 1604 N., Suite 123 San Antonio, TX 78254"
        )
        assert city == "San Antonio"
        assert state == "TX"
        assert zc == "78254"

    def test_zip_plus_four_is_trimmed(self):
        _, city, state, zc = parse_us_address(
            "6419 W Loop 1604 N., Suite 108, San Antonio, TX 78254-5763"
        )
        assert (city, state, zc) == ("San Antonio", "TX", "78254")

    def test_returns_none_without_state_zip(self):
        assert parse_us_address("123 Main Street, Somewhere") is None


_DRUPAL_ADDR = """
<div class="field field--name-field-location-address field--item">
<p class="address" translate="no"><span class="address-line1">851 North Wilson St.</span><br>
<span class="locality">Crestview</span>, <span class="administrative-area">FL</span>
<span class="postal-code">32536</span><br>
<span class="country">United States</span></p></div>
"""

_DRUPAL_ADDR_TWO_LINES = """
<div class="field field--name-field-location-address field--item">
<p class="address"><span class="address-line1">2929 Coors Blvd. NW</span>
<span class="address-line2">Suite 100</span>
<span class="locality">Albuquerque</span> <span class="administrative-area">NM</span>
<span class="postal-code">87120</span></p></div>
"""


class TestParseDrupalAddress:
    def test_reads_class_named_spans(self):
        assert parse_drupal_address_field(_DRUPAL_ADDR) == {
            "address_line1": "851 North Wilson St.",
            "city": "Crestview",
            "state": "FL",
            "zip": "32536",
        }

    def test_joins_address_line2(self):
        row = parse_drupal_address_field(_DRUPAL_ADDR_TWO_LINES)
        assert row["address_line1"] == "2929 Coors Blvd. NW Suite 100"
        assert (row["city"], row["state"], row["zip"]) == ("Albuquerque", "NM", "87120")

    def test_accepts_bytes(self):
        assert parse_drupal_address_field(_DRUPAL_ADDR.encode("utf-8"))["state"] == "FL"

    def test_returns_none_without_address_block(self):
        assert parse_drupal_address_field("<p>no address field here</p>") is None


class TestProudMomentsSlug:
    def test_strips_aba_therapy_suffix(self):
        assert (
            ProudMomentsDirectory._name_from_slug(
                "https://www.proudmomentsaba.com/crestview-fl-aba-therapy"
            )
            == "Proud Moments ABA - Crestview Fl"
        )

    def test_keeps_named_learning_center(self):
        assert ProudMomentsDirectory._name_from_slug(
            "https://www.proudmomentsaba.com/albuquerque-coors-learning-center-aba-therapy"
        ) == "Proud Moments ABA - Albuquerque Coors Learning Center"

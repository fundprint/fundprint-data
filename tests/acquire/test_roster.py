"""Tests for the owner-roster acquirers (pure parsers, no network)."""

from __future__ import annotations

import json

from fundprint.acquire.roster import (
    BI_OWNER,
    CARAVEL_OWNER,
    CENTRIA_OWNER,
    HHF_OWNER,
    INBLOOM_OWNER,
    KBH_OWNER,
    LEARN_BRAND_TO_OWNER,
    _abs_is_therapy,
    _bi_location_urls,
    _bluesprig_address,
    parse_bf_page,
    parse_bi_page,
    parse_caravel_page,
    parse_catalyst_page,
    parse_centria_page,
    parse_hhf_roster,
    parse_inbloom_roster,
    parse_kbh_page,
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


class TestHelpingHandsFamily:
    def test_normalizes_mixed_state_forms(self):
        # The store locator returns the state as either the code or the full name,
        # sometimes for the same state. A site key needs the code, so both fold.
        doc = json.dumps(
            [
                {"address": "1 Iron Bridge Dr", "city": "Collegeville",
                 "state": "Pennsylvania", "zip": "19426"},
                {"address": "770 Miles Rd", "city": "West Chester",
                 "state": "PA", "zip": "19380"},
            ]
        ).encode()
        centers = parse_hhf_roster(doc)
        assert [c.state for c in centers] == ["PA", "PA"]
        assert all(c.owner_name == HHF_OWNER for c in centers)

    def test_splits_a_crammed_street(self):
        # A few store rows carry the whole address in the street field; left alone
        # the city and ZIP land in the site key and stop the centre matching the
        # same building from the registry.
        doc = json.dumps(
            [{"address": "275 Curry Hollow Rd Suite G100, Pittsburgh, PA 15236",
              "city": "Pleasant Hills", "state": "PA", "zip": "15236"}]
        ).encode()
        (c,) = parse_hhf_roster(doc)
        assert c.address_line1 == "275 Curry Hollow Rd Suite G100"
        assert c.city == "Pittsburgh"
        assert c.zip == "15236"


# A Centria centre page, trimmed to the address block and the noise around it. The
# first span.info is the centre's address; the rest carry no "City, ST ZIP" tail.
CENTRIA_PAGE = """
<html><body>
  <ul class="location-details">
    <li><span class="icon"><svg></svg></span>
      <span class="info">5275 N. 59th Ave <br />Glendale, AZ, 85301
        <span class="miles-away">&mdash; <span class="miles-away-output"></span>
        miles away</span></span></li>
    <li class="phone"><span class="info">Phone: (855) 772-8847</span></li>
    <li class="hours"><span class="info">Hours: Mon: 8:00 am - 7:00 pm</span></li>
  </ul>
</body></html>
"""


class TestParseCentriaPage:
    def test_reads_the_address_block(self):
        c = parse_centria_page(CENTRIA_PAGE)
        assert c is not None
        assert c.owner_name == CENTRIA_OWNER
        assert c.address_line1 == "5275 N. 59th Ave"
        assert c.city == "Glendale"
        assert c.state == "AZ"
        assert c.zip == "85301"

    def test_phone_and_hours_blocks_are_not_addresses(self):
        # Only the block with a City, ST ZIP tail is an address; a page that has the
        # info spans but none with that shape yields nothing rather than a bad row.
        page = (
            '<span class="info">Phone: (855) 772-8847</span>'
            '<span class="info">Hours: Mon: 8:00 am - 7:00 pm</span>'
        )
        assert parse_centria_page(page) is None

    def test_page_without_an_info_block_yields_nothing(self):
        assert parse_centria_page("<html>no locator</html>") is None


# InBloom: cards are <h6> title then the address either as "STREET <br> City, ST
# ZIP" or (a few cards) the street and locality in separate <p> tags.
INBLOOM_PAGE = """
<h6 class="x">Deer Valley Learning Center</h6><p>20601 N 19th Ave, Suite 100<br>
Phoenix, AZ 85027</p><p><a href=x>More About This Learning Center</a></p>
<h6 class="x">Wallingford Learning Center</h6><p>860 North Main Street Ext, Suite 101A</p>
<p>Wallingford, CT 06492-2449</p><div class=centerBtn></div>
<h6 class="x">Coming Soon Center</h6><p>Opening in 2027</p><div class=centerBtn></div>
"""


class TestParseInbloom:
    def test_reads_both_card_layouts(self):
        centers = parse_inbloom_roster(INBLOOM_PAGE.encode())
        by_city = {c.city: c for c in centers}
        assert set(by_city) == {"Phoenix", "Wallingford"}
        assert by_city["Phoenix"].address_line1 == "20601 N 19th Ave, Suite 100"
        assert by_city["Phoenix"].zip == "85027"
        # Two <p> tags, ZIP+4 trimmed to five.
        assert by_city["Wallingford"].address_line1 == "860 North Main Street Ext, Suite 101A"
        assert by_city["Wallingford"].zip == "06492"
        assert all(c.owner_name == INBLOOM_OWNER for c in centers)

    def test_a_card_without_a_house_number_is_dropped(self):
        # "Opening in 2027" is not a street; a card with no digits in the street is
        # not an address and must not become a clinic.
        assert all("Coming Soon" not in c.address_line1 for c in
                   parse_inbloom_roster(INBLOOM_PAGE.encode()))


class TestAbsKidsFilter:
    def test_diagnosis_only_excluded_therapy_kept(self):
        assert _abs_is_therapy("Randolph Rd ABA Therapy Center")
        assert _abs_is_therapy("Downtown ABA Therapy Center & Autism Diagnosis Clinic")
        assert not _abs_is_therapy("Autism Diagnosis Clinic")


# Behavior Frontiers: heading names the city, so the street is a subtraction. A
# COMING SOON block is a leased-not-open site and must be excluded.
BF_PAGE = (
    "Detroit, MI (Center) Behavior Frontiers Autism Center "
    "7375 Woodward Ave, Suite 2800 Detroit, MI 48202 Phone: 734 "
    "Rogers, MN (Center) - COMING SOON! Behavior Frontiers Autism Center "
    "14020 Northdale Blvd, Ste B Rogers, MN 55374 Phone: 734 "
    "San Antonio, TX, West Side (Center) Behavior Frontiers Autism Center "
    "5282 Medical Drive, Suite 104 San Antonio, TX 78229 Hours: 8"
)


class TestParseBfPage:
    def test_open_centers_kept_coming_soon_dropped(self):
        centers = parse_bf_page(BF_PAGE)
        cities = sorted(c.city for c in centers)
        assert cities == ["Detroit", "San Antonio"]  # Rogers (coming soon) excluded
        det = next(c for c in centers if c.city == "Detroit")
        assert det.address_line1 == "7375 Woodward Ave, Suite 2800"
        assert det.state == "MI" and det.zip == "48202"

    def test_sub_labelled_heading_still_parses(self):
        sa = next(c for c in parse_bf_page(BF_PAGE) if c.city == "San Antonio")
        assert sa.address_line1 == "5282 Medical Drive, Suite 104"


class TestParseKbhPage:
    def test_json_ld_postal_address(self):
        html = (
            '<script type="application/ld+json">'
            '{"@graph":[{"address":{"@type":"PostalAddress",'
            '"streetAddress":"1641 Matthews Township Parkway",'
            '"addressLocality":"Matthews","addressRegion":"NC","postalCode":"28105"}}]}'
            "</script>"
        )
        c = parse_kbh_page(html)
        assert c is not None
        assert c.address_line1 == "1641 Matthews Township Parkway"
        assert c.state == "NC" and c.zip == "28105" and c.owner_name == KBH_OWNER

    def test_text_label_fallback(self):
        c = parse_kbh_page("<p>Address: 5703 Waters Ave, Savannah, GA 31404</p>")
        assert c is not None
        assert c.state == "GA" and c.zip == "31404"


class TestParseCatalystPage:
    def test_marker_content_block(self):
        html = (
            '<script>var m={"markers":[{"address":"x","content":'
            '"<strong>Sioux Falls, SD</strong><br> 1105 W. Russell St.<br> '
            'Sioux Falls, SD 57104<br> Phone: (866) 569-7395"}]};</script>'
        )
        c = parse_catalyst_page(html)
        assert c is not None
        assert c.address_line1 == "1105 W. Russell St."
        assert c.city == "Sioux Falls" and c.state == "SD" and c.zip == "57104"


class TestBluesprigAddress:
    """The BlueSprig leaf-page address reader: strict JSON-LD, a regex fallback
    for pages whose JSON-LD is malformed, and refusal on non-addresses."""

    def _page(self, ld: str) -> str:
        return f'<html><head><script type="application/ld+json">{ld}</script></head></html>'

    def test_strict_jsonld(self):
        ld = json.dumps(
            {
                "@type": "MedicalClinic",
                "address": {
                    "@type": "PostalAddress",
                    "streetAddress": "5457 SW Canyon Ct.",
                    "addressLocality": "Portland",
                    "addressRegion": "OR",
                    "postalCode": "97221",
                },
            }
        )
        got = _bluesprig_address(self._page(ld))
        assert got == ("5457 SW Canyon Ct.", "Portland", "OR", "97221")

    def test_graph_wrapped(self):
        ld = json.dumps(
            {
                "@graph": [
                    {"@type": "FAQPage"},
                    {
                        "@type": "MedicalClinic",
                        "address": {
                            "streetAddress": "23 Hospital Dr. Suite 102",
                            "addressLocality": "Abilene",
                            "addressRegion": "TX",
                            "postalCode": "79606",
                        },
                    },
                ]
            }
        )
        got = _bluesprig_address(self._page(ld))
        assert got == ("23 Hospital Dr. Suite 102", "Abilene", "TX", "79606")

    def test_malformed_jsonld_regex_fallback(self):
        # An unquoted openingHours value breaks a strict parse; the well-formed
        # address fields must still be recovered rather than the row lost.
        ld = (
            '{"@type":"MedicalClinic",'
            '"openingHours": Mo-Fr: 08:00-18:00,'
            '"address":{"streetAddress":"8650 Brentwood Blvd Suite G",'
            '"addressLocality":"Brentwood","addressRegion":"CA","postalCode":"94513"}}'
        )
        assert _bluesprig_address(self._page(ld)) == (
            "8650 Brentwood Blvd Suite G",
            "Brentwood",
            "CA",
            "94513",
        )

    def test_in_home_is_refused(self):
        # The site files an in-home service as streetAddress "In Home Services";
        # with no house number it is not an address and must be dropped.
        ld = json.dumps(
            {
                "@type": "MedicalClinic",
                "address": {
                    "streetAddress": "In Home Services",
                    "addressLocality": "Capitola",
                    "addressRegion": "CA",
                    "postalCode": "95010",
                },
            }
        )
        assert _bluesprig_address(self._page(ld)) is None

    def test_no_address_block_is_refused(self):
        ld = json.dumps({"@type": "MedicalClinic", "telephone": "555-1212"})
        assert _bluesprig_address(self._page(ld)) is None

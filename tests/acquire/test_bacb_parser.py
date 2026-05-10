"""Tests for the BACB HTML parser. No browser, no HTTP - just the parse function."""


from fundprint.acquire.bacb import _split_address, parse_bacb_html


class TestParseBacbHtml:
    def test_parses_three_providers(self, bacb_html):
        rows = parse_bacb_html(bacb_html)
        assert len(rows) == 3

    def test_names_extracted(self, bacb_html):
        rows = parse_bacb_html(bacb_html)
        names = [r["raw_name"] for r in rows]
        assert "Smith, Jane A." in names
        assert "Doe, John B." in names
        assert "Garcia, Maria C." in names

    def test_credentials_extracted(self, bacb_html):
        rows = parse_bacb_html(bacb_html)
        by_name = {r["raw_name"]: r for r in rows}
        assert by_name["Smith, Jane A."]["credential_type"] == "BCBA"
        assert by_name["Doe, John B."]["credential_type"] == "BCaBA"
        assert by_name["Garcia, Maria C."]["credential_type"] == "BCBA-D"

    def test_npi_extracted_for_first_two(self, bacb_html):
        rows = parse_bacb_html(bacb_html)
        by_name = {r["raw_name"]: r for r in rows}
        assert by_name["Smith, Jane A."]["npi"] == "1234567890"
        assert by_name["Doe, John B."]["npi"] == "0987654321"

    def test_empty_npi_for_third_provider(self, bacb_html):
        rows = parse_bacb_html(bacb_html)
        by_name = {r["raw_name"]: r for r in rows}
        # Garcia has no NPI in the fixture
        assert by_name["Garcia, Maria C."]["npi"] is None

    def test_returns_empty_on_empty_html(self):
        rows = parse_bacb_html(b"<html><body></body></html>")
        assert rows == []

    def test_no_crash_on_malformed_html(self):
        rows = parse_bacb_html(b"<table><tr><td></td></tr></table>")
        # Should return empty list, not raise
        assert isinstance(rows, list)


class TestSplitAddress:
    def test_full_address(self):
        line1, city, state, zip_ = _split_address("123 Main St, Springfield, IL 62701")
        assert city == "Springfield"
        assert state == "IL"
        assert zip_ == "62701"

    def test_city_state_zip_only(self):
        line1, city, state, zip_ = _split_address("Austin, TX 78701")
        assert city == "Austin"
        assert state == "TX"
        assert zip_ == "78701"

    def test_empty_string(self):
        line1, city, state, zip_ = _split_address("")
        assert all(v is None for v in [line1, city, state, zip_])

    def test_no_address_components(self):
        line1, city, state, zip_ = _split_address("no address here")
        # Falls back to returning the whole text as line1
        assert line1 == "no address here"
        assert city is None

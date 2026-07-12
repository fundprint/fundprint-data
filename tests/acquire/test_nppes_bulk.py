"""Tests for the NPPES bulk-registry ingester.

The archive is 1.1GB, so these exercise the pure parts against a synthetic zip
built in-memory: the same shape, four rows, no network.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import pytest

from fundprint.acquire.nppes_bulk import _iso, _member, extract
from fundprint.resolve.clinic_link import normalize

MAIN_COLS = [
    "NPI",
    "Entity Type Code",
    "Provider Organization Name (Legal Business Name)",
    "Provider Other Organization Name",
    "Provider First Line Business Practice Location Address",
    "Provider Business Practice Location Address City Name",
    "Provider Business Practice Location Address State Name",
    "Provider Business Practice Location Address Postal Code",
    "Last Update Date",
    "NPI Deactivation Date",
    "Healthcare Provider Taxonomy Code_1",
]
PL_COLS = [
    "NPI",
    "Provider Secondary Practice Location Address- Address Line 1",
    "Provider Secondary Practice Location Address - City Name",
    "Provider Secondary Practice Location Address - State Name",
    "Provider Secondary Practice Location Address - Postal Code",
]

MAIN_ROWS = [
    # A tracked ABA chain, live.
    ["1", "2", "HOPEBRIDGE, LLC", "", "1 A ST", "FISHERS", "IN", "46037",
     "01/15/2026", "", "103K00000X"],
    # Same chain, but the NPI is DEACTIVATED: must not become a clinic.
    ["2", "2", "HOPEBRIDGE, LLC", "", "2 DEAD ST", "GARY", "IN", "46402",
     "01/01/2019", "03/04/2024", "103K00000X"],
    # An individual (Entity Type 1), not an organization: not a clinic.
    ["3", "1", "", "", "3 PERSON RD", "FISHERS", "IN", "46037",
     "01/15/2026", "", "103K00000X"],
    # A non-ABA portfolio company of a tracked PE firm. The bulk registry is full
    # of these and they must never be captured as ABA clinics.
    ["4", "2", "MYEYEDR OPTOMETRY", "", "4 EYE BLVD", "TAMPA", "FL", "33601",
     "01/15/2026", "", "152W00000X"],
]
PL_ROWS = [
    # A second center run under the live chain's NPI: this is the whole point of
    # the bulk file, and the API cannot see it.
    ["1", "9 SECOND ST", "CARMEL", "IN", "46032"],
    # A secondary location of the DEACTIVATED NPI: must not slip back in.
    ["2", "8 GHOST AVE", "GARY", "IN", "46402"],
    # A secondary location of the non-ABA company: must not slip in either.
    ["4", "7 EYE LN", "OCALA", "FL", "34470"],
]


def _csv(cols: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerows(rows)
    return buf.getvalue().encode()


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    p = tmp_path / "monthly.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("npidata_pfile_20050523-20260607.csv", _csv(MAIN_COLS, MAIN_ROWS))
        z.writestr("pl_pfile_20050523-20260607.csv", _csv(PL_COLS, PL_ROWS))
        z.writestr("npidata_pfile_20050523-20260607_fileheader.csv", b"ignored\n")
    return p


BRANDS = [(normalize("Hopebridge"), "Hopebridge")]


class TestIsoDate:
    def test_converts_nppes_mmddyyyy(self):
        assert _iso("01/15/2026") == "2026-01-15"

    def test_pads_single_digits(self):
        assert _iso("3/4/2024") == "2024-03-04"

    def test_blank_is_none(self):
        assert _iso("") is None
        assert _iso(None) is None


class TestMember:
    def test_skips_the_fileheader_member(self, archive: Path):
        z = zipfile.ZipFile(archive)
        assert "fileheader" not in _member(z, "npidata_pfile")


class TestExtract:
    def test_captures_primary_and_secondary_locations(self, archive: Path):
        result = extract(archive, BRANDS)
        streets = sorted(normalize(r.address_line1) for r in result.rows)
        assert streets == [normalize("1 A ST"), normalize("9 SECOND ST")]

    def test_secondary_location_is_the_point(self, archive: Path):
        # The API can only ever see the primary address; the bulk file gives the
        # chain's other centers, which is the whole reason this module exists.
        result = extract(archive, BRANDS)
        assert result.secondary_locations == 1
        secondary = [r for r in result.rows if r.location_kind == "secondary"]
        assert len(secondary) == 1
        assert secondary[0].npi == "1"
        assert secondary[0].raw_name == "HOPEBRIDGE, LLC"

    def test_deactivated_npi_is_skipped(self, archive: Path):
        result = extract(archive, BRANDS)
        assert result.deactivated_skipped == 1
        assert not any(r.npi == "2" for r in result.rows)

    def test_deactivated_npis_secondary_location_is_also_skipped(self, archive: Path):
        result = extract(archive, BRANDS)
        assert not any(
            normalize("8 GHOST AVE") == normalize(r.address_line1) for r in result.rows
        )

    def test_individual_providers_are_not_clinics(self, archive: Path):
        result = extract(archive, BRANDS)
        assert not any(r.npi == "3" for r in result.rows)

    def test_non_aba_portfolio_company_is_never_captured(self, archive: Path):
        # MyEyeDr. is a real KKR portfolio company and sits in owner_entity. If it
        # were passed in as a brand it would capture a thousand optometry offices,
        # so only is_aba owners are ever passed. Here it is simply not matched.
        result = extract(archive, BRANDS)
        assert not any(r.npi == "4" for r in result.rows)
        assert result.matched_npis == 1

    def test_counts_are_reported_honestly(self, archive: Path):
        result = extract(archive, BRANDS)
        assert result.npis_scanned == 4
        assert result.orgs_scanned == 3  # the individual is not an organization

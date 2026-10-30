---
license: cc-by-4.0
language:
- en
pretty_name: "Fundprint: private equity ownership of US autism therapy clinics"
size_categories:
- 1K<n<10K
task_categories:
- tabular-classification
tags:
- private-equity
- health-policy
- autism
- applied-behavior-analysis
- healthcare
- ownership
- medicaid
- geospatial
configs:
- config_name: clinics
  default: true
  data_files: data/clinics.parquet
- config_name: owners
  data_files: data/owners.parquet
- config_name: firms
  data_files: data/firms.parquet
---

# Fundprint: who owns America's autism therapy clinics

**Private equity owns at least 1,621 autism therapy (ABA) clinics across 42 US states.**
This dataset names, locates and traces each one to a public source document that was
fetched and content-hashed.

- Site: [whofundsmytherapist.com](https://whofundsmytherapist.com)
- Archived release and DOI: [10.5281/zenodo.23426864](https://doi.org/10.5281/zenodo.23426864)
- Pipeline code: [github.com/fundprint/fundprint-data](https://github.com/fundprint/fundprint-data)
- Methodology: [github.com/fundprint/fundprint-methodology](https://github.com/fundprint/fundprint-methodology)

Dataset version `2026.07-beta`, methodology `2026.10-verification-v1`.

## At a glance

| | |
|---|---|
| Clinics traced | **1,757** in 42 states, held by 19 institutional owners |
| Private-equity owned | **1,621**, under 15 sponsors |
| Other financial owners | 136 (a pension fund, a family office, two search funds) |
| PE share of all ABA sites (registry basis, a floor) | **2.8%** nationally; **15.8%** in Minnesota, **13.2%** in New Mexico, **9.5%** in Arizona |
| Attested by the owner's own directory | 1,575 of 1,757 |
| Hand-verified accuracy | **66 of 67** sampled PE clinics correct on site, address, brand and owner (98.5%, 95% CI 92.0% to 99.7%) |
| Platform coverage | 21 of 32 known PE-backed ABA platforms |

## Load it

```python
from datasets import load_dataset

clinics = load_dataset("gameloft737/fundprint", "clinics", split="train")
pe = clinics.filter(lambda r: r["firm_type"] == "private_equity")   # 1,621
```

## Files

| Path | What it is |
|---|---|
| `data/clinics.parquet` / `.csv` | One row per clinic (a physical site). The main table. |
| `data/owners.parquet` / `.csv` | Operating companies (brands) and the firm that owns each. |
| `data/firms.parquet` / `.csv` | Institutional owners, including former owners with 0 clinics. |
| `derived/aba_market.json` | National and per-state shares against the 21,088 ABA sites in the federal registry. |
| `derived/coverage.json` | The 32 in-scope PE-backed platforms and which ones this dataset has read. |
| `derived/reconciliation.json` | Comparison with the published JAMA Pediatrics and PESP estimates. |
| `derived/state_files.json` | HHS OIG Medicaid ABA audits paired with each state's footprint. |
| `derived/verification.json`, `reviewed_3bf05f0a.csv` | The hand-verification sample and its scored results. |
| `derived/snapshot_meta.json` | Versions and headline totals of this release. |

In the CSVs, the `sources` list is joined with ` | `.

## Clinic fields

| Field | Meaning |
|---|---|
| `id` | Stable clinic identifier. |
| `name`, `address`, `city`, `state`, `zip` | The site as its best source states it. |
| `npi` | A registry provider ID at the site, if any. **Not an identity key**: one NPI can cover many sites and one site can carry several NPIs. |
| `lat`, `lng` | ZIP-level, not street-level: the Census ZCTA centroid of the ZIP (or of its 3-digit prefix). One clinic is unplaced. |
| `owner_name`, `owner_id` | The operating company (brand). |
| `firm_name`, `firm_id`, `firm_type` | The controlling institutional owner. `firm_type` is `private_equity`, `pension_fund`, `family_office` or `other` (search funds). |
| `confidence_overall` | The weakest of the four dimensions below: `owner_verified`, `registry_current`, `registry_aging` or `registry_stale`. |
| `confidence_open` | Whether the site is still operating: `owner_listed` (the owner's directory lists it today) or a registry freshness grade. |
| `confidence_address` | `owner_stated` or `registry_filed`. |
| `confidence_site_type` | `center` (the owner lists it as a centre) or `unverified`. |
| `ownership_basis` | `curated` (a hand-verified acquisition source) or `portfolio` (the firm's own portfolio page). |
| `registry_last_updated` | When the registry record was last certified, if the site has one. |
| `confidence_score`, `confidence_method` | The resolver's name-match score and method. |
| `sources` | URLs of every source document behind the row. |

## How it was built

Four public source families: the CMS NPPES bulk registry, operators' own published
location directories and rosters, PE firms' portfolio pages, and hand-verified
acquisition announcements. Every source is fetched and content-hashed. Rules that
matter most:

- **A clinic is a physical site**, keyed on canonicalized street and ZIP within an
  owner, never on an NPI.
- **An owner's complete directory overrides the registry in both directions**: it adds
  centres the registry never recorded and retires registry rows the owner does not run.
- **A wrong street is worse than no street.** Parsers refuse an address they cannot split
  safely.
- **Corrections are quarantines, never deletes.** Every decision is an append-only record.

Full rules: [METHODOLOGY.md](https://github.com/fundprint/fundprint-methodology/blob/main/METHODOLOGY.md).

## Limitations

- **Both the count and the shares are floors.** About 500 facilities sit at 11 PE-backed
  platforms this dataset has not yet read; they are named in `derived/coverage.json`.
- **The 2.8% share uses the registry on both sides**: 580 PE sites the registry can see,
  out of 21,088 registry ABA sites. Directory-only clinics are excluded from both sides,
  and closed centres can be removed from the numerator but not the denominator.
- **Do not compute `pe_clinics / clinics` (about 91%).** That is PE's share of this dataset,
  not of the market.
- **182 clinics rest on the registry alone.** The registry never marks a closed site
  closed, so 30 of them are graded `registry_stale` (untouched 6+ years).
- **The verification sample was reviewed by the author alone**, so there is no
  inter-rater figure.
- **No audit attributes any improper payment to private equity**, and this dataset
  makes no such claim. The state audit files pair figures with footprints; they do not
  assert cause.

## Citation

```bibtex
@dataset{doke_2026_fundprint,
  author    = {Doke, Atharva},
  title     = {Fundprint: a site-level census of institutionally owned ABA clinics},
  year      = {2026},
  version   = {2026.10-verification-v1},
  publisher = {Zenodo},
  doi       = {10.5281/zenodo.23426864},
  url       = {https://doi.org/10.5281/zenodo.23426864}
}
```

## License

CC BY 4.0: reuse freely, with attribution. Archived copies of third-party source
documents are not included and are not covered by this license.

Corrections are welcome and are published with a changelog entry: open an issue on
[fundprint-data](https://github.com/fundprint/fundprint-data/issues).

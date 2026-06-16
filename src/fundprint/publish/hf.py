"""Hugging Face dataset export.

Writes Parquet files from validated views and generates a data card README.
The upload step is a stub unless huggingface_hub is importable - Parquet
files are always written locally to dist/release/<dataset_version>/.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Try to import optional dependencies. Parquet via pyarrow or pandas;
# HF upload via huggingface_hub. Neither is required for local builds.
try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    _HAS_PYARROW = True
except ImportError:
    _HAS_PYARROW = False

try:
    from huggingface_hub import HfApi

    _HAS_HF = True
except ImportError:
    _HAS_HF = False


def build_parquet_files(
    conn: Any,
    *,
    dataset_version: str,
    schema_version: str,
    resolver_version: str,
    methodology_version: str,
    dist_dir: Path | None = None,
) -> Path:
    """Write Parquet files for all public tables and return the release directory.

    One Parquet file per public table. If pyarrow is unavailable the files
    are written as newline-delimited JSON so the pipeline does not hard-fail.
    """
    if dist_dir is None:
        dist_dir = Path("dist/release")

    release_dir = dist_dir / dataset_version
    release_dir.mkdir(parents=True, exist_ok=True)

    tables = _fetch_public_tables(conn)
    for table_name, rows, columns in tables:
        dest = release_dir / f"{table_name}.parquet"
        _write_parquet(dest, rows, columns)

    readme = build_data_card(
        dataset_version=dataset_version,
        schema_version=schema_version,
        resolver_version=resolver_version,
        methodology_version=methodology_version,
    )
    (release_dir / "README.md").write_text(readme)

    return release_dir


def build_data_card(
    *,
    dataset_version: str,
    schema_version: str,
    resolver_version: str,
    methodology_version: str,
) -> str:
    """Return the data card README string with YAML front-matter pinning all four versions."""
    front_matter = (
        "---\n"
        f"dataset_version: {dataset_version}\n"
        f"schema_version: {schema_version}\n"
        f"resolver_version: {resolver_version}\n"
        f"methodology_version: {methodology_version}\n"
        "license: cc-by-4.0\n"
        "task_categories:\n"
        "  - other\n"
        "---\n"
    )

    body = f"""# Fundprint: PE Ownership of U.S. ABA / Autism Therapy Clinics

## Dataset description

Fundprint tracks private-equity ownership of U.S. applied behavior analysis (ABA)
and autism therapy clinics. The frame is consumer protection: families deserve to
know who owns the clinic providing their child's care. The dataset does not take any
position on ABA therapy itself.

Every row traces to a public URL. Confidence scores and method tags are included
for every ownership claim so consumers can apply their own threshold.

### Owner types

Most ownership chains trace to a traditional private-equity firm. A small number of
chains are owned by other institutional financial owners — pension funds or family
offices — that operate them like PE-backed platforms. Rather than mislabel those as
private equity, the ultimate owner is tagged honestly via the `firm_type` field on
`parent_pe_firm` (`private_equity`, `pension_fund`, `family_office`, or `other`).
For example, Acorn Health is owned by a pension fund (Ontario Teachers') and Butterfly
Effects by a family office (Moran Capital Partners). Consumers who want strictly
PE-backed clinics can filter on `firm_type = 'private_equity'`.

## What this dataset is NOT

- It is not a clinical quality or outcomes dataset.
- It is not a directory of all ABA clinics - only those with traceable PE links.
- It does not contain patient-level data or non-public financials.

## Versions pinned to this release

| Dimension           | Version                 |
|---------------------|-------------------------|
| Dataset             | `{dataset_version}`     |
| Schema              | `{schema_version}`      |
| Resolver            | `{resolver_version}`    |
| Methodology         | `{methodology_version}` |

## Schema

### resolution_claim

| Column               | Type    | Description                                                 |
|----------------------|---------|-------------------------------------------------------------|
| id                   | string  | UUID primary key.                                           |
| claim_type           | string  | clinic_to_owner / owner_to_pe_firm / acquisition_event.     |
| clinic_id            | string  | FK to clinic.                                               |
| owner_entity_id      | string  | FK to owner_entity.                                         |
| parent_pe_firm_id    | string  | FK to parent_pe_firm.                                       |
| acquisition_event_id | string  | FK to acquisition_event.                                    |
| confidence_score     | float   | In [0, 1]. Minimum across the provenance chain.             |
| confidence_method    | string  | One of: exact_match, fuzzy_high, fuzzy_low, llm_inferred. |
| resolver_version     | string  | Code version that produced this claim.                      |
| extracted_at         | datetime| When the claim was written.                                 |
| source_record_ids    | list    | UUIDs of supporting source_record rows.                     |
| trust_level          | string  | verified / human_anchored (unverified rows are not exported). |

### clinic

| Column           | Type   | Description                                  |
|------------------|--------|----------------------------------------------|
| id               | string | UUID primary key.                            |
| name             | string | Operating name.                              |
| city             | string | City.                                        |
| state            | string | Two-letter state code.                       |
| zip              | string | ZIP code.                                    |
| npi              | string | NPI number if available.                     |
| owner_entity_id  | string | FK to owner_entity.                          |
| confidence_score | float  | Confidence in the clinic-owner link.         |

## Known limitations

- Coverage is uneven across states. States with richer Medicaid provider directories
  have higher recall.
- LLM-inferred links carry inherent uncertainty; confidence scores reflect this.
- Acquisition dates may be "circa year" estimates when exact dates are not in public filings.
- This dataset covers institutionally-owned chains (private equity plus a few pension
  funds / family offices, distinguished by `firm_type`); independent and owner-operated
  chains are out of scope per the methodology definition.

## Corrections and contact

To challenge a specific row or report a factual error, open an issue at the project
repository. Do not contact individual researchers directly.

Methodology definition: see the `fundprint-methodology` repository, version `{methodology_version}`.
"""
    return front_matter + body


def upload_to_hf(
    release_dir: Path,
    *,
    repo_id: str,
    dataset_version: str,
) -> None:
    """Upload the release directory to Hugging Face.

    Stub if huggingface_hub is not installed - prints what it would do.
    """
    if not _HAS_HF:
        logger.warning(
            "huggingface_hub is not installed; skipping upload. "
            "Would upload %s to %s as tag %s.",
            release_dir,
            repo_id,
            dataset_version,
        )
        print(
            f"[stub] Would upload {release_dir} to HF repo {repo_id!r} "
            f"as dataset version {dataset_version!r}."
        )
        return

    api = HfApi()
    api.upload_folder(
        folder_path=str(release_dir),
        repo_id=repo_id,
        repo_type="dataset",
        commit_message=f"Release {dataset_version}",
    )
    logger.info("Uploaded release %s to %s", dataset_version, repo_id)


# ---- internal helpers -------------------------------------------------------


def _fetch_public_tables(conn: Any) -> list[tuple[str, list, list[str]]]:
    """Return (table_name, rows, column_names) for each public table."""
    public_tables = [
        (
            "resolution_claims",
            """
            SELECT
                rc.id, rc.claim_type,
                rc.clinic_id, rc.owner_entity_id, rc.parent_pe_firm_id,
                rc.acquisition_event_id,
                rc.confidence_score, rc.confidence_method,
                rc.resolver_version, rc.extracted_at,
                rc.source_record_ids,
                vrd.trust_level, vrd.validation_run_id
            FROM resolution_claim rc
            JOIN validation_run_decision vrd ON vrd.resolution_claim_id = rc.id
            WHERE vrd.decision = 'passed'
              AND rc.source_record_ids IS NOT NULL
            """,
            [
                "id", "claim_type", "clinic_id", "owner_entity_id",
                "parent_pe_firm_id", "acquisition_event_id",
                "confidence_score", "confidence_method",
                "resolver_version", "extracted_at",
                "source_record_ids", "trust_level", "validation_run_id",
            ],
        ),
        (
            "clinics",
            """
            SELECT
                c.id, c.name, c.city, c.state, c.zip, c.npi,
                c.owner_entity_id, c.confidence_score, c.confidence_method,
                c.resolver_version, c.extracted_at, c.source_record_ids
            FROM clinic c
            WHERE c.superseded_by IS NULL
            """,
            [
                "id", "name", "city", "state", "zip", "npi",
                "owner_entity_id", "confidence_score", "confidence_method",
                "resolver_version", "extracted_at", "source_record_ids",
            ],
        ),
    ]

    results = []
    for table_name, query, columns in public_tables:
        try:
            rows = conn.execute(query).fetchall()
        except Exception as exc:
            logger.warning("Could not fetch %s: %s", table_name, exc)
            rows = []
        results.append((table_name, rows, columns))
    return results


def _write_parquet(dest: Path, rows: list, columns: list[str]) -> None:
    """Write rows to dest as Parquet, falling back to NDJSON if pyarrow is absent."""
    if _HAS_PYARROW:
        if rows:
            # Convert list-of-tuples to dict-of-lists for Arrow.
            col_data = {col: [r[i] for r in rows] for i, col in enumerate(columns)}
            table = pa.table({k: _to_arrow_array(v) for k, v in col_data.items()})
        else:
            table = pa.table({col: pa.array([], type=pa.string()) for col in columns})
        pq.write_table(table, str(dest))
        return

    # Fallback: NDJSON with a .parquet extension is not ideal but keeps the
    # pipeline runnable without pyarrow. Consumers should install pyarrow.
    with dest.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(dict(zip(columns, [str(v) for v in row]))) + "\n")


def _to_arrow_array(values: list) -> Any:
    """Convert a column of Python values to a string Arrow array.

    The column is typed as string, so every scalar must be stringified -- not
    just lists/dicts. UUID and datetime objects (as returned by psycopg) are
    not bytes/str and would otherwise raise ArrowTypeError. None is preserved
    as null; lists/dicts are JSON-encoded with default=str so UUIDs nested in
    source_record_ids serialize cleanly (e.g. ["f2e3..."], not "[UUID('...')]").
    """
    converted = [
        None
        if v is None
        else json.dumps(v, default=str)
        if isinstance(v, (list, dict))
        else str(v)
        for v in values
    ]
    return pa.array(converted, type=pa.string())

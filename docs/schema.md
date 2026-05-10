# Schema

**Owns:** the relational shape of the dataset — tables, keys, provenance fields, and how they reference each other.
**Depends on:** definitions in `fundprint-methodology` for *clinic*, *owner_entity*, *acquisition*, *PE-backed*.
**Consumed by:** every other layer. The schema is the contract between Acquire/Store/Resolve/Validate/Publish.

The schema is the spine of the system. If a column is fuzzy, every layer downstream gets fuzzier. Treat schema changes the way a database team would: forward-only migrations, versioned, reviewed.

## Core entities

Five tables carry the dataset's meaning. Everything else is staging, audit, or derived view.

| Table              | What it represents                                               |
|--------------------|------------------------------------------------------------------|
| `clinic`           | A physical or operating ABA service location.                    |
| `owner_entity`     | The legal entity that operates a clinic (a chain, an LLC, etc.). |
| `parent_pe_firm`   | A PE firm or fund that owns or has owned an `owner_entity`.      |
| `acquisition_event`| A dated change of ownership (PE buyout, divestiture, bankruptcy).|
| `source_record`    | A pointer to a public document supporting any claim above.       |

Every claim about ownership is a relation between these. Every relation is supported by one or more `source_record` rows. **A relation without source records is not a claim; it is noise.**

## Provenance fields (on every derived row)

Any row produced by Resolve or written to a publishable view must carry:

- `source_record_ids` — array. The supporting documents.
- `confidence_score` — float in `[0, 1]`. How sure we are.
- `confidence_method` — enum: `exact_match`, `fuzzy_high`, `fuzzy_low`, `llm_inferred`, `human_verified`. How confidence was assigned.
- `resolver_version` — string. Which version of the resolution code produced this.
- `extracted_at` — timestamp.
- `superseded_by` — nullable FK. Set when a newer row replaces this one. The old row is *kept*, never deleted.

These six fields are non-negotiable. A migration that adds a derived table without them must be rejected.

## Append-only vs. mutable

- **Append-only:** `source_record`, `acquisition_event`, validation runs, snapshot metadata. These are facts about the world; we add to them but do not edit.
- **Mutable but versioned:** `clinic`, `owner_entity`, `parent_pe_firm`. These can be updated as resolution improves, but old versions are preserved via `superseded_by`.
- **Recomputable:** any view or join that aggregates the above. Drop and rebuild freely; never store derived numbers as source-of-truth.

If you are tempted to mutate an append-only table in place — to fix a typo, dedupe, "clean up" — stop. Add a new row that supersedes; never edit history.

## Vector columns

`owner_entity` and `clinic` carry an embedding column (`name_embedding`) for fuzzy match. The embedding model version is stored in a sibling column (`name_embedding_model`). Embeddings are recomputed when the model version changes; old embeddings are not deleted, they are tagged with their model.

Fuzzy matches across embedding versions are not allowed. Resolution always pins to a single model version per run.

## Migrations

Forward-only. The conventions:

- Every migration is in `schema/` with a timestamped filename.
- Migrations never `DROP COLUMN` on a populated production table without a deprecation period (mark unused, wait one full release, then drop).
- A migration that changes the meaning of an existing column is two migrations: add the new column with the new meaning, backfill, then deprecate the old one.
- A migration that affects what counts as PE-backed must reference a methodology repo commit.

## Versioning

The dataset has four versions that move together at release time:

- `schema_version` — the shape of the tables.
- `resolver_version` — the code that filled them.
- `methodology_version` — the definitions governing them (lives in `fundprint-methodology`).
- `dataset_version` — the public-facing version stamped on Hugging Face.

A release pins all four. A consumer who pins `dataset_version` gets a reproducible artifact.

## What the schema does not store

- **Revenue, margins, EBITDA, or any non-public financials.** Even if a source leaks them, they do not enter the schema.
- **Patient-level data.** Ever. The dataset is about ownership, not care.
- **Predictions or forecasts.** No "likely owner" columns. If we are not sure, we do not claim.
- **Free-text editorial commentary.** Notes go in the methodology repo, not in the data.

When in doubt, the test is: *would I want this column subpoenaed?* If no, it does not belong in the schema.

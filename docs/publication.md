# Publication (the output boundary)

**Owns:** how validated data leaves this repo and reaches external consumers.
**Depends on:** validation runs (only validated rows are publishable), schema versioning, methodology versioning.
**Consumed by:** Hugging Face users, the `fundprint-dashboard`, the `fundprint-methodology` audit appendix, journalists and academics downstream.

Publication is the layer where *internal claims become public artifacts*. After this boundary, mistakes are public mistakes. Treat it accordingly: every publication is a deliberate act, signed off by a human, pinned to versions, and recorded.

## The three consumers

Three distinct external consumers, three distinct contracts. Do not collapse them into one.

| Consumer                  | What it gets                                | Cadence              | Format                     |
|---------------------------|---------------------------------------------|----------------------|----------------------------|
| Hugging Face dataset users| Snapshotted, versioned tables + data card   | Weekly during build  | Parquet + README data card |
| `fundprint-dashboard`     | Read-only Postgres views, latest snapshot   | Continuous (latest)  | SQL views                  |
| `fundprint-methodology`   | Audit summaries + sample queries + counts   | Per release          | Markdown + JSON            |

Each consumer's contract is its own surface. Changing what goes to one consumer is not license to change what goes to the others.

## The Hugging Face contract

The HF dataset is the canonical *citable* artifact. Academic citations and press references will pin to a specific HF release. That gives the release an immutability requirement:

- Every HF release has a `dataset_version`.
- The release pins `schema_version`, `resolver_version`, `methodology_version`, and the validation_run_id that gated it.
- The release is **immutable** once published. Errors are corrected in the *next* release with a documented changelog entry, never by editing the existing release.
- The data card (README on HF) describes: what the dataset is, what it is not, the methodology version it follows, known limitations, and the contact for corrections.

A consumer who downloads `dataset_version=2026.07.01` next year must get exactly the bytes we shipped on that date. That is the entire point of versioning.

## The dashboard contract

The dashboard reads from Supabase via dedicated read-only views, not from the entity tables directly. This is deliberate:

- The view is the contract. Renaming a column in the entity table does not break the dashboard if the view stays stable.
- The view filters to validated, above-floor rows only. Quarantined and unverified claims do not leak through.
- The view exposes only fields the dashboard is allowed to render publicly. Internal scoring details (raw resolver intermediates, candidate sets) stay behind the boundary.

When the dashboard needs a new field, the request goes: dashboard repo issue → view change in this repo → coordinated release. Not the other way around.

## The methodology contract

`fundprint-methodology` consumes a per-release audit packet:

- Counts: clinics, owner entities, parent PE firms, by state and acquirer.
- Confidence-method breakdown: what fraction of claims is `human_verified` vs. `llm_inferred` vs. `fuzzy_high` etc.
- The 100-row hand-validation sample for the release, with reviewer labels.
- A diff against the previous release: rows added, superseded, quarantined.

The methodology repo embeds this packet in its versioned white paper. A reader of the white paper can reconstruct the dataset's state at the time of the release.

## Filters between internal and external

The publication boundary applies these filters, every time:

1. **Confidence floor** — methodology-defined per claim type (see `validation.md`).
2. **Quarantine exclusion** — quarantined claims never publish.
3. **Provenance completeness** — a row missing `source_record_ids` does not publish, regardless of confidence.
4. **PII / sensitive-field exclusion** — the schema is designed not to store these, but the export step double-checks. Any field added later that could carry sensitive content needs an explicit allow-list entry.
5. **Embargo flags** — a row can be flagged for embargo (e.g., during a press exclusive). Embargoed rows do not export until the flag is cleared.

The export step that applies these filters is auditable. A diff between "rows in entity table" and "rows in publication" must be explainable, not mysterious.

## Release sequencing

A release is one act with one tag, but it touches three repos. The order is:

1. `fundprint-methodology` cuts its release first — the definitions for this dataset version are frozen.
2. `fundprint-data` runs validation against the methodology release, produces the audit packet, publishes to HF, and tags `dataset_version`.
3. `fundprint-dashboard` updates its view consumption, pins the HF release link in its UI, and deploys.

Out of order, the dashboard might display data that does not match the methodology document users are reading. That is exactly the kind of inconsistency that destroys credibility in a single screenshot.

## What publication does not do

- **Does not produce summary statistics for press.** Press numbers come from queries against the published dataset, not from a separate "press numbers" view. Consistency is the point.
- **Does not bypass validation under any circumstance.** "We need a number for the Tuesday pitch" is never a reason. Run validation.
- **Does not silently update the latest release.** The latest release is named and dated; updates create a new release.
- **Does not export to ad-hoc destinations.** Hugging Face, dashboard views, methodology audit. Three. Anything else is a new contract that gets documented before it gets built.

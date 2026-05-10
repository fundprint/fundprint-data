# Architecture

**Owns:** the high-level shape of the system — five layers, the boundaries between them, and the direction data flows.
**Depends on:** the methodology repo's definitions of *clinic*, *owner_entity*, *PE-backed*.
**Consumed by:** every other doc in this folder, and `fundprint-dashboard` for its data contract.

## The five layers

Fundprint's data pipeline is a sequence of five layers. Each layer has one job, one input shape, one output shape, and one quality guarantee.

```
┌────────────────────────────────────────────────────────────────────┐
│ 1. Acquire      external sources → raw, source-stamped snapshots   │
├────────────────────────────────────────────────────────────────────┤
│ 2. Store        snapshots → typed staging tables in Postgres       │
├────────────────────────────────────────────────────────────────────┤
│ 3. Resolve      staging rows → resolved entities + ownership chains│
├────────────────────────────────────────────────────────────────────┤
│ 4. Validate     resolved entities → confidence-scored, auditable   │
├────────────────────────────────────────────────────────────────────┤
│ 5. Publish      validated rows → HF dataset, dashboard views, etc. │
└────────────────────────────────────────────────────────────────────┘
```

Each layer is documented in its own file. This doc is about the *gaps between* them.

## Why five layers, not three

The naive design is "scrape, clean, publish." That collapses two distinctions that matter:

- **Acquire vs. Store.** Acquisition is where the outside world's instability lives — sites change HTML, APIs rate-limit, PDFs come in funny encodings. Storage is where we promise typed, queryable data. Mixing them means a website redesign breaks queries.
- **Resolve vs. Validate.** Resolution is "best guess." Validation is "is the best guess good enough to publish?" Mixing them means we cannot scale resolution without revisiting validation, and we cannot tighten validation without retouching resolution.

Hold these distinctions even when it feels like overkill on day one. The system gets messier than expected (see Risk #1 in the project plan) and the layered design is what keeps it auditable when it does.

## Boundary contracts

Each arrow between layers has three properties: **shape**, **trust**, **provenance**.

| Boundary           | Shape (what crosses)             | Trust (what's promised)            | Provenance (what's logged)            |
|--------------------|----------------------------------|------------------------------------|---------------------------------------|
| Acquire → Store    | raw documents + metadata          | byte-faithful to source            | source_url, fetched_at, snapshot_id   |
| Store → Resolve    | typed staging rows                | schema-valid, source-linked        | staging_id chain                      |
| Resolve → Validate | resolved entities + scored claims | every claim has a method + score   | resolver_version, input_staging_ids   |
| Validate → Publish | publishable view rows             | meets confidence floor + audited   | validation_run_id, gate_passed_at     |

If you cannot fill out a row in this table for a change you are making, the change is not ready.

## What is sync, what is async

- **Acquisition is async.** Scrapers run on schedules. They never block downstream work; they produce snapshots and log them.
- **Storage is sync within a job.** A scraper that runs ingests its own snapshot in the same job; otherwise we end up with stranded snapshots.
- **Resolution is batch.** It runs on demand or nightly over new staging rows. It is idempotent — re-running over the same input must give the same output for a given resolver_version.
- **Validation is gated.** It runs on demand. A run produces a `validation_run_id`. Publishing reads from the most recent passing run.
- **Publishing is explicit.** No automatic publish-on-green. Releases are tagged manually after a human approves. See `publication.md`.

## What lives where

- **Postgres on Supabase** — staging tables, entity tables, validation runs, snapshots metadata. The system of record.
- **Object storage (Supabase Storage or S3)** — raw snapshot blobs (HTML, PDF, JSON). Linked from staging by `snapshot_id`.
- **Hugging Face** — published, versioned dataset releases. Read-only from the outside.
- **GitHub Actions** — schedulers for scrapers and weekly snapshot jobs. Stateless.
- **GitHub releases / tags** — version markers that pin (resolver_version, schema_version, methodology_version, dataset_version) together.

If you find yourself adding state somewhere not on this list, stop and update this doc first.

## What this architecture is *not* designed to do

- Real-time updates. The dashboard reads snapshots, not a live feed. If a journalist needs a fresher number, we run a snapshot.
- Per-user customization. The dataset is one canonical view. Filters happen in the dashboard.
- Predictions. Fundprint reports on what is, not what might be. No "likely PE-acquired" guesses go to publish.

Holding these limits keeps the system small enough to defend.

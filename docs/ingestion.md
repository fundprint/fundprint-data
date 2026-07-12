# Ingestion (the input boundary)

**Owns:** how the outside world enters the system. Scrapers, APIs, file drops, manual imports.
**Depends on:** the schema's `source_record` and staging-table contracts.
**Consumed by:** the Resolve layer, which reads only from staging tables.

This is the layer where reality is messy. Sites change layout, APIs go down, PDFs are scanned images, and PE firms relabel portfolios overnight. Ingestion's job is to absorb that mess and hand the rest of the system a clean, typed, source-stamped feed.

## The ingestion contract

Every ingestion module — whether it is a Playwright scraper, a Scrapy spider, an httpx API client, or a manual CSV importer — must guarantee three things:

1. **Source fidelity.** Every row written to staging is traceable to a specific public URL or document, captured as a `source_record` with a stored snapshot blob.
2. **Idempotence.** Running the same ingestion job twice produces the same staging rows (or replaces with a newer snapshot of the same logical record). It never silently duplicates.
3. **Failure honesty.** A partial scrape is reported as partial. The pipeline does not paper over missing rows; it logs the gap and exits non-zero so the schedule knows.

If a module cannot promise all three, it does not run in production.

## Source families

Ingestion modules are organized by *source family*, not by source. A family shares an access pattern, a rate-limit profile, and an extraction shape:

| Family                  | Examples                                  | Access pattern         |
|-------------------------|-------------------------------------------|------------------------|
| Provider directories    | BACB, state Medicaid, state licensing     | Paginated browse + JSON|
| Regulatory filings      | SEC EDGAR (Form D, 8-K, S-1)              | API or feed            |
| PE portfolio pages      | Blackstone, KKR, Charlesbank, Arsenal…    | Static HTML, low cadence|
| Trade and news press    | BHB, Disability Scoop, NBC, Fortune       | RSS + targeted scrape  |
| Insurance directories   | TRICARE, BCBS, Aetna, UnitedHealth        | Search-form scrape     |
| Social/professional     | LinkedIn signals (with care)              | Constrained API        |
| Paid datasets           | PitchBook, Crunchbase (free tier)         | Licensed API           |

When a new source arrives, place it in an existing family if at all possible. Families share retry logic, rate-limit handling, and snapshot conventions. A new family is a real architectural commitment — it deserves a doc update before code.

## What gets stored when a row is acquired

Every ingestion run produces, for each logical record:

- A **snapshot blob** of the source document (HTML, PDF, JSON), stored in object storage.
- A **`source_record` row** in Postgres pointing at the blob with `source_url`, `fetched_at`, `content_hash`, `source_family`, and `module_version`.
- A **staging row** in the appropriate staging table with the parsed fields plus `source_record_id`.

The snapshot blob is the truth. The parsed staging row is convenience. If the parser was wrong, we re-parse from the blob; we do not re-fetch.

This is not a hypothetical. The NPPES parser (module 0.1.0) discarded the registry's freshness fields — `status`, `last_updated`, `certification_date`, `enumeration_date` — so nothing downstream could tell a live clinic from one that closed years ago. Module 0.2.0 extracts them, and `scripts/backfill_registry_freshness.py` recovered them for every already-staged row by re-parsing the stored blobs. No network call, no re-fetch, no lost history. Keep parsers lossy at your peril; keep blobs and you can always undo it.

## Capture liveness, not just existence

A source that tells you a provider *exists* is not telling you it exists *now*. NPPES is a register of identifiers, not an inventory of open businesses: an NPI is not deactivated when a clinic closes, and the record keeps reporting status `A` forever. A closed clinic and an open one are byte-identical.

So for every source, ask what its liveness signal is, and stage it:

- **Provider registries** (NPPES): the only signal is *staleness* — how long the record has gone untouched. Stage the timestamps. They are the only thing standing between the dataset and a growing population of ghost clinics.
- **Owner location directories**: current by construction. An owner lists the centers it operates today. This is why a directory is worth more than its row count suggests.
- **Enrollment and licensing files**: carry a real liveness signal, because a closed provider stops billing and is disenrolled or lapses.

If a new source has no liveness signal at all, say so in this doc when you add it, and expect its rows to decay.

## Rate limits, retries, and politeness

- All scrapers respect robots.txt unless we have written permission otherwise.
- All scrapers identify themselves with a Fundprint user-agent and a contact email.
- Backoff on 429 / 5xx is exponential with jitter, capped at the run's deadline.
- Per-domain concurrency caps live in a single config file, not scattered across modules.
- A scraper that fetches a paywalled URL is a bug. Test fixtures must be free or licensed.

This is not just ethics; it is risk management. A site that gets hammered will block us, and replacing a blocked source mid-summer is exactly the kind of crisis the timeline cannot absorb.

## Versioning a source

Every ingestion module carries a `module_version`. Bump it when:

- The source's structure changes and parsing logic shifts.
- The set of fields extracted changes.
- The interpretation of an extracted field changes (e.g., we now parse "DBA name" separately from "legal name").

`module_version` is stamped on every staging row. Resolution may, in the future, want to re-run only against rows produced by a given module version — preserve that ability.

## What ingestion never does

- **Never resolves.** Ingestion does not decide that two scraped clinics are the same clinic. That is the Resolve layer's job. Hand both rows forward.
- **Never edits source content.** Whitespace normalization is fine; "fixing" a name is not.
- **Never publishes.** Staging tables are not visible to the dashboard or to Hugging Face exports.
- **Never silently dedupes.** If two snapshots of the same URL come in, both are stored; the older is marked superseded.

## Onboarding a new source — the checklist

Before a new ingestion module merges:

1. Source URL is public (or licensed) and robots-permitted.
2. Module produces snapshot + source_record + staging in one transaction.
3. A test fixture from a captured snapshot proves parsing is stable.
4. The module is registered in the scheduler with a cadence.
5. `docs/ingestion.md` (this file) lists the source family if new.
6. The methodology repo is updated if the source changes what data is available.

Six steps; no shortcuts. The legal posture of the project — public records only, every row source-traceable — depends on every source being defensible.

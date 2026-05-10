# Resolution (the transformation core)

**Owns:** turning staging rows into resolved entities — clinics, owners, parent PE firms — and the chains that link them.
**Depends on:** the schema (entity tables + provenance fields), `fundprint-methodology` for what counts as a "match" or a "PE-backed" relation.
**Consumed by:** the Validate layer, which scores and gates resolution output.

This is where most of the engineering substance lives, and where most of the project's risk lives. The Jan 2026 academic study used PitchBook plus scattered public sources; we are building the public-data equivalent of that linkage. Resolution is the work that does not yet exist anywhere as a free, downloadable artifact.

## The resolution problem in one paragraph

Each staging row says something like: "Sunshine Autism Center, 1234 Main St, Austin TX, BCBA-certified provider." Another row says: "Hopebridge Inc. operates Sunshine Autism Center as part of its Texas region." A third says: "Arsenal Capital Partners acquired Hopebridge LLC in 2017." Resolution's job is to turn those three rows into one chain: *Sunshine Autism Center → Hopebridge → Arsenal Capital Partners*, with a confidence score and a source URL for each link.

## How the work is divided

Resolution is a pipeline of three stages. Each stage produces *scored candidates*, never asserted truth. The next stage filters and ranks.

```
staging rows
    │
    ▼
[ Candidate ]  embedding-based name + locality match → top-K candidate links
    │            (cheap, broad, high recall, low precision)
    ▼
[ Verify   ]  LLM extraction over candidate + supporting docs → scored claim
    │            (expensive, narrow, high precision when calibrated)
    ▼
[ Chain    ]  graph walk over verified links → ownership chain to ultimate PE firm
    │            (deterministic, with min-confidence propagation)
    ▼
resolved entities + chains, each with confidence_score and method tag
```

Three stages, three failure modes. Each stage's bugs look different, which is why they are separate:

- **Candidate** failures look like *missed matches* — clinics that should be linked are not.
- **Verify** failures look like *false matches* — clinics that should not be linked are.
- **Chain** failures look like *broken provenance* — a chain where one hop has lost its source.

Keep them separate so a single failure mode can be diagnosed and fixed without disturbing the others.

## How fuzzy matching and the LLM divide work

The fuzzy matcher (Candidate stage) is the right tool for *we have many possible matches; rank them*. It is wrong for *is this actually the same entity?* — names like "ABC of Texas" and "Action Behavior Centers - Texas" pass any reasonable fuzzy threshold while being either a perfect match or a coincidence.

The LLM (Verify stage) is the right tool for *given two candidates and the supporting documents, is this the same entity?* — it can read a Form D filing and a portfolio page and say "yes, both reference Hopebridge LLC." It is wrong as a *primary search tool* — sending every staging row to an LLM is wasteful and produces ungrounded matches.

The split is rigid: fuzzy matcher proposes, LLM disposes. Reversing that lets the LLM hallucinate links that the matcher would never have proposed, which is exactly the failure mode we cannot afford.

## The LLM is a producer of scored claims, not a source of truth

When the LLM verifies a candidate, it returns a structured object: the proposed link, a confidence in `[0, 1]`, the specific snippets it relied on, and any flags ("source contradicts itself", "ambiguous parent entity"). That structured object is treated as a *claim*, identical in status to a fuzzy match or a hand-verified match — just with `confidence_method = llm_inferred`.

Specifically:

- LLM output is never written directly to entity tables. It writes to a claims table. Validation reads claims, scores them, and decides what becomes canonical.
- An LLM claim with no supporting snippet is rejected at the boundary. No snippet = no claim.
- The prompt and model version are stamped on every claim (`resolver_version`).
- Re-running the same prompt over the same input must produce the same claim within tolerance. If it does not, that is the bug; do not paper over it with averaging.

## Confidence propagation

A chain's confidence is the **minimum** confidence along the chain, not the product, not the average. If clinic→owner is 0.95 and owner→PE is 0.60, the chain confidence is 0.60. A weak link defines the chain.

The minimum rule is conservative on purpose. It biases toward "we are unsure about Sunshine Autism Center being PE-backed" rather than "we are very sure" — and unsureness is what gets gated by validation, which is exactly where unsureness should be gated.

## Idempotence and re-runs

Resolution must be idempotent for a fixed `(resolver_version, methodology_version, input set)`:

- Running it twice over the same staging snapshot produces the same entity rows and the same scored claims.
- Bumping `resolver_version` produces new rows that supersede the old; the old are kept for audit.
- Bumping `methodology_version` may change what counts as a chain (e.g., minority PE stake counted vs. not). Re-resolution is mandatory after a methodology change; the new run's outputs supersede.

Non-idempotent resolution is a bug, not a quirk. If you cannot reproduce yesterday's dataset from yesterday's inputs, you cannot defend yesterday's claims.

## What resolution does not do

- **Does not validate.** Resolution proposes; validation accepts. A claim with confidence 0.99 from resolution is still a claim until validation says otherwise.
- **Does not fetch.** Resolution reads staging tables only. If it needs a document the ingestion layer did not capture, the ingestion layer is wrong, not resolution.
- **Does not edit staging.** If resolution finds that a staging row is misparsed, it logs the suspicion; the ingestion module gets fixed and re-runs.
- **Does not publish.** Entity tables are internal until they pass through Validate.

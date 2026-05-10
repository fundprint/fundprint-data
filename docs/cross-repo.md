# Cross-repo coordination

**Owns:** how `fundprint-data`, `fundprint-dashboard`, and `fundprint-methodology` stay coherent as they evolve.
**Depends on:** the publication contracts (`publication.md`) and the schema (`schema.md`).
**Consumed by:** anyone making a change that crosses a repo boundary — which is most consequential changes.

Three repos, one product. The split is deliberate: different rates of change, different reviewers, different audiences. The cost is coordination. This doc is the coordination protocol.

## The three repos and their jobs

| Repo                    | Owns                                              | Changes when…                          |
|-------------------------|---------------------------------------------------|----------------------------------------|
| `fundprint-methodology` | Definitions, white paper, sensitivity-reader notes| The world's understanding shifts.      |
| `fundprint-data`        | Pipelines, schema, resolution, exports            | Sources change or quality improves.    |
| `fundprint-dashboard`   | Public UI, search, visualizations                 | The presentation needs to evolve.      |

Roughly: methodology changes once a quarter, data changes weekly, the dashboard changes daily. The coordination protocol is asymmetric to match.

## Direction of truth (repeated, because it matters)

**Methodology defines. Data implements. Dashboard displays.**

When the three disagree:

- If methodology says X but data computes Y, the data repo is wrong and updates next.
- If data publishes Y but the dashboard shows Z, the dashboard is wrong.
- If the dashboard wants to display W but data does not expose it, the dashboard files a request to data — never adds its own derivation.

A dashboard PR that derives a number the published dataset does not contain is a bug, even if the number is correct. That number cannot be reproduced by an outsider, which means it cannot be cited.

## What flows between repos

```
fundprint-methodology ──┬─→ fundprint-data    (definitions, floors, gates)
                        │
                        └─→ fundprint-dashboard (citation links, copy)

fundprint-data ─────────┬─→ fundprint-dashboard (read-only DB views, HF link)
                        │
                        └─→ fundprint-methodology (audit packet per release)

fundprint-dashboard ────→ fundprint-data       (view-change requests, never code)
```

Three two-way relationships, all asymmetric. The downstream repo never writes upstream; it requests.

## Coordinated releases

A release is one event that touches all three repos. The sequence:

1. **Methodology cuts first.** A versioned white paper, sensitivity-reader sign-off, dated changelog. The version (e.g., `methodology_version=2026.07`) is the input the data repo needs.
2. **Data cuts second.** Resolution + validation run against the new methodology version. The Hugging Face dataset is published, tagged with `(schema_version, resolver_version, methodology_version, dataset_version)`.
3. **Dashboard cuts last.** Its views point at the new dataset version, copy is updated, citation links are refreshed.

Out-of-order release is the most common way to publish an inconsistent product. Do not work around the order to ship faster.

## Versioning across repos

Each repo's release tag is independent (semver or date-based, the team chooses), but they refer to each other:

- A `fundprint-data` release pins a specific `fundprint-methodology` commit hash in its release notes.
- A `fundprint-dashboard` release pins a specific `fundprint-data` HF dataset version.
- A `fundprint-methodology` release does not pin downstream — methodology is upstream of everything.

A consumer reading the dashboard can trace: dashboard release → data release → methodology release → the document defining what they are seeing. That trace is the credibility chain.

## When changes cross repos

Three change patterns to be ready for:

**Pattern A — pure data change.** New source onboarded, new state added, more clinics resolved.
- Methodology: no change (unless new source surfaces a definitional question).
- Data: new release.
- Dashboard: re-deploys against the new dataset version.

**Pattern B — definitional change.** "What counts as PE-backed" tightens or loosens.
- Methodology: new release with dated changelog.
- Data: re-resolution mandatory; rows that no longer qualify get superseded; audit packet shows the diff.
- Dashboard: copy may need updating ("includes minority-stake acquisitions: yes/no").
- Press: a definitional change is a *story* — coordinate disclosure. Do not let it look like silent revision.

**Pattern C — UI-only change.** Dashboard adds a chart, refines copy.
- Methodology: no change.
- Data: no change.
- Dashboard: new release.

Pattern A is most common. Pattern B is the dangerous one — it is where credibility is most at risk and where the coordination discipline pays for itself.

## Cross-repo review

Any PR that touches a contract referenced by another repo:

- Tagged with `cross-repo` in the title.
- Linked to a sibling-repo issue or PR (if the change requires sibling action).
- Merged only after the sibling acknowledges or the dependent change is queued.

A `cross-repo` PR merged without coordination is a regression waiting for a Friday-afternoon screenshot.

## What does *not* cross repos

- **Code.** Each repo has its own code. Shared utilities live in a separate package or are duplicated; they do not get imported across repos.
- **Schemas.** The dashboard does not import this repo's SQLAlchemy models. It reads views.
- **Definitions.** The dashboard does not encode "PE-backed" in TypeScript. It reads a flag from the dataset.

Every time these lines blur, the project gets harder to defend. Hold the lines.

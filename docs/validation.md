# Validation (trust and gates)

**Owns:** how trust is assigned to resolved entities and how the dataset earns the right to be published.
**Depends on:** confidence scores from Resolve; methodology-defined floors and gates.
**Consumed by:** the Publish layer, which reads only from validated views.

Resolution proposes claims. Validation decides which claims are good enough that we are willing to put our names on them in front of a journalist, an academic, or a Senate staffer. This layer is the single hardest thing to get right and the one most likely to be skipped under deadline pressure. Skipping it is what makes the project hollow.

## The three trust levels

Every claim in the system sits at one of three trust levels:

| Level             | Meaning                                                       | Where it can go                |
|-------------------|---------------------------------------------------------------|--------------------------------|
| `unverified`      | A scored claim from Resolve that has not been validated.      | Internal only.                 |
| `verified`        | A claim that passed the validation run's confidence floor.    | Dashboard + HF dataset.        |
| `human_anchored`  | A claim spot-checked or hand-validated by a human reviewer.   | Dashboard + HF + cite-worthy.  |

A claim's level is mutable upward (verified → human_anchored) but never downward without a corresponding `validation_run_id` recording the demotion. We never silently degrade trust; we record it.

## The 95% gate

Before any scaling step (1k → 5k → 10k clinics, or onboarding a new state, or shipping a new resolver_version), a 100-row random sample of newly-resolved claims is hand-validated. Accuracy floor: **95%**.

The rules around this gate, copied from the project plan and made operational here:

- The 100 rows are drawn at random from the new claims, stratified by `confidence_method` (so LLM-only and fuzzy-only claims are both represented).
- The reviewer reads each row's source documents and labels it as agree / disagree / unclear.
- The gate passes if `agree / (agree + disagree)` ≥ 0.95. "Unclear" rows are surfaced and counted separately as a *clarity flag* on the methodology, not as failures.
- A failed gate blocks publication of that batch. The fix is to the pipeline, not the sample.
- The audit (sample, labels, sources reviewed) is committed to the repo as a dated record. It does not get rebuilt or rewritten.

This gate is **non-negotiable**. The whole project's credibility — every press citation, every Senate staffer reply, every advisor co-sign — rests on this number being honest.

## Confidence floors

The publishable view is a `SELECT` over verified claims subject to a floor. The floor is defined in the methodology repo, not here. Examples (illustrative — real values live in methodology):

- Clinic existence (the clinic is real, at this address): floor 0.85.
- Clinic → owner_entity link: floor 0.80.
- Owner_entity → parent_pe_firm link: floor 0.85.
- Acquisition date: floor 0.75 (with the caveat that "circa year" is an acceptable claim).

Below the floor, claims stay internal. They are not deleted; they wait for better evidence or improved resolution.

## What gets quarantined

Quarantine is the explicit "we do not know yet" state. A claim is quarantined when:

- Two sources contradict each other and the resolver cannot pick a winner.
- An LLM extraction returned a flag (e.g., "source contradicts itself").
- A hand-validation review labeled the row "unclear."
- A claim has been challenged by an external party (advisor, journalist, the chain itself) and not yet resolved.

Quarantined claims are visible in internal views and tagged in audit logs. They never appear in public exports. The rate of quarantine is itself a metric — a rising quarantine rate is a signal that a source has changed shape or that resolution needs work.

## Audit trail

Every validation run produces:

- A `validation_run_id` and timestamp.
- The exact set of input claim IDs and the resolver_version they came from.
- The methodology_version used for floors.
- The pass/fail decision per claim, with the deciding rule.
- The 100-row sample (if a hand-validation gate ran), with reviewer labels.

The audit table is append-only. A future journalist asking "how did you know this in August?" must be answerable from this table without reconstruction.

## What validation does not do

- **Does not re-resolve.** If a claim is wrong, validation marks it; resolution fixes it.
- **Does not edit claims.** A demotion creates a new validation event; the original claim is not rewritten.
- **Does not gate on volume.** "We need 5,000 clinics for the launch metric" is never a reason to relax a floor. The number on the dashboard is meaningful or it is not the dashboard.
- **Does not handle takedowns.** Legal takedowns and corrections to public claims have their own process documented in the methodology repo.

## The "able to defend it" test, applied

For any row in the validated view, you must be able to:

1. Explain in 30 seconds what the row claims.
2. Name the hardest decision in resolving it.
3. Identify the weakest link in its provenance chain.
4. Answer one follow-up question you did not anticipate.

If you cannot do this for a randomly selected row, the validation gate has been too loose. Tighten it before the next release.

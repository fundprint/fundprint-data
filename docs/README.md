# docs/ — the Fundprint data framework

This folder describes **how parts of the data system communicate**, not what code currently exists. It is the contract layer. Implementations come and go; these documents define what any implementation must respect.

If a doc here disagrees with the code, the doc is the intent. Either the code is wrong, or the doc is stale — either way, fix it before shipping the next change.

## Reading order

If you are new to the project, read in this order:

1. **architecture.md** — the five layers of the system and how they hand off.
2. **schema.md** — the data shapes that flow between layers.
3. **ingestion.md** — how external reality enters the system.
4. **resolution.md** — how raw rows become resolved entities.
5. **validation.md** — how trust is assigned and gated.
6. **publication.md** — how the dataset reaches the outside world.
7. **cross-repo.md** — how this repo coordinates with `fundprint-dashboard` and `fundprint-methodology`.

Each doc opens with: *what it owns*, *what it depends on*, *what consumes it*. That triple is the contract.

## What these docs are not

- They are **not** code documentation. Function-level docs live next to the code.
- They are **not** a tutorial. They assume basic familiarity with the project's goals and tech stack.
- They are **not** the methodology. "What counts as PE-backed" lives in `fundprint-methodology`. These docs describe how *whatever the methodology says* gets implemented.

## When to update these docs

Update a doc **before** changing the contract it describes. Sequence:

1. Open a draft change to the relevant doc.
2. Get the change approved (the methodology repo may need to update too).
3. Implement code against the new contract.
4. Merge the doc and code together.

Never update the doc to "match what the code now does" after the fact. That inverts the contract.

## A note on framing

Every doc here treats the data pipeline as a system of **producers and consumers** separated by **trust boundaries**. At each boundary, three questions must always have an answer:

- What can the consumer assume the producer guarantees?
- What does the consumer have to verify itself?
- What is logged so a third party can audit the boundary?

If you cannot answer those three for a code change you are about to make, you are about to break the system.

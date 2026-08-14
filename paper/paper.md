# Deal-based censuses undercount private equity ownership of autism therapy: a reproducible, site-level national estimate

**Atharva Doke**

*Draft. Dataset version `2026.07-beta`, resolver version `0.1.0`, methodology
version `2026.07-statefile-v1`.*

---

## Abstract

*To be written last, after Results and Validation are final. Per the impact plan,
the abstract leads with the registry-only figure of 580 rather than the
all-sources figure of 1,621, because a finding deliberately made smaller is a
finding people believe.*

---

## 1. Background

*To be drafted: private equity in ABA; the growth of Medicaid autism spending;
the CARD bankruptcy as the anchor case; why facility-level ownership is invisible
to the parties who regulate it; the January 2026 peer-reviewed estimate and the
April 2026 advocacy report, and what each can and cannot see.*

---

## 2. Data

Fundprint draws on four source families. Every record in every family is fetched
over HTTP, stored verbatim as a content-addressed blob, and linked to the claims
it supports before any claim is eligible for publication. A row whose source
document cannot be retrieved and hashed does not enter the dataset, regardless of
how plausible the underlying fact is. This rule has real cost: two ownership
claims that are well documented in secondary reporting remain unpublished because
their primary sources return HTTP 403 to every client attempted, and one state
Medicaid audit is published with its reasoning and without its dollar figures for
the same reason.

### 2.1 The federal provider registry

The backbone is the National Plan and Provider Enumeration System (NPPES), the
CMS registry that assigns a National Provider Identifier (NPI) to every provider
that bills federal health programs. Fundprint uses the **monthly bulk
dissemination archive**, not the NPPES API, and the distinction is consequential
rather than a matter of convenience.

The API caps a query at 1,200 records and returns only an NPI's *primary*
practice address. A chain that registers one NPI and attaches thirty locations to
it as secondary addresses is therefore visible to the API as one address. The
bulk archive ships a separate Practice Location Reference File (`pl_pfile`)
containing exactly those non-primary locations, which is where a multi-site
operator's other centers actually live. Any census built from the API, or from a
taxonomy query against it, is structurally blind to this file. So is any census
built from company websites alone, for the opposite reason: a website lists what
the company chooses to advertise today.

The archive used for this release is approximately 1.1 GB compressed, expanding
to an 11.5 GB CSV. It is streamed out of the zip rather than extracted, and only
the filtered ABA extract, a few megabytes, is retained as a snapshot alongside
the upstream URL and the SHA-256 of the archive itself:

```
archive_sha256 = e73068bbafb9ed72bb9303b36a1091ddce66f6e510a99fa1ca3339a04954cfea
```

Publishing that hash is what makes the registry-based results in Section 4
reproducible by a reader with no access to anything proprietary. The archive is
free, requires no login, and is not governed by a data use agreement.

A provider organization enters the ABA universe if it holds a live,
non-deactivated organization NPI and either carries a behavior-analysis taxonomy
or bears the name of a tracked ABA brand. The second clause is necessary because
several genuine ABA chains register under no ABA taxonomy at all. Omitting them
would drop tracked clinics out of the denominator while leaving them in the
numerator, inflating the share.

### 2.2 Owner location directories

The second family is the operator's own published list of centers. Where a chain
publishes a machine-readable directory, Fundprint reads each center's address
from the page's structured data: a schema.org `MedicalBusiness` block, a
WordPress REST collection, a store-locator endpoint, or the site's semantic
per-field address markup. This is a read of published structured data rather than
brittle scraping of rendered prose.

This family is the single largest methodological difference between this work and
a deal-based or registry-based census, and it accounts for the majority of the
published dataset: **1,573 of 1,755 clinics are attested by an owner's own
current directory or roster; 182 rest on the registry alone.**

A directory must be read at the leaf page. An earlier release recorded Autism
Learning Partners as operating a single clinic because its state and county index
pages carry no street addresses. The 59 pages beneath those indexes each publish
one address. The operator is now recorded at 45 clinics. "This directory has no
addresses" is a claim about the page that was actually opened, and the page to
open is the last one.

Directory records are self-reported by the owner, which is a weakness for some
questions and a strength for the one that matters most here: whether a center is
open now. A registration is filed once and essentially never revoked. A directory
is what the company tells prospective clients today.

### 2.3 Owner rosters

Some operators expose their entire center list through a single structured
endpoint, typically a WordPress custom post type or the WP Store Locator plugin's
`store_search` action with `autoload=1`. One request then returns every center
with a clean address and, in some cases, the sub-brand. This is treated as a
distinct source family because attribution is free: a roster published by the
owner is the owner asserting the center is theirs, so no name matching is
required and rows stage under the owner's own name.

### 2.4 Curated ownership facts

Ownership links themselves come from hand-verified primary sources: an
acquisition announcement, a portfolio page, or reputable trade-press reporting
that explicitly states *current* ownership. Each such fact fetches and
content-hashes its own source URL. Historical ownership that has ended is
recorded as a dated event with its own source and displayed on a timeline, never
as a live ownership link.

Two acquisition tools are worth noting as negative results. A Bureau of Labor
listing source and an SEC EDGAR acquirer were both built and are both dormant:
the first because its content is rendered client-side, the second because it
stages to a table no resolver reads. They are documented here so that a reader
does not assume the pipeline draws on filings it does not draw on.

### 2.5 Sources considered and rejected

**State Medicaid provider files** were researched and rejected. Only one state
publishes a usable bulk file, and its `robots.txt` disallows all automated
access. More importantly, a Medicaid-based denominator would bias *against* the
paper's own finding rather than for it: private-equity-backed chains skew
commercial, and the largest operator in this dataset does not accept Medicaid in
its home state at all. Building fifty state scrapers would have produced a
denominator that understates private-equity presence.

**TLS-level access control** shapes what is fetchable in a way worth recording.
Several sources classify clients by TLS handshake fingerprint rather than by
user agent, returning HTTP 403 to a Python HTTP client and HTTP 200 to `curl`
presenting an identical user-agent string. The fetch layer retries such blocks
through `curl` while continuing to present Fundprint's real user agent and
contact address, and continuing to honor `robots.txt`. No browser is
impersonated, and sources that block all clients are recorded as blocked rather
than worked around.

---

## 3. Method

### 3.1 The unit of analysis is a physical site

The central methodological commitment is that **a clinic is a physical service
location**: not an NPI, not an office, not a brand. A site is identified by

```
site_key = (owner_entity, normalize_street(address_line1), zip5)
```

with a fallback to `(owner_entity, city, state)` for directory rows that publish
no street.

**Keying on the NPI instead would fail in both directions simultaneously**, which
is why no NPI-based de-duplication appears anywhere in the pipeline. Several NPIs
can share one address: one operator holds six NPIs at a single Broomfield,
Colorado suite under two legal-entity name variants, and an early pass that
counted registrations reported 184 rows for 71 real sites, inflating that
operator roughly threefold. And one NPI can hold many addresses, which is
precisely how chains register secondary centers in `pl_pfile`. A count of
registrations is therefore not merely noisy; it is biased unevenly across
operators, which makes per-firm counts non-comparable.

### 3.2 Address canonicalization, and why it is not fuzzy matching

`normalize_street` canonicalizes USPS street suffixes, directionals and unit
markers, so that `AVE` and `Avenue`, and `STE 101`, `Suite 101` and `#101`, all
key alike. Before this existed, the site key stripped punctuation only, so the
registry's `18301 N 79TH AVE, BUILDING A STE 101` and the owner directory's
`18301 N 79th Avenue, Building A, Suite 101` were two clinics. **Forty-four real
sites were double-counted this way** across six operators, once per source.

Two properties of this transform are load-bearing:

1. **It is a canonicalization, never a fuzzy match.** Every pair it folds together
   is an abbreviation and its own expansion. A looser rule, in particular one that
   dropped the unit designator, would merge two genuinely distinct clinics in one
   office park, which is the exact failure the site key exists to prevent.
2. **It applies to addresses only.** A separate and weaker `normalize` handles
   brand names, where collapsing "st" into "street" would be meaningless and
   risky. Using the address normalizer on names, or the name normalizer on
   addresses, are both errors that have occurred and both produce wrong counts.

A related rule governs address *parsing*: **a wrong street is worse than no
street**, because a wrong street produces a wrong site key, which stops the same
building matching itself across two sources and counts it twice. An address with
no comma before the city, such as `7041 Transit Rd East Amherst New York 14051`,
cannot be split by rule, since peeling trailing words carries the street name into
the city and leaves the street as a bare house number. Where a source names the
locality separately, the parser subtracts the known locality rather than guessing.
Where it does not, the parser refuses the row. Relatedly, a directory entry is not
always an address at all: one operator lists a city with the text "In-home services
available now" in the street position, so a street is required to carry a house
number.

### 3.3 One suite is one clinic, across brands and across sources

A financial sponsor that acquires two chains and merges them leaves both
registrations standing at the same address. Three such cases appear in this
release, covering sites in Florida, Wisconsin and Pennsylvania, and **23 sites
were double-counted** before they were collapsed. Where a collapse is required,
the surviving row is chosen on evidence: an owner's own directory beats a
registry record, and a fresher registration beats a staler one.

The reasoning here is worth stating explicitly, because an earlier version of
this work disclosed the problem as a residual limitation rather than correcting
it, on the theory that merging two separately registered legal entities was a
stronger claim than the evidence supported. That is backwards. **Declining to
merge is not the neutral option.** It asserts that two competing centers operate
in one suite, which is the claim the evidence contradicts. When the choice is
between two positive claims, "publish less" means the one that counts fewer
clinics, not the one that requires less thought.

A second, subtler collapse operates within a single owner family. When one
sponsor's five brands share one directory, the registry has often filed the same
suites without a unit designator, so a naive merge of directory and registry rows
mints a second clinic per suite spelling. Corroboration is therefore performed at
*building* level while identity remains at *suite* level, and net-new buildings
alone are staged. The correction is directory-count-aware: a building the
directory lists once collapses to one clinic, while a building the directory
lists twice, such as an address in Dublin, Ohio holding two of the family's
brands in different suites, remains two. The rule can therefore never merge two
real suites.

### 3.4 The owner's own directory overrides the registry, in both directions

Where an operator publishes a *complete* directory of its centers, and the
registry disagrees about whether a center exists, the directory wins. This is the
largest single lever in the dataset, and its effect is bidirectional.

The clearest case is the largest operator tracked. Its own directory lists 414
centers; the registry carried roughly half that many rows, and was wrong in both
directions at once. It **missed most of Texas**, where the operator lists 227
centers and the registry carried 15, because a chain does not separately register
every center it opens. And it **invented centers that do not exist**: nine in
Ohio, three in Virginia and one in Georgia, all states the operator does not
serve; an apartment; the operator's own corporate headquarters, filed as a
practice location on the very NPI carrying its 109 real centers as secondary
locations; and 34 stale Colorado registrations, where the registry gave 67
against the operator's own 42. After correction the operator publishes at **412
centers**, two short of its own directory, the difference being rows the address
parser refused rather than guessed at.

> **TODO (open, needs the user's call).** The project's internal documents
> disagree on two intermediate figures for this operator: the pre-correction
> registry row count is recorded as 206 in `METHODOLOGY.md` and 212 in
> `CLAUDE.md`, and the count of registry rows the directory does not list is
> recorded as 105 in the former and roughly 50 in the latter. The final figure of
> 412 is confirmed against the published snapshot. The passage above is written
> to depend only on confirmed numbers until the intermediates are settled.

Applied across ten operators, the rule corrects counts in both directions:
212 to 412, 145 to 101, 90 to 70, 1 to 45, 54 to 53, 24 to 12, 18 to 64, 37 to
74, 41 to 54, and 29 to 28. **Every one of them was wrong in both directions.**

The rule fixes three distinct faults with no special-casing for any of them:
closed centers that were never deregistered, head offices filed as practice
locations, and registrations belonging to entirely different companies whose
names merely share a prefix with a tracked brand. On the third fault, a
name-prefix match cannot distinguish a tracked chain from an unrelated counseling
practice, a home-care company, or a chiropractor sharing the first two words of
its name. Before this rule, such businesses were being published as
private-equity-owned autism clinics.

Two constraints keep the rule honest. It runs **only where the directory is
complete**, since a regional directory would quarantine real clinics elsewhere,
and completeness is checked rather than assumed. And it **quarantines rather than
deletes**: the registration is real, and what is false is the inference that a
registration is a center. A center that genuinely exists survives via its
directory row.

### 3.5 Scope rules

Three scope tests exclude records that are real but are not ABA therapy clinics.

**In-home providers operate no clinics.** An in-home operator still holds NPIs
and must register them somewhere, so the registry returns an address that looks
like a clinic and is not one: one operator registers exactly one office suite per
state across fourteen states, and another registers at apartments. Such an
operator is published with an ownership record, a clinic count of zero, and an
explicit label. Deleting it would understate private-equity presence in ABA, so
the ownership is retained even though the clinic count is not.

**A multi-service provider is in scope only when ABA is a core line.** The test
is the registrations, not the company's self-description. One pediatric therapy
provider carries a behavior-analysis taxonomy at 22 of 24 sites and is included;
one behavioral-health company has one primary-ABA identifier out of 65 and is
excluded. The test cuts one way only: it can exclude a multi-service company, and
it can never include a chain that would otherwise be out of scope.

**Non-therapy sites are excluded.** One operator's stand-alone diagnostic and
evaluation clinics are held out, because this dataset counts therapy centers.
Another operator's "coming soon" listings are excluded, because a leased address
is not an operating clinic.

Head-office exclusions are made **only on direct evidence**, never on a
heuristic, and only two appear in this release, each because the owner's own
directory omits the address. A tempting rule, that many NPIs at one address
indicates a head office, was tested against the data and found false: the largest
tracked operator registers three to four NPIs at every one of its 35 Colorado
centers. Applying that rule would have deleted dozens of real clinics. The
negative result is recorded because the rule is intuitive enough to be
reinvented.

### 3.6 The ownership firewall

The owner table is populated by scraping sponsors' portfolio pages, so it
contains every company a tracked sponsor owns, including optometry, dental and
restaurant chains. These were harmless against a taxonomy-filtered API query and
are not harmless against the bulk registry, where they match: an early unguarded
pass attributed roughly 5,000 optometry, dental and fast-food locations to
private-equity firms as autism clinics.

An explicit `is_aba` flag, defaulting to false, is therefore the firewall: only
owners marked as ABA operators can capture clinics. A second flag,
`directory_only`, marks brands whose names are too generic to be safely
name-matched against the registry at all; these are linkable from their own
rosters and are never used to match registry records.

A related constraint governs naming. The registry carries an operator's **legal**
name, which is frequently not its consumer brand: one operator registers under
its search-fund holding company's name, another under a vocational-services
entity, a third under a regional legal name it no longer trades as. The
name-prefix linker must match what the registry carries, so `owner_entity.name`
holds the legal name and a separate `trade_name` holds the brand. Published views
display `COALESCE(trade_name, name)`. Renaming the legal field to the friendly
name breaks the linker silently, and has done so.

### 3.7 Validation, floors and quarantine

Resolution proposes; validation disposes. Every claim carries a confidence score
and a method tag recording how the link was established (`human_verified`,
`exact_match`, `llm_inferred`, or `fuzzy_high`). A chain's confidence is the
**minimum** along the chain, never the product or the average, so a weak link
defines the chain.

Publication requires clearing a per-claim-type floor defined by the methodology
version: 0.85 for clinic existence, 0.80 for clinic-to-owner, 0.85 for
owner-to-parent, and 0.75 for a dated acquisition event. A claim below its floor
is retained internally and waits for better evidence; it is never deleted.

**Corrections are quarantines, never deletions.** A correction opens a validation
run and writes a `quarantined` decision per affected claim. The claim stays on the
record and leaves the published views. Every validation run produces an
append-only audit record: run identifier, timestamp, input claim identifiers, the
resolver and methodology versions in force, and the deciding rule per claim. A
reader asking "how did you know this at the time" must be answerable from that
record without reconstruction.

It is worth recording that although a language-model resolution path exists in
the codebase, **it is dormant: zero published claims in this release were
established by it.** Everything published here is deterministic.

### 3.8 Denominator and share

The denominator is computed from the same registry archive as the numerator, in
**one pass over one universe with one site key**, so the numerator is a strict
subset of the denominator by construction.

An earlier implementation built the numerator by name-matching the archive
directly rather than reading the published dataset. It therefore saw none of the
corrections above: it counted centers that had just been quarantined as closed,
and it counted the name-collision companies of Section 3.4. One state was
published at 7.6% private-equity penetration on the strength of eight centers the
named operator does not run. The numerator now reads the published clinic table
and intersects it with the registry universe, so a single correction moves the
map, the operator tables and the national share together.

Three further properties of this computation matter:

**Distinct sites are counted as a union, never as a sum of per-operator sizes.**
An address carrying two brands or two legal entities would otherwise count twice
on both sides, making the numerator a multiset rather than a subset. This is not
a corner case: roughly 2,000 addresses in the registry are shared between legal
entities. The visible consequence is that the operator-size distribution in
Section 4, being per-operator, sums to 21,756 site-slots against 21,088 distinct
sites, a difference of 668 shared addresses.

**The visibility test operates at building level while identity remains at suite
level.** The registry frequently records `551 36th St SE Unit 2` where the owner
writes `551 36th St. SE`. Requiring an exact site-key match to consider a
published clinic registry-visible dropped 94 real sites, 82 of them
private-equity-owned. The test therefore peels unit markers, and resolves to the
*archive's* key rather than Fundprint's, so the numerator remains a strict
subset. A building that is ambiguous in the archive, holding several suites, is
skipped rather than guessed. This relaxation applies to the visibility test only.
It does not relax `site_key`, under which two suites in one office park remain two
clinics.

**The published share is a floor and is labelled one.** Closed centers are removed
from the numerator, because the operators' own directories can be read, and cannot
be removed from the denominator, because the other seventeen thousand operators'
directories cannot. The bias runs against the finding. It is disclosed rather than
adjusted away, since adjusting it would mean guessing.

### 3.9 What is deliberately not computed

An earlier release headlined private equity's share of clinics run by operators
with five or more locations. It was withdrawn, and it is not reinstated here,
for one disqualifying reason and two supporting ones.

**The denominator was endogenous.** An operator is a "chain" *because* it has many
sites, and it has many sites *because* private equity rolled it up. Private
equity's own buying inflated numerator and denominator together. A sponsor that
acquired forty four-site operators and merged them into one forty-site chain would
barely move its share of chain-run clinics while its actual market power grew
enormously: it manufactures its own denominator. The measure was partly blind to
the thing it existed to measure. Supporting the same conclusion, the five-site
cutoff was arbitrary and undefended, and no other published work uses the measure,
so it could not be checked against anything.

The general form of the trap is worth naming, because it is easy to re-derive
under a new name: **any share of "the part of the market private equity could
plausibly buy" has this same defect**, because private equity defines that part.

What replaces it requires no threshold and no editorial choice: an absolute count,
a share of the full registry universe, per-state shares, and the operator-size
distribution published in full so that any cut is the reader's and is stated out
loud.

### 3.10 Confidence as four questions

A single confidence label conflates questions that fail independently, so each
published clinic carries four graded dimensions rather than one: whether it is
**open**, whether the **address** is right, whether it is a center rather than
in-home care (**site type**), and who **owns** it. An overall grade is the weakest
of the four. All four are derived from published fields alone, source types,
registry freshness, service model and ownership-claim provenance, so a reader can
reproduce the grade rather than trust it.

---

## 4. Results

*To be drafted. Inputs are final and stated here for continuity: 1,755 clinics
across 19 owner entities and 42 states, of which 1,621 are private-equity-owned
and 134 are held by a pension fund, a family office and two search funds;
1,573 owner-verified and 182 registry-only; a registry universe of 17,569 ABA
organizations operating 21,088 sites; 682 tracked sites (3.2%) and 580
private-equity sites (2.8%) on a registry basis; the operator-size distribution;
the per-state concentration table; and the reconciliation against both published
estimates.*

---

## 5. Validation

*Blocked on the hand-verification sample now in progress: 150 published clinics
drawn under seed 913063207, stratified 110 private-equity / 20 non-private-equity
/ 20 unclaimed and 87 directory / 63 registry. The unclaimed stratum is the only
one capable of detecting a false negative. Reporting will be accuracy with a 95%
Wilson interval broken out by source type, plus the ghost rate. Rows the reviewer
could not verify count against the rate rather than being discarded, so the
published figure is a conservative floor.*

---

## 6. Limitations

*To be drafted, and per the impact plan it leads rather than trails. The
registry reports existence-ever, not existence-now. The as-of dates of the
compared estimates differ by 19 months, so part of the difference is real growth
and cannot be separated without the comparison study's unpublished site list.
Coverage is 21 of 32 known private-equity-backed platforms, and the 11 uncovered
platforms hold roughly 504 facilities by an outside count, which makes the total a
floor for a second and independent reason. The withdrawn chain-run share of
Section 3.9 belongs here too.*

---

## 7. Reproducibility

*To be drafted: the three public repositories, the published archive SHA-256, the
versioned methodology, the Zenodo dataset DOI once minted, and the command
sequence that regenerates every published number from the archive.*

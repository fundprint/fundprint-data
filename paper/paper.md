# Deal-based censuses undercount private equity ownership of autism therapy: a reproducible, site-level national estimate

| **Atharva Doke**
| Independent researcher
| Correspondence: atharva.doke737@gmail.com

*Preprint, October 2026. Dataset version 2026.07-beta, resolver version 0.1.0, methodology
version 2026.10-verification-v1.*

---

## Abstract

**Background.** The first peer-reviewed national estimate of private equity (PE)
ownership of autism therapy sites in the United States counts 574 sites in 42
states as of December 2024, built from 147 acquisitions identified in a
proprietary deal database. Deal-based censuses see a company at the moment it was
bought. They cannot see the centres it opened afterwards.

**Methods.** We built a reproducible, site-level census of institutionally owned
applied behavior analysis (ABA) clinics from four public source families: the
federal NPPES bulk registry, operators' own published location directories and
rosters, and hand-verified ownership facts. Every source document is fetched and
content-hashed. A clinic is a physical site, keyed on canonicalized street and
ZIP within an owner, never on a provider identifier. Where an operator publishes
a complete directory it overrides the registry in both directions.

**Results.** Restricted to what the federal registry can see, we find 580
PE-owned sites, six from the published estimate, by a method sharing none of its
data. Adding operators' own directories, we find 1,621 PE-owned clinics in the
same 42 states, 2.8 times the deal-based count. A second outside estimate,
counted by platform rather than by deal, agrees with ours to within 8% over the
16 platforms both cover (1,521 against 1,642). PE holds 2.8% of the 21,088 ABA
sites in the registry nationally, a floor, but 15.8% in Minnesota, 13.2% in New
Mexico and 9.5% in Arizona. The registry's blind spot varies by operator from
none at all to complete: one 18-centre operator is entirely invisible to it. A
hand check of a seeded random sample found 66 of 67 PE clinics correct on site,
address, brand and owner (98.5%, 95% CI 92.0% to 99.7%).

**Conclusions.** Censuses of PE ownership sort by unit rather than by rigor.
Counts built from deals or registrations see a company frozen at acquisition;
counts built from platforms or directories see it today, and the difference is a
factor of nearly three. Neither a deal list nor the provider registry is a
sufficient basis for oversight of a sector whose operators keep growing after they
are bought.

---

## 1. Background

Applied behavior analysis (ABA) therapy for autistic children has, over the past
decade, become an active area of private equity investment: 117 of the 147 PE
acquisitions of autism service providers identified by the first national study fell between 2018 and 2022
[5]. The pattern is the familiar roll-up: a sponsor buys a regional operator
as a platform, adds smaller practices to it, and opens new centres under the
platform brand. The largest operator in this dataset publishes 414 centres. The
case most often cited when this model is questioned is the Center for Autism and
Related Disorders, then the largest US autism therapy chain, which Blackstone
acquired around 2018. It filed for Chapter 11 in 2023 having closed more than 100
locations [7], and the bankruptcy court approved its sale in pieces later that
year [8].

The question has a public-finance dimension because much of this care is paid
for by Medicaid. The Department of Health and Human Services Office of Inspector
General (OIG) has audited state Medicaid ABA programs in a continuing series,
and each published audit has found improper payments at scale: at least $77.8
million in Colorado (2022-2023) [1], $56 million in Indiana (2019-2020) [2],
$18.5 million confirmed and a further $94.3 million potentially improper in
Wisconsin (2021-2022) [3], and $45.6 million in Maine (2019-2023) [4]. Spending
grew fast over the audited periods: Colorado's from $60.1 million in 2019 to
$163.5 million in 2023, Maine's from $52.2 million to $80.6 million over the same
years. None of these audits attributes a dollar to any company or type of owner.
They audit state programs, and the federal provider registry that they and most
other analyses rely on records the entity that bills, not the firm that owns it.

Two recent studies have measured PE ownership in this sector directly. Arnold
and colleagues, in *JAMA Pediatrics* in January 2026 [5], identified 147 PE
acquisitions of autism service providers between 2015 and 2024 in the PitchBook
database, verified and expanded them from press releases and company websites,
and counted 574 PE-owned service delivery sites across 42 states as of 31
December 2024. The authors name two limitations themselves: they cannot report
PE's share of all autism service sites, and they are likely undercounting. In
April 2026 the Private Equity Stakeholder Project (PESP) published a report on PE
in ABA and Medicaid [6] whose appendix lists 34 PE-backed ABA platforms with a
facility count for each, drawn from PitchBook and LevinPro. Its leading
recommendation is greater ownership transparency and facility-level oversight.

Both studies rest on proprietary deal databases, and neither publishes its site
list, so neither can be reproduced, updated or checked line by line. This paper
offers a census built entirely from public, fetchable sources, at the level of
the physical site, with every source document content-hashed and every number
regenerable from a published archive. It addresses both limitations the JAMA
authors name: Section 4.3 reports PE's share of all ABA sites in the registry,
and Sections 4.5 and 4.6 measure the undercount and locate where it comes from.

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
published dataset: **1,575 of 1,757 clinics are attested by an owner's own
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
Where it does not, the parser refuses the row. The same rule applies to markup: a
directory card that writes `421 Windchime Place<br> Colorado Springs, CO` marks the
end of the street with the line break, and a parser that flattens the card to text
before splitting it loses that boundary. One parser did exactly this, misfiling
eight of one operator's 30 centres, and the garbled addresses then caused the
directory rule of Section 3.4 to retire five correctly addressed registry rows. The
hand verification of Section 5 caught it; the parser now splits on the markup and
refuses a card it cannot split that way. Relatedly, a directory entry is not
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
directions at once. The registry carried 206 live rows for the operator, and 105
of them are addresses the operator's directory does not list. It **missed most of
Texas**, where the operator lists 227
centers and the registry carried 15, because a chain does not separately register
every center it opens. And it **invented centers that do not exist**: nine in
Ohio, three in Virginia and one in Georgia, all states the operator does not
serve; an apartment; the operator's own corporate headquarters, filed as a
practice location on the very NPI carrying its 109 real centers as secondary
locations; and 34 stale Colorado registrations, where the registry gave 67
against the operator's own 42. After correction the operator publishes at **412
centers**, two short of its own directory, the difference being rows the address
parser refused rather than guessed at.

Applied across ten operators, the rule corrects counts in both directions:
212 to 412, 145 to 101, 90 to 70, 1 to 45, 54 to 53, 24 to 12, 18 to 64, 37 to
74, 41 to 54, and 29 to 30. **Every one of them was wrong in both directions.**

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

### 4.1 The count

The published dataset holds **1,757 ABA clinics in 42 states owned by
institutional financial owners. Private equity owns 1,621 of them.** The other 136
are held by a pension fund (70), a family office (30) and two search-fund owners
(36). They are published under their own labels and never folded into the PE
figure, which is the like-for-like quantity for every comparison below.

A count needs no denominator, which is why it is the headline: nobody can hand it
a wrong one. It is nonetheless a floor, because 11 known PE-backed platforms,
holding 504 facilities by an outside count, have not yet been read at all
(Section 6). The one error that runs the other way, a closed centre the registry
still lists, is confined to the registry-only clinics and is graded below.

Of the 1,757 clinics, **1,575 are attested by the owner's own current directory or
roster**; 182 rest on the federal registry alone. Each registry-only clinic is
graded by the age of its registration record: 117 were re-certified within three
years, 35 between three and six years ago, and 30 six or more years ago. That last
group, 1.7% of the dataset, is where a closed centre is most likely to remain
published.

### 4.2 Concentration among sponsors

Fifteen PE sponsors own the 1,621 PE clinics, and ownership is concentrated. One
sponsor's single platform accounts for a quarter of the total, and the three
largest sponsors hold 47%.

| Sponsor                  | Clinics | Principal platform(s)                              |
|--------------------------|--------:|----------------------------------------------------|
| Charlesbank              | 412     | Action Behavior Centers                            |
| KKR                      | 197     | BlueSprig family (four brands)                     |
| Gryphon Investors        | 151     | LEARN Behavioral (federation of named brands)      |
| Tenex Capital Management | 139     | Behavioral Innovations, Monarch                    |
| Nautic Partners          | 128     | Proud Moments                                      |
| Arsenal Capital Partners | 101     | Hopebridge                                         |
| GTCR                     | 79      | Caravel Autism Health                              |
| General Atlantic         | 75      | ACES                                               |
| Petra Capital Partners   | 74      | ABS Kids                                           |
| Six further sponsors     | 265     |                                                    |

### 4.3 Share of the market, and why it is a floor

The registry archive lists **17,569 ABA organizations operating 21,088 distinct
sites**. Restricted to sites the registry can see, so that numerator and
denominator come from one source under one site key, Fundprint can name the owner
of 686 (3.3%), and **580 (2.8%) are PE-owned**. Clinics known only from an owner's
directory are excluded from both sides of this share, because the registry cannot
see them; that is why the share's numerator is far smaller than the count.

The national figure is small because the profession is overwhelmingly
independent:

| Sites per operator | Operators | Sites  |
|--------------------|----------:|-------:|
| 1                  | 15,142    | 13,940 |
| 2 to 4             | 2,145     | 4,867  |
| 5 to 9             | 192       | 1,142  |
| 10 to 24           | 76        | 1,033  |
| 25 or more         | 14        | 774    |

Sites are counted distinctly within each class; across classes they sum to
21,756 against 21,088 distinct sites, because 668 addresses are shared between
operators in different classes (Section 3.8). The distribution is published in
full so that a reader who wants a "share of chains" draws the line themselves and
says where; Section 3.9 explains why this paper does not.

### 4.4 Concentration is local

Care is bought locally, so the national share understates what a family sees.
Among the 50 jurisdictions with at least 25 ABA sites in the registry, PE's share
of sites is highest in:

| State | PE sites | ABA sites | PE share |
|-------|---------:|----------:|---------:|
| MN    | 22       | 139       | 15.8%    |
| NM    | 14       | 106       | 13.2%    |
| AZ    | 50       | 529       | 9.5%     |
| CO    | 54       | 688       | 7.8%     |
| PA    | 33       | 431       | 7.7%     |
| WY    | 2        | 27        | 7.4%     |
| ID    | 4        | 58        | 6.9%     |
| MD    | 30       | 464       | 6.5%     |
| GA    | 42       | 792       | 5.3%     |
| IL    | 30       | 562       | 5.3%     |

These are registry-basis shares and so are floors on the same terms as the
national figure. Wyoming and Idaho rest on small counts and are shown for
completeness rather than emphasis.

### 4.5 Reconciliation with the deal-based estimate

The JAMA estimate counts 574 PE-owned sites as of December 2024; this dataset
counts 1,621 as of October 2026, 2.8 times as many. Asserting that one figure is
right would be unhelpful. The difference can instead be decomposed, and the order
of the decomposition matters.

**First, the footprint agrees.** Both studies find PE-owned sites in exactly 42
states. The disagreement is about depth, not about where PE operates.

**Second, restricted to the registry, the two counts converge.** Of the 1,621 PE
clinics, 580 are visible in the federal registry: six sites from the deal-based
574, by two methods sharing no data source. That convergence is what makes the
remainder interpretable rather than a standoff between two unreproducible
numbers.

**Third, the remaining 1,041 clinics come from a source neither the deal list nor
the registry consults: the operator's own directory.** They are centres the
operator says it runs today, at addresses it publishes, that no registration
records.

**Fourth, the registry's blind spot is not a constant.** Measured operator by
operator, the ratio of published centres to registry-visible centres runs from
1.0 (several operators register every centre) to 21.3 (one operator publishes 64
centres, of which the registry sees 3), and is unbounded for one: an 18-centre
Wisconsin operator has no registry presence at all. No single multiplier can
correct a registry-based or deal-based count, because the correction differs by
operator by more than an order of magnitude.

The JAMA letter reports its five largest states, which allows a partial
comparison:

| State | JAMA (Dec 2024) | Fundprint PE (Oct 2026) | Ratio |
|-------|----------------:|------------------------:|------:|
| CA    | 97              | 148                     | 1.5   |
| TX    | 81              | 379                     | 4.7   |
| CO    | 38              | 67                      | 1.8   |
| IL    | 36              | 52                      | 1.4   |
| FL    | 36              | 80                      | 2.2   |

The ratio is largest in Texas, where the largest operator in the dataset lists
227 centres of its own, of which the registry carried 15. Part of every ratio is real growth over
the 19 months between the two counts, and that part cannot be separated without
the JAMA study's site list, which is unpublished (Section 6).

### 4.6 A second estimate, in a different unit

One ratio against one estimate is the weakest form of this argument: it cannot
distinguish a deal list that undercounts from a pipeline that inflates. The PESP
appendix provides a test, because it counts in a third unit, facilities per
platform, from a source that is current rather than frozen at the deal.

Over the 16 platforms both sources measure, **PESP counts 1,521 facilities and
this dataset 1,642, a ratio of 1.08**. The differences run in both directions:
this dataset is higher on 10 platforms, lower on 4, and exact on 2 (70 and 70;
64 and 64). The comparison includes three platforms whose owners are not PE (a
pension fund, a family office and a search fund), which is why its total exceeds
the PE-only 1,621. A seventeenth platform, an in-home provider PESP lists with 22
facilities, is excluded as a scope rule rather than a disagreement: this dataset
counts no clinics for an operator that runs none.

The 1.08 is a precision check over shared platforms, not a national total, and
must not be read as deflating the 2.8. Nationally the three figures are: the deal
list, 574; this dataset, 1,621; and PESP's appendix summed over all 32 in-scope
platforms, 2,047. **The two outside estimates differ from each other by more than
either differs from this one, and this dataset sits between them, nearer the
platform count.** It sits below PESP chiefly because it has not yet read the
platforms that make up the difference (Section 6).

The independence of this check is partial and is stated as such. This dataset
adopted PESP's appendix as its coverage denominator, so the list of platforms is
shared; the counts are not, since PESP's come from commercial databases and
these from directories and the registry. The units also differ at the margin:
PESP counts centres "open or opening soon", this dataset counts operating sites.

**The pattern across all four counts is the finding.** Deal-based (574) and
registry-based (580) counts both see a company as it was when it was bought or
registered. Platform-based (1,521) and directory-based (1,642) counts both see it
today. A chain bought with 40 centres that now runs 400 is counted at 40 by the
first kind and 400 by the second. The censuses sort by unit, not by rigor, and
the factor of nearly three between the kinds is a property of the unit rather
than of anyone's diligence.

### 4.7 What the auditors can see

The registry's blind spot is not only a measurement problem. The registry is the
standard public record of who provides care, and it is the view available to
anyone without access to commercial deal databases. In the four states with published OIG audits, this dataset
tracks 156 PE-owned clinics: 67 in Colorado (54 visible in the registry), 41 in
Indiana (11 visible), 48 in Wisconsin (2 visible) and none in Maine. **In
Wisconsin, the federal registry can see 2 of the 48 PE-owned centres in the
state.**

This pairing supports no causal claim, and none is made. No audit attributes any
payment to PE or to any owner, and Maine, with the third-largest finding of the
four ($45.6 million) and no PE-owned clinics tracked, shows directly that these
audits are not measuring ownership. What the pairing does show needs no causal
claim: a large, fast-growing public expenditure is being audited in states where
the public provider record does not show who owns the providers and, in
Wisconsin, does not show most of the PE-owned centres at all. Facility-level
ownership transparency, the leading recommendation of the advocacy report [6],
is the gap this dataset is built to fill.

---

## 5. Validation

A seeded, stratified random sample was drawn from the dataset as it stood on 19
July 2026 (seed 913063207): 110 PE clinics, 20 clinics held by other financial
owners, and 20 registry ABA sites that the dataset attributes to nobody. Within
the first two strata the draw was split by source in proportion, because registry
and directory rows fail differently. The third stratum is the only one that can
detect a false negative; a sample drawn only from published rows can measure
precision but can never find a missed owner. Rows were shuffled before review,
and review stopped at 99 of the 150, which are therefore themselves a random
subset of the draw.

Each published clinic was checked against sources independent of the one that
produced it, and counted as correct only if all four held: the centre is open,
at the address given, under the brand given, and owned by the firm given. A clinic
the reviewer could not confirm counts as a failure.

**66 of 67 PE clinics were correct on all four questions: 98.5% (95% Wilson
interval 92.0% to 99.7%).** By source, 48 of 48 directory-sourced PE clinics were
correct (92.6% to 100.0%) and 18 of 19 registry-sourced (94.7%, 75.4% to 99.1%).
The single failure is a registry-only row at an address the operator's own
directory does not list, which is where the design predicts failures to fall.
Pooled with the non-PE strata the figure is 83 of 84 (98.8%), but the non-PE
stratum was deliberately oversampled to test the owner-type label and so is not
pooled into the headline.

No sampled clinic was closed or was not an ABA clinic (0 of 84; upper bound
4.4%). None of the 15 unattributed registry sites reviewed had a financial owner
the dataset had missed. That is evidence of coverage rather than proof of it: at
n = 15 the upper bound on the miss rate is 20.4%.

Two reviewer verdicts were changed after review. In each, the reviewer marked a
clinic as being at the wrong address, and the operator's own directory showed it
runs two centres in that city, of which the reviewer had found the other. Before
those adjudications the PE rate was 64 of 67 (95.5%). Both figures are published,
and each changed row carries its reason in the committed review file.

The review also caught an error that every automated check had passed: one
operator's directory parser was misfiling street names into the city field
(Section 3.2). It affected eight centres, was fixed, and the correction was
published with the reason. This is the case for hand verification as a standing
step rather than a one-off: the garbled rows were each internally consistent, and
only a person reading the address against the world could see they were wrong.

---

## 6. Limitations

**The two counts are 19 months apart.** The deal-based estimate is as of 31
December 2024 and this dataset as of October 2026. Part of the 2.8-fold
difference is real growth in that interval, and it cannot be separated from the
difference in method without the JAMA study's site list, which is not published.
This paper does not claim the whole factor as method. The registry-basis
convergence (580 against 574) and the platform-basis agreement (1,642 against
1,521, counted six months apart rather than nineteen) are the evidence that method, rather than
growth alone, drives most of it.

**The registry reports existence-ever, not existence-now.** A provider
registration is filed once and essentially never revoked; every published
registry clinic reports active status, including some last certified many years
ago, and chains do not deactivate an identifier when a centre closes. Owner
directories remove this problem for 1,575 clinics. For the 182 registry-only
clinics it is graded rather than assumed away, and 30 of them sit on records six
or more years old. The verification sample found no closed clinics, but the
registry-only strata are small.

**The count is a floor, for a second and independent reason.** This dataset covers
21 of the 32 in-scope PE-backed ABA platforms in PESP's appendix. The 11 it has not
yet read, 8 not started and 3 blocked on sources that cannot be fetched, hold 504
facilities by PESP's count, including the post-bankruptcy Center for Autism and
Related Disorders (105). Reading them moves this dataset toward the platform
count, not away from the deal count.

**The share is a floor, and the bias runs against the finding.** Closed centres
are removed from the numerator, because tracked operators' directories can be
read, but cannot be removed from the denominator, because the other 17,000
operators' cannot. The asymmetry understates PE's share. It is disclosed rather
than corrected, because correcting it would mean guessing.

**Directories are self-reported.** An operator's directory is the best available
evidence of which centres are open now, which is why it overrides the registry,
but it is the operator's own statement. Rules exclude what a directory lists but
does not operate: "coming soon" sites, diagnostic-only clinics, service-area pages
and in-home offices. A directory overrides the registry only where it has been
checked as complete.

**Ownership is controlling ownership at a point in time.** Minority stakes in
which founders retain control are excluded, which removes several recognized
investor relationships from the count. Ownership changes; each link cites a
source stating current ownership, and a former owner appears as a dated event,
never as a live link.

**The verification has one reviewer.** The sample was reviewed by the author
alone, so no inter-rater agreement can be reported. The seed, the drawn sample and
the labelled review file are published so the exercise can be replicated
independently, which is invited. The sample also predates the current release,
and rows corrected since the draw are not re-sampled.

**What is deliberately not reported.** An earlier release headlined PE's share of
clinics run by operators with five or more sites. It was withdrawn because its
denominator is endogenous to PE's own acquisitions (Section 3.9), and no
equivalent measure is reported here under another name.

---

## 7. Reproducibility

Every number in this paper is regenerated from public inputs by public code.

- **Code and data pipeline:** https://github.com/fundprint/fundprint-data
- **Methodology, versioned:** https://github.com/fundprint/fundprint-methodology
  (this paper corresponds to `2026.10-verification-v1`)
- **Public dataset and dashboard:** https://whofundsmytherapist.com, built from
  https://github.com/fundprint/fundprint-dashboard
- **Registry archive:** the CMS NPPES monthly dissemination file, whose SHA-256
  is given in Section 2.1
- **Archived dataset:** Zenodo, https://doi.org/10.5281/zenodo.23426864

Each source document behind a published claim is stored as a content-addressed
snapshot and cited by hash, so a reader can confirm that the page a claim rests on
said what the claim says on the day it was read. The verification sample's seed,
draw and labelled review file are in the data repository under
`samples/` and `data/verification/`.

The full regeneration sequence, from archive to published figures, is:

```
python scripts/run_acquire_nppes_bulk.py --archive monthly.zip
python scripts/run_acquire_roster.py
python scripts/run_acquire_curated.py
python scripts/run_validate.py --resolver-version 0.1.0 \
    --methodology-version 2026.06-floors-v0
python scripts/create_dashboard_views.py
python scripts/compute_market_share.py --archive monthly.zip
python scripts/build_platform_denominator.py
python scripts/build_reconciliation.py
python scripts/build_state_files.py
python scripts/score_verification.py data/verification/reviewed_3bf05f0a.csv \
    --sample samples/verify_3bf05f0a.json --out data/verification/verification.json
python scripts/export_dashboard_snapshot.py
```

---

## Declarations

**Competing interests.** The author declares no competing interests. The
author holds no position in, and receives no compensation from, any company,
sponsor or advocacy organization named in this paper.

**Funding.** This work received no specific funding.

**Ethics.** This study uses only publicly available records about organizations:
a federal provider registry, companies' published location directories, public
ownership announcements and published government audits. It involves no human
participants and no individual-level or patient data, and so did not require
ethics review.

**Data availability.** The full dataset, the reviewed verification sample and
every derived figure are openly available under CC BY 4.0 at
https://github.com/fundprint/fundprint-data and archived on Zenodo at
https://doi.org/10.5281/zenodo.23426864. The registry archive is the free CMS NPPES monthly
dissemination file, identified by SHA-256 in Section 7. Archived copies of
third-party source documents are retained as evidence and are not redistributed
under that license.

**Code availability.** All code is openly available under the MIT License at
https://github.com/fundprint/fundprint-data and
https://github.com/fundprint/fundprint-dashboard. The methodology is versioned
and published under CC BY 4.0 at
https://github.com/fundprint/fundprint-methodology.

**Author contributions.** A.D. conceived the study, built the data pipeline,
performed the hand verification, analyzed the data and wrote the manuscript.

**Use of AI tools.** AI tools were used to help write the code. All code was
reviewed by the author.

---

## References

1. U.S. Department of Health and Human Services, Office of Inspector General.
   *Colorado Made at Least $77.8 Million in Improper Fee-for-Service Medicaid
   Payments for Applied Behavior Analysis Provided to Children.* A-09-24-02004,
   25 February 2026. https://oig.hhs.gov/reports/all/2026/colorado-made-at-least-778-million-in-improper-fee-for-service-medicaid-payments-for-applied-behavior-analysis-provided-to-children/
2. U.S. Department of Health and Human Services, Office of Inspector General.
   *Indiana Made at Least $56 Million in Improper Fee-for-Service Medicaid
   Payments for Applied Behavior Analysis Provided to Children Diagnosed With
   Autism.* A-09-22-02002, 2024. https://oig.hhs.gov/reports/all/2024/indiana-made-at-least-56-million-in-improper-fee-for-service-medicaid-payments-for-applied-behavior-analysis-provided-to-children-diagnosed-with-autism/
3. U.S. Department of Health and Human Services, Office of Inspector General.
   *Wisconsin Made at Least $18.5 Million in Improper Fee-For-Service Medicaid
   Payments for Applied Behavior Analysis Provided to Children Diagnosed With
   Autism.* July 2025. https://oig.hhs.gov/reports/all/2025/wisconsin-made-at-least-185-million-in-improper-fee-for-service-medicaid-payments-for-applied-behavior-analysis-provided-to-children-diagnosed-with-autism/
4. U.S. Department of Health and Human Services, Office of Inspector General.
   *HHS-OIG Audit Finds Maine Made at Least $45.6 Million in Improper Medicaid
   Payments for Autism Services.* 22 January 2026. https://oig.hhs.gov/newsroom/news-releases-articles/hhs-oig-audit-finds-maine-made-at-least-456-million-in-improper-medicaid-payments-for-autism-services/
5. Arnold DR, Reddy M, Cantor J, McBain RK, Yu H, Whaley CM, Singh Y. Private
   Equity in Autism Services. *JAMA Pediatrics*. Published online 5 January 2026.
   https://jamanetwork.com/journals/jamapediatrics/fullarticle/2843100
6. Private Equity Stakeholder Project. *Private equity's autism therapy boom is
   straining Medicaid.* April 2026. Appendix: Private equity-backed ABA
   providers. https://pestakeholder.org/wp-content/uploads/2026/04/PESP_Report_PE-in-ABA_2026.pdf
7. NBC News. Reporting on the Center for Autism and Related Disorders, Blackstone
   and its bankruptcy. https://www.nbcnews.com/health/health-care/card-blackstone-kids-autism-private-equity-bankruptcy-rcna118544
8. Behavioral Health Business. Bankruptcy court approves $48.5M sale of CARD;
   buyers to split up company. 27 July 2023. https://bhbusiness.com/2023/07/27/bankruptcy-court-approves-48-5m-sale-of-card-buyers-to-split-up-company/

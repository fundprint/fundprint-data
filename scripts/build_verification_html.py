"""Render a verification sample as a self-contained HTML review tool.

Turns samples/verify_<id>.json into verify_<id>.html: one file, opened in a
browser, no server. It shows one clinic at a time with the links you need to
check it, takes a verdict by keystroke, autosaves to the browser so you can stop
and come back, and exports the CSV that score_verification.py reads.

The reviewer is asked separate questions rather than one blended "is it right",
because the failure modes are different and a single verdict would blur them:

  * Does an ABA clinic exist at this address today? (catches ghosts: the registry
    never marks a closed clinic closed)
  * Is the owner brand right? (catches a bad name match)
  * Is the parent firm right? (catches a bad ownership claim)

For the `unclaimed` stratum the question is inverted: this is a clinic we claim
nothing about, and the reviewer is asked whether a financial owner is behind it.
A "yes" there is a false negative, and it is the most valuable single finding the
whole exercise can produce.

Usage:
    python scripts/build_verification_html.py samples/verify_<id>.json
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<title>Fundprint verification: {run_id}</title>
<style>
  :root {{ --ink:#1b1c1a; --paper:#d7d6c8; --sheet:#f6f5ef; --pe:#b3241c;
           --pen:#45525a; --rule:#c4c3b4; --mute:#565851; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--paper); color:var(--ink);
         font:16px/1.5 Archivo,system-ui,sans-serif; }}
  header {{ position:sticky; top:0; background:var(--sheet);
            border-bottom:1px solid var(--rule); padding:10px 20px; z-index:10;
            display:flex; align-items:center; gap:20px; flex-wrap:wrap; }}
  .bar {{ flex:1; min-width:180px; height:8px; background:var(--rule);
          border-radius:2px; overflow:hidden; }}
  .bar > i {{ display:block; height:100%; background:var(--pe); width:0; }}
  main {{ max-width:900px; margin:24px auto; padding:0 20px 120px; }}
  .card {{ background:var(--sheet); border:1px solid var(--rule); border-radius:3px;
           padding:24px; box-shadow:0 12px 26px -18px rgba(27,28,26,.45); }}
  .tag {{ display:inline-block; font-size:11px; font-weight:700; letter-spacing:.12em;
          text-transform:uppercase; padding:3px 8px; border-radius:2px;
          background:var(--pen); color:var(--sheet); }}
  .tag.unclaimed {{ background:var(--pe); }}
  h1 {{ font-size:26px; margin:14px 0 4px; }}
  .addr {{ font-size:17px; color:var(--mute); margin-bottom:16px; }}
  .claim {{ border-left:3px solid var(--pe); padding:10px 14px; background:#fff;
            margin:16px 0; }}
  .claim b {{ color:var(--pe); }}
  .links a {{ display:inline-block; margin:4px 8px 4px 0; padding:7px 12px;
              background:#fff; border:1px solid var(--rule); border-radius:2px;
              text-decoration:none; color:var(--ink); font-size:14px; }}
  .links a:hover {{ border-color:var(--pe); color:var(--pe); }}
  .q {{ margin-top:22px; padding-top:16px; border-top:1px solid var(--rule); }}
  .q p {{ margin:0 0 8px; font-weight:600; }}
  .opts {{ display:flex; gap:8px; flex-wrap:wrap; }}
  button.opt {{ font:inherit; padding:9px 14px; border:1px solid var(--rule);
                background:#fff; border-radius:2px; cursor:pointer; }}
  button.opt:hover {{ border-color:var(--pen); }}
  button.opt[aria-pressed="true"] {{ background:var(--pe); color:#fff;
                                     border-color:var(--pe); }}
  button.opt kbd {{ font:12px monospace; opacity:.6; margin-left:6px; }}
  button.opt[aria-pressed="true"] kbd {{ opacity:.85; }}
  textarea {{ width:100%; margin-top:14px; padding:10px; font:inherit;
              border:1px solid var(--rule); border-radius:2px; min-height:56px; }}
  .fix {{ margin-top:12px; padding:12px 14px; background:#fff;
          border-left:3px solid var(--pen); }}
  .fix p {{ margin:0 0 8px; font-weight:600; }}
  .fix input {{ width:100%; padding:9px 10px; font:inherit;
                border:1px solid var(--rule); border-radius:2px; }}
  .fix .muted {{ margin-top:8px; }}
  nav {{ position:fixed; bottom:0; left:0; right:0; background:var(--sheet);
         border-top:1px solid var(--rule); padding:12px 20px; display:flex;
         gap:10px; align-items:center; justify-content:center; }}
  nav button {{ font:inherit; padding:9px 16px; border:1px solid var(--rule);
                background:#fff; border-radius:2px; cursor:pointer; }}
  nav button.primary {{ background:var(--pe); color:#fff; border-color:var(--pe);
                        font-weight:600; }}
  .muted {{ color:var(--mute); font-size:13px; }}
  .done {{ text-align:center; padding:60px 20px; }}
</style>

<header>
  <strong>Fundprint verification</strong>
  <span class="muted" id="pos"></span>
  <span class="bar"><i id="prog"></i></span>
  <span class="muted" id="tally"></span>
</header>

<main id="main"></main>

<nav>
  <button id="prev">&larr; Back</button>
  <button id="skip">Skip</button>
  <button id="next" class="primary">Next &rarr;</button>
  <button id="export">Download CSV</button>
</nav>

<script>
const ROWS = {rows_json};
const RUN = {run_json};
const KEY = "fundprint_verify_" + RUN;
let answers = JSON.parse(localStorage.getItem(KEY) || "{{}}");
let i = 0;

// "Open, but not at this address" is its own answer, and keeping it separate from
// "closed" is the whole point. A clinic is DEFINED here as a physical site
// (owner + street + ZIP), so a wrong address is a wrong record even though the
// clinic is real. The two failures are opposites and must never be blended:
//
//   closed        -> we are counting a clinic that does not exist. An OVERCOUNT.
//   wrong_address -> the clinic exists, so the count is right, but the site key is
//                    wrong. The map lies, the state attribution may be wrong, and
//                    if the true address is already in the dataset under another
//                    row, one real site has been counted twice.
//
// So the reviewer is asked for the real address when they pick it. That turns a
// vague "this is a bit off" into a checkable fact, and the scorer can then ask the
// database whether the corrected address collides with a clinic we already hold.
const Q_EXISTS = {{
  id: "exists",
  q: "Does an ABA clinic operate at THIS address today?",
  opts: [
    ["yes", "Yes, open at this address", "1"],
    ["closed", "Closed / gone", "2"],
    ["wrong_address", "Open, but NOT at this address", "3"],
    ["not_aba", "Not an ABA clinic", "4"],
    ["unclear", "Cannot tell", "5"],
  ],
}};

const Q_CLAIMED = [
  Q_EXISTS,
  {{ id:"owner", q:"Is the owner brand correct?",
     opts:[["yes","Correct","1"],["no","Wrong","2"],["unclear","Cannot tell","3"]] }},
  {{ id:"firm", q:"Is the parent firm correct?",
     opts:[["yes","Correct","1"],["no","Wrong","2"],["unclear","Cannot tell","3"]] }},
];
const Q_UNCLAIMED = [
  Q_EXISTS,
  {{ id:"missed",
     q:"We claim NO owner here. Is it in fact owned by a PE firm or other "
       + "financial owner?",
     opts:[["no","No, independent","1"],["yes","YES, we missed one","2"],
           ["unclear","Cannot tell","3"]] }},
];

function save() {{ localStorage.setItem(KEY, JSON.stringify(answers)); }}

function questionsFor(r) {{
  return r.stratum === "unclaimed" ? Q_UNCLAIMED : Q_CLAIMED;
}}

function render() {{
  const r = ROWS[i];
  const a = answers[r.clinic_id] || {{}};
  const qs = questionsFor(r);
  const addr = [r.address, r.city, r.state, r.zip].filter(Boolean).join(", ");
  const maps = "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(addr);
  const query = '"' + (r.name||"") + '" ' + (r.city||"") + " " + (r.state||"");
  const web  = "https://www.google.com/search?q=" + encodeURIComponent(query);
  const npi  = r.npi ? "https://npiregistry.cms.hhs.gov/provider-view/" + r.npi : null;

  const claim = r.stratum === "unclaimed"
    ? `<div class="claim"><b>We claim no owner for this clinic.</b> It is in the federal registry as an ABA provider, and no tracked owner matched its name. Your job: find out if a financial owner is behind it.</div>`
    : `<div class="claim">We claim this clinic is operated by <b>${{esc(r.claimed_owner)}}</b>, owned by <b>${{esc(r.claimed_firm)}}</b> <span class="muted">(${{esc(r.claimed_firm_type)}})</span>.</div>`;

  const stale = r.registry_last_updated
    ? `<div class="muted">Registry record last updated ${{esc(r.registry_last_updated)}}</div>` : "";

  document.getElementById("main").innerHTML = `
    <div class="card">
      <span class="tag ${{r.stratum === "unclaimed" ? "unclaimed" : ""}}">${{esc(r.sub_stratum)}}</span>
      <h1>${{esc(r.name)}}</h1>
      <div class="addr">${{esc(addr)}}${{r.npi ? " &middot; NPI " + esc(r.npi) : ""}}</div>
      ${{claim}}
      ${{stale}}
      <div class="links" style="margin-top:14px">
        <a href="${{maps}}" target="_blank" rel="noopener">Google Maps</a>
        <a href="${{web}}" target="_blank" rel="noopener">Search the name</a>
        ${{npi ? `<a href="${{npi}}" target="_blank" rel="noopener">NPI registry</a>` : ""}}
        ${{(r.source_urls||[]).map((u,n) => `<a href="${{esc(u)}}" target="_blank" rel="noopener">Our source ${{n+1}}</a>`).join("")}}
      </div>
      ${{qs.map(q => `
        <div class="q">
          <p>${{esc(q.q)}}</p>
          <div class="opts">
            ${{q.opts.map(([val,label,key]) => `
              <button class="opt" data-q="${{q.id}}" data-v="${{val}}"
                aria-pressed="${{a[q.id] === val}}">${{esc(label)}}<kbd>${{key}}</kbd></button>`).join("")}}
          </div>
          ${{q.id === "exists" && a.exists === "wrong_address" ? `
            <div class="fix">
              <p>What is the real street address? Copy it exactly as the owner or
                 Maps gives it, including the suite.</p>
              <input id="correct_address" value="${{esc(a.correct_address||"")}}"
                     placeholder="e.g. 2760 Virginia Parkway Suite 100, McKinney, TX 75071">
              <div class="muted">A wrong address is not a closed clinic. The centre is
                real, so the count is not inflated, but the site key is wrong, and if
                the true address is already in the dataset then one real centre has
                been counted twice. Typing it here is what lets us find out.</div>
            </div>` : ""}}
        </div>`).join("")}}
      <textarea id="notes" placeholder="Notes (what you found, the URL that settled it)">${{esc(a.notes||"")}}</textarea>
    </div>`;

  document.querySelectorAll("button.opt").forEach(b => {{
    b.onclick = () => {{
      const q = b.dataset.q;
      answers[r.clinic_id] = answers[r.clinic_id] || {{}};
      answers[r.clinic_id][q] = b.dataset.v;
      save(); render();
    }};
  }});
  const fix = document.getElementById("correct_address");
  if (fix) fix.oninput = e => {{
    answers[r.clinic_id] = answers[r.clinic_id] || {{}};
    answers[r.clinic_id].correct_address = e.target.value;
    save();
  }};
  document.getElementById("notes").oninput = e => {{
    answers[r.clinic_id] = answers[r.clinic_id] || {{}};
    answers[r.clinic_id].notes = e.target.value;
    save();
  }};

  const done = ROWS.filter(x => {{
    const ans = answers[x.clinic_id] || {{}};
    return questionsFor(x).every(q => ans[q.id]);
  }}).length;
  document.getElementById("pos").textContent = `${{i+1}} of ${{ROWS.length}}`;
  document.getElementById("tally").textContent = `${{done}} complete`;
  document.getElementById("prog").style.width = (100*done/ROWS.length) + "%";
}}

function esc(s) {{
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[c]));
}}

document.getElementById("next").onclick = () => {{ if (i < ROWS.length-1) {{ i++; render(); window.scrollTo(0,0); }} }};
document.getElementById("prev").onclick = () => {{ if (i > 0) {{ i--; render(); window.scrollTo(0,0); }} }};
document.getElementById("skip").onclick = () => {{ if (i < ROWS.length-1) {{ i++; render(); window.scrollTo(0,0); }} }};

// Keys: 1-4 answer the FIRST unanswered question on the card, so a fast reviewer
// can go 1, 1, 1, Enter without ever touching the mouse.
document.onkeydown = e => {{
  if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
  const r = ROWS[i];
  const a = answers[r.clinic_id] || {{}};
  const qs = questionsFor(r);
  if (e.key === "Enter" || e.key === "ArrowRight") {{ document.getElementById("next").click(); return; }}
  if (e.key === "ArrowLeft") {{ document.getElementById("prev").click(); return; }}
  const open = qs.find(q => !a[q.id]);
  if (!open) return;
  const opt = open.opts.find(([,,key]) => key === e.key);
  if (!opt) return;
  answers[r.clinic_id] = answers[r.clinic_id] || {{}};
  answers[r.clinic_id][open.id] = opt[0];
  save(); render();
}};

document.getElementById("export").onclick = () => {{
  const cols = ["clinic_id","stratum","sub_stratum","name","address","city","state","zip",
                "npi","claimed_owner","claimed_firm","claimed_firm_type",
                "exists","owner","firm","missed","correct_address","notes"];
  const lines = [cols.join(",")];
  for (const r of ROWS) {{
    const a = answers[r.clinic_id] || {{}};
    const rec = {{...r, ...a}};
    lines.push(cols.map(c => {{
      const v = rec[c] == null ? "" : String(rec[c]);
      return /[",\\n]/.test(v) ? '"' + v.replace(/"/g,'""') + '"' : v;
    }}).join(","));
  }}
  const blob = new Blob([lines.join("\\n")], {{type:"text/csv"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "review_verify_" + RUN + ".csv";
  a.click();
}};

render();
</script>
"""


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    sheet = json.loads(src.read_text(encoding="utf-8"))
    out = src.parent.parent / f"verify_{sheet['run_id']}.html"
    out.write_text(
        TEMPLATE.format(
            run_id=html.escape(sheet["run_id"]),
            rows_json=json.dumps(sheet["rows"]),
            run_json=json.dumps(sheet["run_id"]),
        ),
        encoding="utf-8",
    )
    print(f"wrote {out}  ({sheet['total_drawn']} clinics, seed {sheet['seed']})")
    print("open it in a browser, label every row, then click 'Download CSV'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

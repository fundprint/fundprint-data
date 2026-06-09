"""Render a hand-validation sample as a self-contained HTML review tool.

Turns samples/<run_id>.json into validation_<run_id>.html: a single file you
open in a browser (no server, no internet needed beyond the source links). It
resolves entity UUIDs to readable names, shows one claim at a time with its
source link, and lets you label agree / disagree / unclear by click or
keyboard. Progress autosaves to the browser, and an export button downloads a
CSV in exactly the shape ``review_sample.py --score`` expects.

Usage:
    python scripts/build_validation_html.py samples/<run_id>.json
        -> writes validation_<run_id>.html next to it.

Workflow:
    1. Run this script, open the HTML, and label all 100 rows.
    2. Click "Download review CSV" -> review_<run_id>.csv.
    3. python scripts/review_sample.py --score review_<run_id>.csv \
            --sample samples/<run_id>.json
       (or use the JSON the tool can also export as the audit record).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fundprint import db


def _name_map() -> dict[str, str]:
    """Map every clinic / owner / PE-firm UUID to its human-readable name."""
    c = db.connect()
    try:
        out: dict[str, str] = {}
        for tbl in ("clinic", "owner_entity", "parent_pe_firm"):
            for oid, nm in c.execute(f"SELECT id, name FROM {tbl}").fetchall():
                out[str(oid)] = nm
        return out
    finally:
        c.close()


def _asserts(claim_type: str, link: dict, names: dict[str, str]) -> str:
    """Render a claim as 'Subject -> Object' using real names, not UUIDs."""

    def nm(key: str) -> str:
        return names.get(link.get(key, ""), link.get(key, "?"))

    if claim_type == "owner_to_pe_firm":
        return f"{nm('owner_entity_id')} → {nm('parent_pe_firm_id')}"
    if claim_type == "clinic_to_owner":
        return f"{nm('clinic_id')} → {nm('owner_entity_id')}"
    return claim_type


def build_rows(sheet: dict, names: dict[str, str]) -> list[dict]:
    """Flatten the sample into the minimal record the HTML/JS needs."""
    rows = []
    for r in sheet["rows"]:
        rows.append(
            {
                "claim_id": r["claim_id"],
                "claim_type": r["claim_type"],
                "asserts": _asserts(r["claim_type"], r["proposed_link"], names),
                "confidence": r["confidence_score"],
                "method": r["confidence_method"],
                "source_url": (r["source_urls"][0] if r["source_urls"] else ""),
                "verdict": r.get("reviewer_label") or "",
            }
        )
    return rows


def render_html(run_id: str, rows: list[dict]) -> str:
    """Return a standalone HTML document with the rows embedded."""
    data_json = json.dumps(rows)
    # The page is one f-string; literal CSS/JS braces are doubled.
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fundprint hand-validation — {run_id[:8]}</title>
<style>
  :root {{ --green:#1a7f37; --red:#cf222e; --gray:#6e7781; --bg:#f6f8fa; --card:#fff; --line:#d0d7de; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
          margin:0; background:var(--bg); color:#1f2328; }}
  header {{ position:sticky; top:0; background:var(--card); border-bottom:1px solid var(--line);
            padding:12px 20px; z-index:10; }}
  h1 {{ font-size:16px; margin:0 0 8px; }}
  .bar {{ height:10px; background:#eaeef2; border-radius:6px; overflow:hidden; }}
  .bar > i {{ display:block; height:100%; background:var(--green); width:0%; transition:width .2s; }}
  .meta {{ display:flex; gap:18px; flex-wrap:wrap; font-size:13px; color:#57606a; margin-top:8px; align-items:center; }}
  .meta b {{ color:#1f2328; }}
  .gate-pass {{ color:var(--green); font-weight:600; }}
  .gate-fail {{ color:var(--red); font-weight:600; }}
  main {{ max-width:760px; margin:24px auto; padding:0 16px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:24px; }}
  .counter {{ font-size:13px; color:var(--gray); margin-bottom:6px; }}
  .asserts {{ font-size:26px; font-weight:700; line-height:1.3; margin:6px 0 16px; }}
  .badges {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:18px; }}
  .badge {{ font-size:12px; padding:3px 10px; border-radius:999px; background:#eaeef2; color:#57606a; }}
  .src {{ display:inline-block; margin:4px 0 22px; padding:10px 16px; border:1px solid var(--line);
          border-radius:8px; text-decoration:none; color:#0969da; font-weight:600; word-break:break-all; }}
  .src.empty {{ color:var(--gray); pointer-events:none; }}
  .verdicts {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; }}
  .vbtn {{ padding:16px; font-size:16px; font-weight:600; border:2px solid var(--line); border-radius:10px;
           background:#fff; cursor:pointer; }}
  .vbtn:hover {{ background:#f3f4f6; }}
  .vbtn.sel-agree {{ border-color:var(--green); background:#e6f4ea; color:var(--green); }}
  .vbtn.sel-disagree {{ border-color:var(--red); background:#fbe9eb; color:var(--red); }}
  .vbtn.sel-unclear {{ border-color:var(--gray); background:#eef0f2; color:var(--gray); }}
  .kbd {{ font-size:11px; opacity:.6; display:block; margin-top:4px; font-weight:400; }}
  textarea {{ width:100%; margin-top:16px; padding:10px; border:1px solid var(--line);
              border-radius:8px; font-family:inherit; font-size:14px; resize:vertical; min-height:48px; }}
  .nav {{ display:flex; justify-content:space-between; margin-top:18px; }}
  .nav button {{ padding:8px 16px; border:1px solid var(--line); border-radius:8px; background:#fff; cursor:pointer; }}
  .toolbar {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:22px; }}
  .toolbar button {{ padding:10px 16px; border:1px solid var(--line); border-radius:8px;
                     background:#fff; cursor:pointer; font-weight:600; }}
  .toolbar .primary {{ background:#0969da; color:#fff; border-color:#0969da; }}
  .grid {{ display:flex; flex-wrap:wrap; gap:5px; margin-top:18px; }}
  .dot {{ width:22px; height:22px; border-radius:5px; border:1px solid var(--line); background:#fff;
          font-size:10px; display:flex; align-items:center; justify-content:center; cursor:pointer; color:#57606a; }}
  .dot.agree {{ background:var(--green); color:#fff; border-color:var(--green); }}
  .dot.disagree {{ background:var(--red); color:#fff; border-color:var(--red); }}
  .dot.unclear {{ background:var(--gray); color:#fff; border-color:var(--gray); }}
  .dot.cur {{ outline:2px solid #0969da; outline-offset:1px; }}
  .hint {{ font-size:12px; color:var(--gray); margin-top:14px; }}
</style>
</head>
<body>
<header>
  <h1>Fundprint hand-validation &mdash; sample <code>{run_id[:8]}</code></h1>
  <div class="bar"><i id="progbar"></i></div>
  <div class="meta">
    <span><b id="done">0</b>/<b id="total">0</b> labeled</span>
    <span>agree <b id="nA">0</b></span>
    <span>disagree <b id="nD">0</b></span>
    <span>unclear <b id="nU">0</b></span>
    <span>accuracy <b id="acc">&ndash;</b> <span id="gate"></span></span>
  </div>
</header>
<main>
  <div class="card">
    <div class="counter">Claim <span id="idx">1</span> of <span id="cnt">0</span></div>
    <div class="asserts" id="asserts">&hellip;</div>
    <div class="badges" id="badges"></div>
    <a class="src" id="src" target="_blank" rel="noopener">Open source &#8599;</a>
    <div class="verdicts">
      <button class="vbtn" data-v="agree" onclick="setV('agree')">&#10003; Agree<span class="kbd">key: A</span></button>
      <button class="vbtn" data-v="disagree" onclick="setV('disagree')">&#10007; Disagree<span class="kbd">key: D</span></button>
      <button class="vbtn" data-v="unclear" onclick="setV('unclear')">? Unclear<span class="kbd">key: U</span></button>
    </div>
    <textarea id="notes" placeholder="Optional notes for this claim..." oninput="saveNotes(this.value)"></textarea>
    <div class="nav">
      <button onclick="go(-1)">&#8592; Prev (K)</button>
      <button onclick="nextUnlabeled()">Next unlabeled</button>
      <button onclick="go(1)">Next (J) &#8594;</button>
    </div>
    <div class="grid" id="grid"></div>
    <div class="toolbar">
      <button class="primary" onclick="downloadCSV()">&#8681; Download review CSV</button>
      <button onclick="downloadJSON()">&#8681; Download labeled JSON</button>
      <button onclick="resetAll()">Reset</button>
    </div>
    <div class="hint">Then run: <code>python scripts/review_sample.py --score review_{run_id}.csv --sample samples/{run_id}.json</code></div>
  </div>
</main>
<script>
const RUN_ID = "{run_id}";
const ROWS = {data_json};
const KEY = "fundprint-val-" + RUN_ID;
const notes = {{}};
let i = 0;

// Load saved progress.
(function load() {{
  try {{
    const s = JSON.parse(localStorage.getItem(KEY) || "{{}}");
    ROWS.forEach(r => {{ if (s.v && s.v[r.claim_id]) r.verdict = s.v[r.claim_id]; }});
    if (s.notes) Object.assign(notes, s.notes);
    if (typeof s.i === "number") i = s.i;
  }} catch (e) {{}}
}})();

function save() {{
  const v = {{}}; ROWS.forEach(r => {{ if (r.verdict) v[r.claim_id] = r.verdict; }});
  localStorage.setItem(KEY, JSON.stringify({{ v, notes, i }}));
}}

function setV(v) {{
  ROWS[i].verdict = v; save(); render();
  setTimeout(nextUnlabeled, 150);
}}
function saveNotes(t) {{ notes[ROWS[i].claim_id] = t; save(); }}
function go(d) {{ i = Math.max(0, Math.min(ROWS.length - 1, i + d)); save(); render(); }}
function jump(n) {{ i = n; save(); render(); }}
function nextUnlabeled() {{
  const start = i;
  for (let k = 1; k <= ROWS.length; k++) {{
    const j = (start + k) % ROWS.length;
    if (!ROWS[j].verdict) {{ i = j; save(); render(); return; }}
  }}
  render();
}}
function resetAll() {{
  if (!confirm("Clear all labels for this sample?")) return;
  ROWS.forEach(r => r.verdict = "");
  for (const k in notes) delete notes[k];
  i = 0; save(); render();
}}

function render() {{
  const r = ROWS[i];
  document.getElementById("idx").textContent = i + 1;
  document.getElementById("cnt").textContent = ROWS.length;
  document.getElementById("asserts").textContent = r.asserts;
  document.getElementById("badges").innerHTML =
    `<span class="badge">${{r.claim_type}}</span>` +
    `<span class="badge">method: ${{r.method}}</span>` +
    `<span class="badge">confidence: ${{r.confidence}}</span>`;
  const src = document.getElementById("src");
  if (r.source_url) {{ src.href = r.source_url; src.className = "src"; src.textContent = "Open source ↗  " + r.source_url; }}
  else {{ src.removeAttribute("href"); src.className = "src empty"; src.textContent = "(no source URL)"; }}
  document.querySelectorAll(".vbtn").forEach(b =>
    b.className = "vbtn" + (b.dataset.v === r.verdict ? " sel-" + r.verdict : ""));
  document.getElementById("notes").value = notes[r.claim_id] || "";

  // Stats.
  let a = 0, d = 0, u = 0;
  ROWS.forEach(x => {{ if (x.verdict === "agree") a++; else if (x.verdict === "disagree") d++; else if (x.verdict === "unclear") u++; }});
  const done = a + d + u, decided = a + d;
  document.getElementById("done").textContent = done;
  document.getElementById("total").textContent = ROWS.length;
  document.getElementById("nA").textContent = a;
  document.getElementById("nD").textContent = d;
  document.getElementById("nU").textContent = u;
  document.getElementById("progbar").style.width = (100 * done / ROWS.length) + "%";
  const accEl = document.getElementById("acc"), gateEl = document.getElementById("gate");
  if (decided) {{
    const ratio = a / decided;
    accEl.textContent = (ratio * 100).toFixed(1) + "%";
    const pass = ratio >= 0.95 && done === ROWS.length;
    gateEl.textContent = pass ? "GATE PASS" : (done === ROWS.length ? "GATE FAIL" : "(in progress)");
    gateEl.className = pass ? "gate-pass" : (done === ROWS.length ? "gate-fail" : "");
  }} else {{ accEl.textContent = "–"; gateEl.textContent = ""; }}

  // Dot grid.
  const grid = document.getElementById("grid");
  grid.innerHTML = "";
  ROWS.forEach((x, n) => {{
    const dot = document.createElement("div");
    dot.className = "dot " + (x.verdict || "") + (n === i ? " cur" : "");
    dot.textContent = n + 1; dot.title = x.asserts;
    dot.onclick = () => jump(n);
    grid.appendChild(dot);
  }});
}}

function csvCell(s) {{ s = (s == null ? "" : String(s)); return '"' + s.replace(/"/g, '""') + '"'; }}
function downloadCSV() {{
  const head = ["claim_id","claim_type","asserts","confidence","method","source_url","verdict","notes"];
  const lines = [head.join(",")];
  ROWS.forEach(r => lines.push([
    r.claim_id, r.claim_type, r.asserts, r.confidence, r.method, r.source_url,
    r.verdict || "", notes[r.claim_id] || ""
  ].map(csvCell).join(",")));
  dl("review_" + RUN_ID + ".csv", lines.join("\\n"), "text/csv");
}}
function downloadJSON() {{
  const out = ROWS.map(r => ({{ claim_id: r.claim_id, reviewer_label: r.verdict || null, notes: notes[r.claim_id] || "" }}));
  dl("labels_" + RUN_ID + ".json", JSON.stringify({{ run_id: RUN_ID, labels: out }}, null, 2), "application/json");
}}
function dl(name, text, type) {{
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], {{ type }}));
  a.download = name; a.click(); URL.revokeObjectURL(a.href);
}}

document.addEventListener("keydown", e => {{
  if (e.target.tagName === "TEXTAREA") return;
  const k = e.key.toLowerCase();
  if (k === "a") setV("agree");
  else if (k === "d") setV("disagree");
  else if (k === "u") setV("unclear");
  else if (k === "j" || e.key === "ArrowRight") go(1);
  else if (k === "k" || e.key === "ArrowLeft") go(-1);
}});

render();
</script>
</body>
</html>
"""


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    sample_path = Path(sys.argv[1])
    sheet = json.loads(sample_path.read_text())
    rows = build_rows(sheet, _name_map())
    html = render_html(sheet["run_id"], rows)
    out = sample_path.with_name(f"validation_{sheet['run_id']}.html")
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {len(rows)} rows -> {out}")
    print("Open it in a browser, label all rows, then Download review CSV.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

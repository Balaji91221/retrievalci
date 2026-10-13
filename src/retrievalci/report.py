"""Offline, interactive report generation; all source strings are HTML escaped."""
from __future__ import annotations

import html
import json
from typing import Any


def render_html(report: dict[str, Any]) -> str:
    verdict = html.escape(report["verdict"])
    regression_ids = {row["query_id"] for row in report["regressions"]}
    rows = []
    for delta in report["query_deltas"]:
        name = html.escape(delta["query_id"], quote=True)
        evidence = html.escape(json.dumps(delta, indent=2, ensure_ascii=False))
        rows.append(f'<tr data-query="{name}" data-regression="{str(delta["query_id"] in regression_ids).lower()}">'
                    f'<td><details><summary>{name}</summary><pre>{evidence}</pre></details></td>'
                    f'<td>{delta["recall_drop"]:.3f}</td><td>{delta["rr_drop"]:.3f}</td></tr>')
    diffs = []
    for source in report.get("source_diffs", []):
        title = html.escape(f'{source["kind"]}: {source["source_id"]} ({source["before_path"]} → {source["after_path"]})')
        diffs.append(f'<details><summary>{title}</summary><pre>{html.escape(source["diff"] or "Path changed; content identical.")}</pre></details>')
    raw = html.escape(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
    issues = html.escape(json.dumps(report["compatibility_issues"] + report["review_issues"], indent=2, ensure_ascii=False))
    return f'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>RetrievalCI — {verdict}</title>
<style>body{{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px;color:#142638}}
table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;padding:12px;border-bottom:1px solid #ccc;vertical-align:top}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5f7;padding:18px;border-radius:8px}}
.verdict{{font-size:30px;font-weight:700}}p{{line-height:1.5}}summary{{cursor:pointer}}input{{padding:8px}}details{{margin:10px 0}}</style>
<h1>RetrievalCI comparison</h1><p class="verdict">{verdict}</p>
<p>Positive drops mean worse retrieval. Review issues and incomplete runs cannot pass.
Source and score changes are observations, not proof of causation.</p>
<label>Filter query <input id="filter" type="search" placeholder="Query ID"></label>
<label><input id="regressions" type="checkbox"> Regressions only</label>
<p id="visible" aria-live="polite"></p>
<table><thead><tr><th>Query (expand for evidence)</th><th>Recall drop</th><th>Reciprocal rank drop</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
<h2>Gate and review issues</h2><pre>{issues}</pre>
<h2>Source changes</h2>{"".join(diffs) or '<p>No mapped source changes.</p>'}
<details><summary>Complete machine-readable report</summary><pre>{raw}</pre></details>
<script>
const filter = document.getElementById('filter');
const regressions = document.getElementById('regressions');
function update() {{
 let count = 0;
 document.querySelectorAll('tbody tr').forEach(row => {{
  const matches = row.dataset.query.toLocaleLowerCase().includes(filter.value.toLocaleLowerCase());
  const show = matches && (!regressions.checked || row.dataset.regression === 'true');
  row.hidden = !show; if (show) count++;
 }});
 document.getElementById('visible').textContent = count + ' queries shown';
}}
filter.addEventListener('input', update); regressions.addEventListener('change', update); update();
</script></html>'''

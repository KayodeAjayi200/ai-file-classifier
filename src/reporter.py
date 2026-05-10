import json
from pathlib import Path
from datetime import datetime
from src.database import Database

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AI File Classifier Report</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: system-ui, -apple-system, sans-serif; background: #0f0f0f; color: #e0e0e0; padding: 24px; }}
h1 {{ color: #60a5fa; margin-bottom: 6px; font-size: 1.6em; }}
.sub {{ color: #6b7280; margin-bottom: 28px; font-size: 0.9em; }}
.stats {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 28px; }}
.card {{ background: #1a1a1a; border-radius: 12px; padding: 14px 22px; min-width: 110px; }}
.num {{ font-size: 2em; font-weight: 700; }}
.lbl {{ color: #6b7280; font-size: 0.8em; margin-top: 2px; }}
.keep {{ color: #34d399; }} .review {{ color: #fbbf24; }}
.probably_delete {{ color: #f87171; }} .duplicates {{ color: #c084fc; }}
.low_quality {{ color: #fb923c; }} .screenshots {{ color: #60a5fa; }}
.filters {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }}
.btn {{ padding: 6px 16px; border-radius: 999px; border: 1px solid #333; background: #1a1a1a;
        color: #e0e0e0; cursor: pointer; font-size: 0.82em; transition: background .15s; }}
.btn.on {{ background: #2563eb; border-color: #2563eb; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.88em; }}
th {{ text-align: left; padding: 9px 12px; background: #161616; color: #6b7280;
      font-weight: 500; font-size: 0.8em; text-transform: uppercase; letter-spacing: .04em; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #1a1a1a; max-width: 280px;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
tr:hover td {{ background: #181818; }}
.badge {{ display: inline-block; padding: 2px 9px; border-radius: 9999px; font-size: 0.75em; font-weight: 600; }}
.bk {{ background: #064e3b; color: #34d399; }}
.br {{ background: #451a03; color: #fbbf24; }}
.bd {{ background: #450a0a; color: #f87171; }}
</style>
</head>
<body>
<h1>🤖 AI File Classifier</h1>
<p class="sub">Report generated {date}</p>
<div class="stats">
  <div class="card"><div class="num">{total}</div><div class="lbl">Total</div></div>
  <div class="card"><div class="num keep">{keep}</div><div class="lbl">Keep</div></div>
  <div class="card"><div class="num review">{review}</div><div class="lbl">Review</div></div>
  <div class="card"><div class="num probably_delete">{probably_delete}</div><div class="lbl">Probably Delete</div></div>
  <div class="card"><div class="num duplicates">{duplicates}</div><div class="lbl">Duplicates</div></div>
  <div class="card"><div class="num low_quality">{low_quality}</div><div class="lbl">Low Quality</div></div>
</div>
<div class="filters">
  <button class="btn on" onclick="filter('all',this)">All</button>
  <button class="btn" onclick="filter('keep',this)">Keep</button>
  <button class="btn" onclick="filter('review',this)">Review</button>
  <button class="btn" onclick="filter('probably_delete',this)">Probably Delete</button>
</div>
<table id="t"><thead>
  <tr><th>File</th><th>Type</th><th>Category</th><th>Quality</th><th>Action</th><th>Confidence</th><th>Reason</th></tr>
</thead><tbody>
{rows}
</tbody></table>
<script>
function filter(a,btn){{
  document.querySelectorAll('.btn').forEach(b=>b.classList.remove('on'));
  btn.classList.add('on');
  document.querySelectorAll('#t tbody tr').forEach(r=>{{
    r.style.display = (a==='all'||r.dataset.a===a) ? '' : 'none';
  }});
}}
</script>
</body></html>"""


class Reporter:
    def __init__(self, db: Database):
        self.db = db

    def generate(self, output_path: str):
        stats = self.db.get_stats()
        results = self.db.get_all_results()

        rows = []
        for r in results:
            action = r.get('action', 'review')
            badge = {'keep': 'bk', 'review': 'br', 'probably_delete': 'bd'}.get(action, 'br')
            name = Path(r['path']).name
            rows.append(
                f'<tr data-a="{action}">'
                f'<td title="{r["path"]}">{name}</td>'
                f'<td>{r.get("file_type","?")}</td>'
                f'<td>{r.get("category","?")}</td>'
                f'<td>{r.get("quality","?")}</td>'
                f'<td><span class="badge {badge}">{action}</span></td>'
                f'<td>{r.get("confidence","?")}%</td>'
                f'<td>{r.get("reason","")}</td>'
                f'</tr>'
            )

        html = _HTML.format(
            date=datetime.now().strftime("%Y-%m-%d %H:%M"),
            rows='\n'.join(rows),
            **stats,
        )
        Path(output_path).write_text(html, encoding='utf-8')

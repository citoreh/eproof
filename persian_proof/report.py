"""HTML and CSV reports."""
import csv
import html
import json
from pathlib import Path

from . import store
from .rules import RULES_BY_ID, ZWNJ

SOURCES = ("rules", "spelling", "grammar")
CATS = [
    ("grammar", "دستور و نگارش", "Grammar"),
    ("spelling", "املا", "Spelling"),
    ("typography", "حروف‌نگاری", "Typography"),
    ("style", "شیوه‌نامه", "House style"),
]
CAT_FA = {c: fa for c, fa, _ in CATS}

CSS = """
:root{--bg:#faf8f4;--panel:#fff;--ink:#1f1d1a;--muted:#6b665e;--line:#e6e1d8;
--accent:#8a3b12;--del:#fbe3dc;--delink:#9b2c12;--ins:#dcefe0;--insink:#1d6b34;
--chip:#f1ece3;--g:#6a3fa0;--s:#b3261e;--t:#1f5fa8;--y:#7a6a00}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#171613;--panel:#211f1b;
--ink:#ece8e1;--muted:#a39d92;--line:#353229;--accent:#e39a6a;--del:#4a241b;--delink:#ffb4a1;
--ins:#1e3b27;--insink:#9be0ad;--chip:#2c2924;--g:#c6a8f0;--s:#ff8a80;--t:#8ab8ff;--y:#e5d36a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:Vazirmatn,Tahoma,sans-serif;
line-height:1.9;font-size:15px}
.wrap{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.1rem;margin:32px 0 10px}
.sub{color:var(--muted);font-size:.9rem;margin-bottom:20px}
a{color:var(--accent)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.stat b{display:block;font-size:1.4rem;font-feature-settings:"tnum"}.stat span{color:var(--muted);font-size:.85rem}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);
border-radius:10px;overflow:hidden;font-size:.9rem}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:right;vertical-align:top}
th{background:var(--chip);cursor:pointer;white-space:nowrap;user-select:none}
td.n{font-feature-settings:"tnum";white-space:nowrap}
.tablewrap{overflow-x:auto}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0 14px}
.bar button{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:999px;
padding:4px 12px;font:inherit;font-size:.85rem;cursor:pointer}
.bar button.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.bar input{flex:1;min-width:160px;border:1px solid var(--line);background:var(--panel);color:var(--ink);
border-radius:8px;padding:6px 10px;font:inherit}
.issue{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:8px 0}
.meta{display:flex;flex-wrap:wrap;gap:6px 12px;font-size:.8rem;color:var(--muted);align-items:center}
.chip{background:var(--chip);border-radius:6px;padding:0 8px;font-weight:600}
.c-grammar{color:var(--g)}.c-spelling{color:var(--s)}.c-typography{color:var(--t)}.c-style{color:var(--y)}
.ctx{margin:6px 0 2px;font-size:1.02rem}
mark{background:var(--del);color:var(--delink);text-decoration:line-through;border-radius:3px;padding:0 2px}
ins{background:var(--ins);color:var(--insink);text-decoration:none;border-radius:3px;padding:0 2px;margin-inline-start:4px}
.zw{display:inline-block;width:3px;height:.95em;vertical-align:-.1em;background:currentColor;opacity:.55;margin:0 1px;border-radius:1px}
.sp{display:inline-block;width:.6em;border-bottom:1.5px solid currentColor;height:.6em;margin:0 1px}
.msg{font-size:.88rem}
details{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 14px;margin:6px 0}
summary{cursor:pointer}
.ltr{direction:ltr;unicode-bidi:isolate}
.empty{color:var(--muted);padding:20px 0}
"""

JS = """
const q=s=>document.querySelector(s),qa=s=>[...document.querySelectorAll(s)];
let cat='all';
function apply(){const t=(q('#q')?.value||'').trim();
 qa('.issue').forEach(e=>{const ok=(cat==='all'||e.dataset.cat===cat)&&(!t||e.textContent.includes(t));
 e.style.display=ok?'':'none'});}
qa('.bar button').forEach(b=>b.onclick=()=>{cat=b.dataset.cat;qa('.bar button').forEach(x=>x.classList.toggle('on',x===b));apply();});
q('#q')?.addEventListener('input',apply);
qa('th[data-k]').forEach((th,ci)=>th.onclick=()=>{const tb=th.closest('table').tBodies[0];
 const rows=[...tb.rows],idx=[...th.parentNode.children].indexOf(th),num=th.dataset.k==='n';
 const dir=th.dataset.dir==='d'?1:-1;th.dataset.dir=dir===1?'a':'d';
 rows.sort((a,b)=>{let x=a.cells[idx].dataset.v??a.cells[idx].textContent,y=b.cells[idx].dataset.v??b.cells[idx].textContent;
 if(num){x=+x;y=+y;return (x-y)*dir}return x.localeCompare(y,'fa')*dir});rows.forEach(r=>tb.appendChild(r));});
"""


def _page(title, body, depth=0):
    return f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;600;700&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body><div class="wrap">{body}</div><script>{JS}</script></body></html>"""


def _vis(s):
    """Escape and make ZWNJ / spaces visible inside a highlighted fragment."""
    out = []
    for ch in s:
        if ch == ZWNJ:
            out.append('<span class="zw" title="نیم‌فاصله"></span>')
        elif ch in "  ":
            out.append('<span class="sp" title="فاصله"></span>')
        else:
            out.append(html.escape(ch))
    return "".join(out)


def _context(text, off, ln, sugg, width=70):
    s = max(0, off - width)
    e = min(len(text), off + ln + width)
    pre = ("…" if s > 0 else "") + html.escape(text[s:off])
    post = html.escape(text[off + ln:e]) + ("…" if e < len(text) else "")
    orig = text[off:off + ln]
    del_html = f"<mark>{_vis(orig)}</mark>" if orig else ""
    return f'{pre}{del_html}<ins>{_vis(sugg) if sugg else "∅"}</ins>{post}'


def _plain_context(text, off, ln, width=50):
    s = max(0, off - width)
    return text[s:off] + "⟦" + text[off:off + ln] + "⟧" + text[off + ln:off + ln + width]


def _book_issues(out_dir, bid):
    """All issues for a book; the same span flagged by several passes is shown once."""
    items, seen = [], {}
    for src in SOURCES:
        for x in store.load_issues(out_dir, bid, src):
            key = (x["f"], x["i"], x["off"], x["len"])
            if key in seen and x["severity"] != "safe":
                prev = seen[key]
                prev["src"] = prev["src"] + "+" + src
                continue
            x["src"] = src
            seen[key] = x
            items.append(x)
    return items


def build(out_dir, min_conf=0.0):
    out = Path(out_dir)
    rep = out / "report"
    (rep / "books").mkdir(parents=True, exist_ok=True)
    books = store.load_books(out_dir)
    fixlog = store.read_json(out_dir, "fix_log.json", {}) or {}
    rows = []
    all_csv = open(rep / "all_issues.csv", "w", newline="", encoding="utf-8-sig")
    w = csv.writer(all_csv)
    w.writerow(["book", "file", "paragraph", "category", "rule", "source", "original",
                "suggestion", "confidence", "message", "context"])
    totals = {"books": 0, "words": 0, "safe": 0, "grammar": 0, "spelling": 0, "typography": 0,
              "style": 0}

    for b in books:
        if b.get("error"):
            rows.append({"b": b, "err": b["error"]})
            continue
        bid = b["id"]
        texts = {(f, bl["i"]): bl["t"] for f, blocks in store.load_blocks(out_dir, bid)
                 for bl in blocks}
        issues = [x for x in _book_issues(out_dir, bid)
                  if x.get("conf", 1) >= min_conf]
        review = [x for x in issues if x["severity"] != "safe"]
        safe_examples = [x for x in issues if x["severity"] == "safe"]
        order = {"grammar": 0, "spelling": 1, "typography": 2, "style": 3}
        review.sort(key=lambda x: (x["f"] not in b.get("spine", []),
                                   b.get("spine", []).index(x["f"]) if x["f"] in b.get("spine", []) else 0,
                                   x["i"], x["off"]))
        counts = {c: sum(1 for x in review if x["category"] == c) for c, _, _ in CATS}
        safe_total = sum(b.get("safe", {}).values())
        words = b.get("words", 0) or 1
        density = (counts["grammar"] + counts["spelling"] + counts["typography"]) / words * 1000
        totals["books"] += 1
        totals["words"] += b.get("words", 0)
        totals["safe"] += safe_total
        for c in counts:
            totals[c] += counts[c]
        rows.append({"b": b, "counts": counts, "safe": safe_total, "density": density})

        # --- CSV
        for x in review:
            t = texts.get((x["f"], x["i"]), "")
            w.writerow([b["title"], x["f"], x["i"], x["category"], x["rule"], x["src"], x["orig"],
                        x["sugg"], x.get("conf", ""), x.get("msg_fa", ""),
                        _plain_context(t, x["off"], x["len"])])

        # --- book page
        parts = [f'<p class="sub"><a href="../index.html">→ همه‌ی کتاب‌ها</a></p>',
                 f"<h1>{html.escape(b['title'])}</h1>",
                 f'<div class="sub ltr">{html.escape(b["rel"])}</div>',
                 '<div class="stats">',
                 f'<div class="stat"><b>{b.get("words", 0):,}</b><span>واژه</span></div>']
        for c, fa, _ in CATS:
            parts.append(f'<div class="stat"><b class="c-{c}">{counts[c]:,}</b><span>{fa}</span></div>')
        parts.append(f'<div class="stat"><b>{safe_total:,}</b><span>اصلاح خودکار</span></div></div>')

        parts.append("<h2>موارد نیازمند بررسی</h2>")
        if review:
            parts.append('<div class="bar"><button class="on" data-cat="all">همه</button>')
            for c, fa, _ in CATS:
                if counts[c]:
                    parts.append(f'<button data-cat="{c}">{fa} ({counts[c]:,})</button>')
            parts.append('<input id="q" placeholder="جست‌وجو در موارد…"></div>')
            for x in review:
                t = texts.get((x["f"], x["i"]), "")
                conf = f'<span>اطمینان {x["conf"]:.2f}</span>' if "conf" in x else ""
                src = " + ".join({"rules": "قاعده", "spelling": "آمار پیکره", "grammar": "Claude"}[s]
                                 for s in x["src"].split("+"))
                parts.append(
                    f'<div class="issue" data-cat="{x["category"]}"><div class="meta">'
                    f'<span class="chip c-{x["category"]}">{CAT_FA[x["category"]]}</span>'
                    f'<span>{src}</span>{conf}'
                    f'<span class="ltr">{html.escape(x["f"])} ¶{x["i"]}</span></div>'
                    f'<div class="ctx">{_context(t, x["off"], x["len"], x["sugg"])}</div>'
                    f'<div class="msg">{html.escape(x.get("msg_fa", ""))}</div></div>')
        else:
            parts.append('<p class="empty">موردی یافت نشد.</p>')

        safe = b.get("safe", {})
        fixed = fixlog.get(bid)
        parts.append("<h2>اصلاح‌های خودکار</h2>")
        if fixed is not None:
            parts.append(f'<p class="sub">در نسخه‌ی اصلاح‌شده {sum(fixed.values()):,} تغییر اعمال شد.</p>')
        else:
            parts.append('<p class="sub">این موارد با فرمان fix در نسخه‌ی اصلاح‌شده اعمال می‌شوند.</p>')
        if safe:
            for rid, n in sorted(safe.items(), key=lambda kv: -kv[1]):
                ex = [x for x in safe_examples if x["rule"] == rid][:8]
                exh = "".join(
                    f'<div class="ctx">{_context(texts.get((x["f"], x["i"]), ""), x["off"], x["len"], x["sugg"], 40)}</div>'
                    for x in ex)
                r = RULES_BY_ID.get(rid)
                parts.append(f"<details><summary>{html.escape(r.fa if r else rid)} — "
                             f"<b>{n:,}</b></summary>{exh}</details>")
        else:
            parts.append('<p class="empty">موردی نبود.</p>')
        (rep / "books" / f"{bid}.html").write_text(_page(b["title"], "".join(parts)),
                                                   encoding="utf-8")
    all_csv.close()

    # --- summary CSV
    with open(rep / "summary.csv", "w", newline="", encoding="utf-8-sig") as fh:
        sw = csv.writer(fh)
        sw.writerow(["book", "file", "words", "grammar", "spelling", "typography_review", "style",
                     "auto_fixed", "issues_per_1000_words", "error"])
        for r in rows:
            b = r["b"]
            if "err" in r:
                sw.writerow([b.get("title", ""), b["rel"], "", "", "", "", "", "", "", r["err"]])
                continue
            c = r["counts"]
            sw.writerow([b["title"], b["rel"], b.get("words", 0), c["grammar"], c["spelling"],
                         c["typography"], c["style"], r["safe"], round(r["density"], 2), ""])

    # --- index
    stats = store.read_json(out_dir, "corpus_stats.json", {}) or {}
    trs = []
    for r in sorted([r for r in rows if "err" not in r], key=lambda r: -r["density"]):
        b, c = r["b"], r["counts"]
        trs.append(
            f'<tr><td><a href="books/{b["id"]}.html">{html.escape(b["title"])}</a>'
            f'<div class="sub ltr" style="margin:0">{html.escape(b["rel"])}</div></td>'
            f'<td class="n" data-v="{b.get("words", 0)}">{b.get("words", 0):,}</td>'
            + "".join(f'<td class="n c-{k}" data-v="{c[k]}">{c[k]:,}</td>'
                      for k in ("grammar", "spelling", "typography", "style"))
            + f'<td class="n" data-v="{r["safe"]}">{r["safe"]:,}</td>'
            f'<td class="n" data-v="{r["density"]:.3f}">{r["density"]:.1f}</td></tr>')
    errs = [r for r in rows if "err" in r]
    err_html = ""
    if errs:
        err_html = "<h2>فایل‌های ناخوانا</h2><ul>" + "".join(
            f'<li class="ltr">{html.escape(r["b"]["rel"])} — {html.escape(r["err"])}</li>'
            for r in errs) + "</ul>"
    body = f"""<h1>گزارش ویرایش کتاب‌های الکترونیکی</h1>
<div class="sub">{totals['books']:,} کتاب · {totals['words']:,} واژه · {stats.get('types', 0):,} واژه‌ی یکتا در پیکره</div>
<div class="stats">
<div class="stat"><b class="c-grammar">{totals['grammar']:,}</b><span>دستور و نگارش</span></div>
<div class="stat"><b class="c-spelling">{totals['spelling']:,}</b><span>املا</span></div>
<div class="stat"><b class="c-typography">{totals['typography']:,}</b><span>حروف‌نگاری (بررسی)</span></div>
<div class="stat"><b class="c-style">{totals['style']:,}</b><span>شیوه‌نامه</span></div>
<div class="stat"><b>{totals['safe']:,}</b><span>اصلاح خودکار</span></div></div>
<p class="sub">مرتب‌شده بر اساس تراکم خطا (موارد بررسی در هر ۱۰۰۰ واژه). برای مرتب‌سازی روی سرستون‌ها بزنید.</p>
<div class="bar"><input id="qt" placeholder="جست‌وجوی عنوان…" oninput="for(const r of document.querySelectorAll('tbody tr'))r.style.display=r.textContent.includes(this.value)?'':'none'"></div>
<div class="tablewrap"><table><thead><tr><th data-k="s">کتاب</th><th data-k="n">واژه</th>
<th data-k="n">دستور</th><th data-k="n">املا</th><th data-k="n">حروف‌نگاری</th><th data-k="n">شیوه‌نامه</th>
<th data-k="n">خودکار</th><th data-k="n">تراکم</th></tr></thead><tbody>{''.join(trs)}</tbody></table></div>
{err_html}"""
    (rep / "index.html").write_text(_page("گزارش ویرایش", body), encoding="utf-8")
    print(f"Report: {rep / 'index.html'}")
    return rep

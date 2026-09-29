"""Grammar/context pass with Claude (Message Batches API, or synchronous pilot)."""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import store
from .rules import has_persian, is_arabic_like

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_CHARS = 6000          # paragraph text per request
PER_BATCH = 5000          # requests per batch (API limit is higher; keeps payloads small)
MAX_TOKENS = 4096

SYSTEM = """You are an expert Persian (Farsi) copy editor proofreading a published book. You receive numbered paragraphs from one book. Report only genuine errors that a professional Persian editor would correct:
- spelling mistakes (misspelled words, wrong letters such as ز/ذ/ض/ظ or س/ص/ث or ت/ط or ه/ح confusions, missing or extra letters)
- grammar: subject-verb agreement, wrong verb form or tense, wrong preposition, broken or incomplete sentences, missing or duplicated words
- wrong word: a real word used where another was clearly intended
- punctuation that is wrong or changes the meaning

Do NOT report:
- half-space (نیم‌فاصله/ZWNJ) or spacing issues, Arabic vs Persian ی/ک, digit style, quotation-mark style (another tool handles these)
- stylistic preferences, or rewrites of sentences that are already correct
- intentional colloquial speech (محاوره) in dialogue, dialect, archaic, literary or poetic usage, or Arabic quotations
- proper names, foreign words, or technical terms you are not sure about

When unsure, do not report. Precision matters more than recall.

Output ONLY a JSON object, no other text:
{"issues":[{"p":<paragraph number>,"original":"<exact text copied character-for-character from that paragraph, 1-8 words, containing the error>","suggestion":"<corrected replacement for exactly that text>","type":"spelling|grammar|word_choice|punctuation","note":"<very short explanation in Persian>","confidence":<0.0-1.0>}]}
If there are no errors, output {"issues":[]}"""

_CHARMAP = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"})


def _client():
    try:
        import anthropic
    except ImportError:
        sys.exit("The 'anthropic' package is required: pip install anthropic")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first (export ANTHROPIC_API_KEY=sk-ant-...)")
    return anthropic.Anthropic(max_retries=6)


def build_chunks(out_dir, book_ids, max_chars=MAX_CHARS):
    """Yield (book_id, [(file, block_index, text), ...]) groups of paragraphs."""
    for bid in book_ids:
        cur, size = [], 0
        for f, blocks in store.load_blocks(out_dir, bid):
            for b in blocks:
                t = b["t"]
                if len(t) < 12 or not has_persian(t) or is_arabic_like(t):
                    continue
                if cur and size + len(t) > max_chars:
                    yield bid, cur
                    cur, size = [], 0
                cur.append((f, b["i"], t))
                size += len(t)
        if cur:
            yield bid, cur


def user_message(items, register=None):
    head = f"Book register/genre note: {register}\n\n" if register else ""
    body = "\n\n".join(f"[{n}] {t}" for n, (_f, _i, t) in enumerate(items, 1))
    return head + body


def request_params(model, items, register=None):
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": user_message(items, register)}],
    }


def parse_json(text):
    text = text.strip()
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        return []
    try:
        data = json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return []
    issues = data.get("issues", []) if isinstance(data, dict) else []
    return [x for x in issues if isinstance(x, dict)]


def verify(raw_issues, items, model, min_conf):
    """Keep only issues whose 'original' text really occurs in the paragraph."""
    kept, dropped = [], 0
    for x in raw_issues:
        try:
            p = int(x.get("p"))
            conf = float(x.get("confidence", 0.5))
        except (TypeError, ValueError):
            dropped += 1
            continue
        orig = str(x.get("original", "")).strip()
        sugg = str(x.get("suggestion", "")).strip()
        if not (1 <= p <= len(items)) or not orig or orig == sugg or conf < min_conf:
            dropped += 1
            continue
        f, i, t = items[p - 1]
        off = t.find(orig)
        if off < 0:
            off = t.translate(_CHARMAP).find(orig.translate(_CHARMAP))
        if off < 0:
            dropped += 1
            continue
        typ = str(x.get("type", "grammar"))
        kept.append({
            "f": f, "i": i, "off": off, "len": len(orig), "orig": t[off:off + len(orig)],
            "sugg": sugg, "rule": "llm_" + typ, "severity": "review",
            "category": "spelling" if typ == "spelling" else "grammar",
            "conf": round(conf, 2), "msg_fa": str(x.get("note", "")), "msg_en": "",
            "model": model,
        })
    return kept, dropped


# ---------- run bookkeeping ----------

def gdir(out_dir):
    d = Path(out_dir) / "grammar"
    (d / "results").mkdir(parents=True, exist_ok=True)
    return d


def _next_cid(out_dir):
    m = gdir(out_dir) / "manifest.jsonl"
    if not m.exists():
        return 0
    with open(m, encoding="utf-8") as fh:
        return sum(1 for _ in fh)


def _load_registers(path):
    if not path:
        return {}
    import csv
    reg = {}
    with open(path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            key = (row.get("file") or row.get("book") or "").strip()
            if key:
                reg[key] = (row.get("register") or "").strip()
    return reg


def _register_for(book, registers):
    return registers.get(book["rel"]) or registers.get(Path(book["rel"]).name) or registers.get(book["id"])


def _select_books(out_dir, only=None, limit=None):
    books = [b for b in store.load_books(out_dir) if not b.get("error")]
    if only:
        wanted = set(only)
        books = [b for b in books if b["id"] in wanted or b["rel"] in wanted
                 or Path(b["rel"]).name in wanted]
    if limit:
        books = books[:limit]
    return books


def _prepare(out_dir, books, model, registers, max_chars):
    reg = _load_registers(registers)
    n = _next_cid(out_dir)
    reqs, manifest = [], []
    by_id = {b["id"]: b for b in books}
    for bid, items in build_chunks(out_dir, list(by_id), max_chars):
        cid = f"c{n:07d}"
        n += 1
        reqs.append({"custom_id": cid,
                     "params": request_params(model, items, _register_for(by_id[bid], reg))})
        manifest.append({"cid": cid, "book": bid, "items": [[f, i] for f, i, _ in items]})
    return reqs, manifest


def _append_manifest(out_dir, manifest):
    with open(gdir(out_dir) / "manifest.jsonl", "a", encoding="utf-8") as fh:
        for m in manifest:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")


def _batches_file(out_dir):
    return gdir(out_dir) / "batches.json"


def _load_batches(out_dir):
    p = _batches_file(out_dir)
    return json.loads(p.read_text()) if p.exists() else []


def _save_batches(out_dir, batches):
    _batches_file(out_dir).write_text(json.dumps(batches, indent=2))


# ---------- commands ----------

def estimate(out_dir, model, only=None, limit=None, sample=25, registers=None,
             max_chars=MAX_CHARS, price_in=None, price_out=None):
    books = _select_books(out_dir, only, limit)
    reqs, _ = _prepare(out_dir, books, model, registers, max_chars)
    if not reqs:
        print("Nothing to send.")
        return
    total_chars = sum(len(r["params"]["messages"][0]["content"]) for r in reqs)
    client = _client()
    step = max(1, len(reqs) // sample)
    sample_reqs = reqs[::step][:sample]
    tok = chars = 0
    for r in sample_reqs:
        p = r["params"]
        tok += client.messages.count_tokens(model=p["model"], system=p["system"],
                                            messages=p["messages"]).input_tokens
        chars += len(p["messages"][0]["content"])
    per_req_overhead = 0  # included in the counted tokens
    est_in = int(tok / len(sample_reqs) * len(reqs)) + per_req_overhead
    out_lo, out_hi = 60 * len(reqs), 600 * len(reqs)
    print(f"Books: {len(books)}   requests: {len(reqs):,}   text: {total_chars:,} chars")
    print(f"Input tokens (measured on {len(sample_reqs)} samples, extrapolated): ~{est_in:,}")
    print(f"Output tokens (depends on how many errors are found): ~{out_lo:,} – {out_hi:,}")
    if price_in and price_out:
        # Batch API is billed at a discount; pass the prices you want to model
        lo = est_in / 1e6 * price_in + out_lo / 1e6 * price_out
        hi = est_in / 1e6 * price_in + out_hi / 1e6 * price_out
        print(f"Estimated cost at ${price_in}/M in, ${price_out}/M out: ${lo:,.0f} – ${hi:,.0f}")
    else:
        print("Pass --price-in / --price-out (USD per million tokens, from the pricing page) for a cost figure.")


def submit(out_dir, model, only=None, limit=None, registers=None, max_chars=MAX_CHARS,
           dry_run=False):
    books = _select_books(out_dir, only, limit)
    reqs, manifest = _prepare(out_dir, books, model, registers, max_chars)
    if not reqs:
        print("Nothing to send.")
        return
    if dry_run:
        p = gdir(out_dir) / "dry_run_requests.jsonl"
        with open(p, "w", encoding="utf-8") as fh:
            for r in reqs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"Dry run: {len(reqs):,} requests for {len(books)} books written to {p}")
        return
    client = _client()
    _append_manifest(out_dir, manifest)
    batches = _load_batches(out_dir)
    for k in range(0, len(reqs), PER_BATCH):
        part = reqs[k:k + PER_BATCH]
        b = client.messages.batches.create(requests=part)
        batches.append({"id": b.id, "n": len(part), "model": model,
                        "created": time.strftime("%Y-%m-%d %H:%M:%S"), "collected": False})
        _save_batches(out_dir, batches)
        print(f"Submitted batch {b.id} ({len(part):,} requests)")
    print("Check progress with:  python -m persian_proof grammar status -o", out_dir)


def status(out_dir):
    batches = _load_batches(out_dir)
    if not batches:
        print("No batches submitted.")
        return
    client = _client()
    for b in batches:
        r = client.messages.batches.retrieve(b["id"])
        c = r.request_counts
        print(f"{b['id']}  {r.processing_status:<11} ok={c.succeeded} err={c.errored} "
              f"processing={c.processing} expired={c.expired}  collected={b.get('collected')}")


def _write_results(path, rows):
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def collect(out_dir, min_conf=0.6, quiet=False):
    """Download finished batch results, then rebuild grammar issues for every book."""
    batches = _load_batches(out_dir)
    if batches and any(not b.get("collected") for b in batches):
        client = _client()
        for b in batches:
            if b.get("collected"):
                continue
            r = client.messages.batches.retrieve(b["id"])
            if r.processing_status != "ended":
                print(f"{b['id']} still {r.processing_status}; collect again later.")
                continue
            rows = []
            for entry in client.messages.batches.results(b["id"]):
                res = entry.result
                if res.type == "succeeded":
                    text = "".join(getattr(c, "text", "") for c in res.message.content)
                    rows.append({"cid": entry.custom_id, "text": text,
                                 "in": res.message.usage.input_tokens,
                                 "out": res.message.usage.output_tokens})
                else:
                    rows.append({"cid": entry.custom_id, "error": res.type})
            _write_results(gdir(out_dir) / "results" / f"{b['id']}.jsonl", rows)
            b["collected"] = True
            _save_batches(out_dir, batches)
            print(f"Collected {len(rows):,} results from {b['id']}")
    return rebuild_issues(out_dir, min_conf, quiet)


def rebuild_issues(out_dir, min_conf=0.6, quiet=False):
    g = gdir(out_dir)
    man = {}
    mp = g / "manifest.jsonl"
    if not mp.exists():
        print("No grammar requests yet.")
        return
    with open(mp, encoding="utf-8") as fh:
        for line in fh:
            m = json.loads(line)
            man[m["cid"]] = m
    results = {}
    tin = tout = errors = 0
    for rp in sorted((g / "results").glob("*.jsonl")):
        with open(rp, encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                if "error" in r:
                    errors += 1
                    continue
                results[r["cid"]] = r
                tin += r.get("in", 0)
                tout += r.get("out", 0)
    text_cache = {}

    def block_text(bid):
        if bid not in text_cache:
            text_cache.clear()
            text_cache[bid] = {(f, b["i"]): b["t"] for f, bl in store.load_blocks(out_dir, bid)
                               for b in bl}
        return text_cache[bid]

    per_book, dropped, kept_n = {}, 0, 0
    for cid in sorted(results, key=lambda c: (man.get(c, {}).get("book", ""), c)):
        m = man.get(cid)
        if not m:
            continue
        texts = block_text(m["book"])
        items = [(f, i, texts.get((f, i), "")) for f, i in m["items"]]
        kept, d = verify(parse_json(results[cid]["text"]), items, "claude", min_conf)
        dropped += d
        kept_n += len(kept)
        per_book.setdefault(m["book"], []).extend(kept)
    for bid, issues in per_book.items():
        store.write_issues(out_dir, bid, "grammar", issues)
    if not quiet:
        print(f"Grammar issues kept: {kept_n:,}   dropped (unverifiable/low confidence): {dropped:,}"
              f"   failed requests: {errors}")
        print(f"Tokens used so far: {tin:,} in / {tout:,} out")
    return per_book


def pilot(out_dir, model, only=None, limit=3, registers=None, max_chars=MAX_CHARS,
          concurrency=4, min_conf=0.6):
    """Run the grammar pass synchronously on a few books (no batch wait)."""
    books = _select_books(out_dir, only, limit)
    reqs, manifest = _prepare(out_dir, books, model, registers, max_chars)
    if not reqs:
        print("Nothing to send.")
        return
    client = _client()
    _append_manifest(out_dir, manifest)
    out_path = gdir(out_dir) / "results" / f"pilot-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    print(f"Pilot: {len(reqs)} requests for {len(books)} books with {model}")

    def run(r):
        try:
            msg = client.messages.create(**r["params"])
            text = "".join(getattr(c, "text", "") for c in msg.content)
            return {"cid": r["custom_id"], "text": text,
                    "in": msg.usage.input_tokens, "out": msg.usage.output_tokens}
        except Exception as e:  # keep going; failures are reported
            return {"cid": r["custom_id"], "error": f"{type(e).__name__}: {e}"}

    rows = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(run, r) for r in reqs]
        for k, fut in enumerate(as_completed(futs), 1):
            rows.append(fut.result())
            if k % 10 == 0 or k == len(futs):
                print(f"  {k}/{len(futs)}")
    _write_results(out_path, rows)
    return rebuild_issues(out_dir, min_conf)

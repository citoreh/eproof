"""Scan pipeline: extract text, typography rules, corpus spelling."""
import csv
import os
import time
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

from . import store
from .epub_io import Epub, EpubError, book_id_for, iter_book_blocks
from .rules import find_issues
from .spelling import (count_book, detect_typos, find_spelling_issues, load_wordlist,
                       tokens)

SAFE_EXAMPLES = 15  # safe fixes are counted; only a few examples are kept per rule

WHITELIST_HEADER = """# One word per line. Words listed here are never flagged as spelling errors.
# Add names, rare vocabulary, and anything the checker got wrong, then re-run 'scan'.
"""


def find_epubs(input_dir):
    root = Path(input_dir)
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".epub")
    return [(str(p), str(p.relative_to(root))) for p in files]


def _pass_a(args):
    path, rel, out_dir, prev = args
    bid = book_id_for(rel)
    st = os.stat(path)
    meta = {"id": bid, "rel": rel, "path": path, "size": st.st_size, "mtime": int(st.st_mtime)}
    try:
        cached = (prev and prev.get("size") == meta["size"] and prev.get("mtime") == meta["mtime"]
                  and not prev.get("error")
                  and (Path(out_dir) / "blocks" / f"{bid}.json.gz").exists())
        if cached:
            blocks_by_file = store.load_blocks(out_dir, bid)
            meta["title"], meta["spine"] = prev.get("title", ""), prev.get("spine", [])
        else:
            with Epub(path) as ep:
                meta["title"], meta["spine"] = ep.title, ep.spine
                blocks_by_file = [[f, bl] for f, bl in iter_book_blocks(ep)]
            store.save_blocks(out_dir, bid, blocks_by_file)
    except (EpubError, OSError, KeyError, ValueError) as e:
        meta["error"] = f"{type(e).__name__}: {e}"
        return meta, {}

    issues, safe_counts, examples = [], Counter(), Counter()
    words = 0
    for f, blocks in blocks_by_file:
        for b in blocks:
            words += sum(1 for _ in tokens(b["t"]))
            for h in find_issues(b["t"]):
                if h["severity"] == "safe":
                    safe_counts[h["rule"]] += 1
                    if examples[h["rule"]] >= SAFE_EXAMPLES:
                        continue
                    examples[h["rule"]] += 1
                h.update(f=f, i=b["i"])
                issues.append(h)
    store.write_issues(out_dir, bid, "rules", issues)
    meta["words"] = words
    meta["safe"] = dict(safe_counts)
    meta["title"] = meta.get("title") or Path(rel).stem
    counts = count_book(blocks_by_file)
    return meta, counts


_FLAGGED = None


def _init_b(flagged):
    global _FLAGGED
    _FLAGGED = flagged


def _pass_b(args):
    out_dir, bid = args
    issues = find_spelling_issues(store.load_blocks(out_dir, bid), _FLAGGED)
    store.write_issues(out_dir, bid, "spelling", issues)
    return bid, len(issues)


def scan(input_dir, out_dir, workers=None, dicts=None, rare_max=3, rare_books=2,
         freq_min=30, ratio=40, min_len=4, limit=None):
    t0 = time.time()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    wl = out / "whitelist.txt"
    if not wl.exists():
        wl.write_text(WHITELIST_HEADER, encoding="utf-8")

    epubs = find_epubs(input_dir)
    if limit:
        epubs = epubs[:limit]
    if not epubs:
        raise SystemExit(f"No .epub files found under {input_dir}")
    try:
        prev = {b["id"]: b for b in store.load_books(out_dir)}
    except SystemExit:
        prev = {}
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    print(f"Found {len(epubs)} ePubs. Pass 1/2: text extraction + typography ({workers} workers)")

    books, tf, df = [], Counter(), Counter()
    jobs = [(p, r, str(out), prev.get(book_id_for(r))) for p, r in epubs]
    with Pool(workers) as pool:
        for k, (meta, counts) in enumerate(pool.imap_unordered(_pass_a, jobs, chunksize=2), 1):
            books.append(meta)
            tf.update(counts)
            df.update(counts.keys())
            if k % 25 == 0 or k == len(jobs):
                print(f"  {k}/{len(jobs)}")
    books.sort(key=lambda b: b["rel"])
    store.save_books(out_dir, books)
    bad = [b for b in books if b.get("error")]
    for b in bad:
        print(f"  ! could not read {b['rel']}: {b['error']}")

    print("Building corpus word statistics…")
    known = load_wordlist((dicts or []) + [str(wl)])
    flagged = detect_typos(tf, df, known, rare_max=rare_max, rare_books=rare_books,
                           freq_min=freq_min, ratio=ratio, min_len=min_len)
    store.write_json(out_dir, "corpus_stats.json", {
        "books": len(books) - len(bad), "tokens": sum(tf.values()), "types": len(tf),
        "known_words": len(known),
        "thresholds": {"rare_max": rare_max, "rare_books": rare_books, "freq_min": freq_min,
                       "ratio": ratio, "min_len": min_len},
    })
    with open(out / "spelling_candidates.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["word", "suggestion", "kind", "count", "suggestion_count", "confidence"])
        for word, h in sorted(flagged.items(), key=lambda kv: -kv[1]["conf"]):
            w.writerow([word, h["sugg"], h["kind"], h["tf"], h["sugg_tf"], h["conf"]])
    print(f"  {sum(tf.values()):,} words, {len(tf):,} distinct; {len(flagged):,} suspicious forms")

    print("Pass 2/2: locating spelling suspects in each book")
    ok_ids = [b["id"] for b in books if not b.get("error")]
    counts = {}
    with Pool(workers, initializer=_init_b, initargs=(flagged,)) as pool:
        for bid, n in pool.imap_unordered(_pass_b, [(str(out), i) for i in ok_ids], chunksize=4):
            counts[bid] = n
    print(f"Scan finished in {time.time() - t0:.0f}s")
    return books

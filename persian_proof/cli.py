"""Command-line interface.

    python -m persian_proof scan    ./epubs -o ./proof
    python -m persian_proof grammar estimate -o ./proof
    python -m persian_proof grammar pilot    -o ./proof --books 3
    python -m persian_proof grammar submit   -o ./proof
    python -m persian_proof grammar status   -o ./proof
    python -m persian_proof grammar collect  -o ./proof
    python -m persian_proof fix     -o ./proof
    python -m persian_proof report  -o ./proof
"""
import argparse
import os
from multiprocessing import Pool
from pathlib import Path

from . import grammar, report, scan, store
from .fixer import write_fixed_epub


def _fix_one(args):
    b, dest = args
    target = Path(dest) / b["rel"]
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        return b["id"], write_fixed_epub(b["path"], target, b.get("spine", [])), None
    except Exception as e:
        return b["id"], {}, f"{type(e).__name__}: {e}"


def cmd_fix(a):
    books = [b for b in store.load_books(a.out) if not b.get("error")]
    dest = Path(a.dest or Path(a.out) / "fixed")
    log = {}
    with Pool(a.workers or max(1, (os.cpu_count() or 2) - 1)) as pool:
        for bid, counts, err in pool.imap_unordered(_fix_one, [(b, str(dest)) for b in books]):
            if err:
                print(f"  ! {bid}: {err}")
            else:
                log[bid] = counts
    store.write_json(a.out, "fix_log.json", log)
    print(f"Corrected copies of {len(log)} books in {dest}  "
          f"({sum(sum(c.values()) for c in log.values()):,} safe fixes)")


def main(argv=None):
    p = argparse.ArgumentParser(prog="persian_proof",
                                description="Spelling, grammar and typography checks for Persian ePubs")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="extract text, run typography rules and corpus spelling check")
    s.add_argument("input", help="folder containing .epub files (searched recursively)")
    s.add_argument("-o", "--out", required=True, help="output folder")
    s.add_argument("--dict", action="append", default=[],
                   help="word list or hunspell .dic of known-good words (repeatable)")
    s.add_argument("--workers", type=int)
    s.add_argument("--limit", type=int, help="only the first N books (for a trial)")
    s.add_argument("--rare-max", type=int, default=3, help="max corpus count for a suspect word")
    s.add_argument("--rare-books", type=int, default=2, help="max number of books a suspect word appears in")
    s.add_argument("--freq-min", type=int, default=30, help="min corpus count for the 'correct' form")
    s.add_argument("--ratio", type=int, default=40, help="how many times more common the correct form must be")
    s.add_argument("--min-len", type=int, default=4, help="ignore words shorter than this")

    g = sub.add_parser("grammar", help="Claude grammar pass")
    g.add_argument("action", choices=["estimate", "pilot", "submit", "status", "collect"])
    g.add_argument("-o", "--out", required=True)
    g.add_argument("--model", default=grammar.DEFAULT_MODEL)
    g.add_argument("--books", type=int, help="limit to the first N books")
    g.add_argument("--only", nargs="*", help="book ids or file names to include")
    g.add_argument("--registers", help="CSV with columns file,register (e.g. 'classical poetry')")
    g.add_argument("--max-chars", type=int, default=grammar.MAX_CHARS)
    g.add_argument("--min-conf", type=float, default=0.6)
    g.add_argument("--concurrency", type=int, default=4)
    g.add_argument("--dry-run", action="store_true", help="write requests to a file instead of sending")
    g.add_argument("--price-in", type=float, help="USD per million input tokens (for estimate)")
    g.add_argument("--price-out", type=float, help="USD per million output tokens (for estimate)")

    f = sub.add_parser("fix", help="write corrected ePub copies with the safe fixes applied")
    f.add_argument("-o", "--out", required=True)
    f.add_argument("--dest", help="where to write corrected ePubs (default: OUT/fixed)")
    f.add_argument("--workers", type=int)

    r = sub.add_parser("report", help="build the HTML and CSV reports")
    r.add_argument("-o", "--out", required=True)
    r.add_argument("--min-conf", type=float, default=0.0)

    a = p.parse_args(argv)
    if a.cmd == "scan":
        scan.scan(a.input, a.out, workers=a.workers, dicts=a.dict, rare_max=a.rare_max,
                  rare_books=a.rare_books, freq_min=a.freq_min, ratio=a.ratio,
                  min_len=a.min_len, limit=a.limit)
        report.build(a.out)
    elif a.cmd == "grammar":
        kw = dict(only=a.only, limit=a.books, registers=a.registers, max_chars=a.max_chars)
        if a.action == "estimate":
            grammar.estimate(a.out, a.model, price_in=a.price_in, price_out=a.price_out, **kw)
        elif a.action == "pilot":
            kw["limit"] = a.books or 3
            grammar.pilot(a.out, a.model, concurrency=a.concurrency, min_conf=a.min_conf, **kw)
            report.build(a.out)
        elif a.action == "submit":
            grammar.submit(a.out, a.model, dry_run=a.dry_run, **kw)
        elif a.action == "status":
            grammar.status(a.out)
        elif a.action == "collect":
            grammar.collect(a.out, min_conf=a.min_conf)
            report.build(a.out)
    elif a.cmd == "fix":
        cmd_fix(a)
        report.build(a.out)
    elif a.cmd == "report":
        report.build(a.out, min_conf=a.min_conf)


if __name__ == "__main__":
    main()

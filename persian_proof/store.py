"""On-disk layout of a run (everything lives under the output folder)."""
import gzip
import json
from pathlib import Path


def _p(out_dir, *parts):
    p = Path(out_dir).joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def save_books(out_dir, books):
    _p(out_dir, "books.json").write_text(json.dumps(books, ensure_ascii=False, indent=1),
                                         encoding="utf-8")


def load_books(out_dir):
    p = Path(out_dir) / "books.json"
    if not p.exists():
        raise SystemExit(f"No scan found in {out_dir}. Run the 'scan' command first.")
    return json.loads(p.read_text(encoding="utf-8"))


def save_blocks(out_dir, bid, blocks_by_file):
    with gzip.open(_p(out_dir, "blocks", f"{bid}.json.gz"), "wt", encoding="utf-8") as fh:
        json.dump(blocks_by_file, fh, ensure_ascii=False)


def load_blocks(out_dir, bid):
    with gzip.open(Path(out_dir) / "blocks" / f"{bid}.json.gz", "rt", encoding="utf-8") as fh:
        return json.load(fh)


def write_issues(out_dir, bid, source, issues):
    with open(_p(out_dir, "issues", f"{bid}.{source}.jsonl"), "w", encoding="utf-8") as fh:
        for x in issues:
            fh.write(json.dumps(x, ensure_ascii=False) + "\n")


def load_issues(out_dir, bid, source):
    p = Path(out_dir) / "issues" / f"{bid}.{source}.jsonl"
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_json(out_dir, name, data):
    _p(out_dir, name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def read_json(out_dir, name, default=None):
    p = Path(out_dir) / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

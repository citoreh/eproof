"""Corpus-based spelling check.

With ~1,000 books, the corpus itself is the best dictionary: a word that
appears only once or twice across everything, and is one edit away from a
word that appears hundreds of times, is almost certainly a typo.
"""
import math
import re
import unicodedata
from collections import Counter, defaultdict

from rapidfuzz.distance import OSA, Levenshtein

from .rules import ZWNJ, _L, is_arabic_like

TOKEN_RE = re.compile(f"[{_L}‌ً-ٰٟـﭐ-﷿ﹰ-ﻼ]+")
_CHARMAP = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ھ": "ه", "ە": "ه"})
_STRIP = re.compile("[ً-ٰٟـ]")

SUFFIX_CHARS = set("مشتینهاد")
PREFIX_CHARS = set("بنمی")


def norm_word(w: str) -> str:
    if any("ﭐ" <= c <= "ﻼ" for c in w):
        w = unicodedata.normalize("NFKC", w)
    w = _STRIP.sub("", w.translate(_CHARMAP))
    w = re.sub("‌{2,}", ZWNJ, w).strip(ZWNJ)
    return w


def tokens(text):
    """Yield (start, end, raw, normalised) for Persian words in text."""
    for m in TOKEN_RE.finditer(text):
        raw = m.group(0)
        s, e = m.span()
        while raw.startswith(ZWNJ):
            raw, s = raw[1:], s + 1
        while raw.endswith(ZWNJ):
            raw, e = raw[:-1], e - 1
        if not raw:
            continue
        n = norm_word(raw)
        if n:
            yield s, e, raw, n


def count_book(blocks_by_file):
    c = Counter()
    for _f, blocks in blocks_by_file:
        for b in blocks:
            if is_arabic_like(b["t"]):
                continue
            for _s, _e, _raw, n in tokens(b["t"]):
                c[n] += 1
    return c


def load_wordlist(paths):
    words = set()
    for p in paths or []:
        with open(p, encoding="utf-8", errors="ignore") as fh:
            for i, line in enumerate(fh):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # hunspell .dic: first line is a count, entries are word/FLAGS
                if i == 0 and line.isdigit():
                    continue
                w = norm_word(line.split("/")[0].split("\t")[0].strip())
                if w:
                    words.add(w)
    return words


def _deletes(w):
    return {w[:i] + w[i + 1:] for i in range(len(w))}


def _is_morph_variant(w, cand):
    """True if the one-letter difference looks like normal Persian inflection."""
    if ZWNJ in w or ZWNJ in cand:
        a, b = w.replace(ZWNJ, ""), cand.replace(ZWNJ, "")
        if a == b:
            return True
    if Levenshtein.distance(w, cand) != 1:
        return False  # transposition: treat as typo
    (op, i, j), = Levenshtein.editops(w, cand)
    if op == "replace":
        last = i == len(w) - 1 and j == len(cand) - 1
        first = i == 0 and j == 0
        if last and w[i] in SUFFIX_CHARS and cand[j] in SUFFIX_CHARS:
            return True
        if first and w[i] in PREFIX_CHARS and cand[j] in PREFIX_CHARS:
            return True
        return False
    ch = w[i] if op == "delete" else cand[j]
    pos, length = (i, len(w)) if op == "delete" else (j, len(cand))
    if ch == ZWNJ:
        return True
    if pos >= length - 1 and ch in SUFFIX_CHARS:
        return True
    if pos == 0 and ch in PREFIX_CHARS:
        return True
    return False


def detect_typos(tf: Counter, df: Counter, known=frozenset(), *, rare_max=3, rare_books=2,
                 freq_min=30, ratio=40, min_len=4):
    """Return {word: {"sugg", "kind", "conf", "tf", "sugg_tf"}}."""
    flagged = {}

    # 1) ZWNJ inconsistency: same letters, different half-space placement
    groups = defaultdict(list)
    for w in tf:
        groups[w.replace(ZWNJ, "")].append(w)
    for forms in groups.values():
        if len(forms) < 2:
            continue
        dom = max(forms, key=tf.__getitem__)
        for f in forms:
            if f == dom or f in known:
                continue
            if tf[dom] >= 20 and tf[dom] >= 5 * tf[f]:
                share = tf[dom] / (tf[dom] + tf[f])
                flagged[f] = {"sugg": dom, "kind": "zwnj_variant",
                              "conf": round(min(0.95, share), 2),
                              "tf": tf[f], "sugg_tf": tf[dom]}

    # 2) rare word one edit from a frequent word
    index = defaultdict(list)
    for w, c in tf.items():
        if c >= freq_min and len(w) >= min_len - 1:
            index[w].append(w)
            for d in _deletes(w):
                index[d].append(w)
    for w, c in tf.items():
        if (c > rare_max or df.get(w, 0) > rare_books or w in known or w in flagged
                or len(w.replace(ZWNJ, "")) < min_len):
            continue
        cands = set(index.get(w, ()))
        for d in _deletes(w):
            cands.update(index.get(d, ()))
        best = None
        for cand in cands:
            if cand == w or tf[cand] < ratio * c:
                continue
            if OSA.distance(w, cand) != 1 or _is_morph_variant(w, cand):
                continue
            if best is None or tf[cand] > tf[best]:
                best = cand
        if best:
            r = tf[best] / c
            flagged[w] = {"sugg": best, "kind": "corpus_typo",
                          "conf": round(min(0.95, 0.45 + math.log10(r) / 6), 2),
                          "tf": c, "sugg_tf": tf[best]}
    return flagged


MSG = {
    "corpus_typo": ("احتمالاً غلط املایی؛ شکل رایج در کل مجموعه: «{s}» ({n} بار)",
                    "Likely typo; common form across the corpus: {s} ({n}×)"),
    "zwnj_variant": ("نیم‌فاصله ناهماهنگ با بقیه‌ی مجموعه؛ شکل رایج: «{s}» ({n} بار)",
                     "ZWNJ spacing inconsistent with the rest of the corpus; common form: {s} ({n}×)"),
}


def find_spelling_issues(blocks_by_file, flagged):
    out = []
    for f, blocks in blocks_by_file:
        for b in blocks:
            if is_arabic_like(b["t"]):
                continue
            for s, e, raw, n in tokens(b["t"]):
                hit = flagged.get(n)
                if not hit:
                    continue
                fa, en = MSG[hit["kind"]]
                out.append({
                    "f": f, "i": b["i"], "off": s, "len": e - s, "orig": raw,
                    "sugg": hit["sugg"], "rule": hit["kind"], "severity": "review",
                    "category": "spelling" if hit["kind"] == "corpus_typo" else "typography",
                    "conf": hit["conf"],
                    "msg_fa": fa.format(s=hit["sugg"], n=hit["sugg_tf"]),
                    "msg_en": en.format(s=hit["sugg"], n=hit["sugg_tf"]),
                })
    return out

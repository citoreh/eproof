"""Deterministic Persian typography rules.

Each rule has a severity:
  safe    - applied automatically to the corrected ePub copies
  review  - reported only; a person decides
  style   - reported only, as a house-style note (digits, quote marks)
"""
import re
import unicodedata

ZWNJ = "‌"

# Persian/Arabic letters (no digits, no punctuation, no diacritics)
_L = (
    "ء-غف-يٮ-ٯٱ-ۓە"
    "ۮ-ۯۺ-ۼۿ"
)
PL = f"[{_L}]"
NOT_WORD_BEFORE = f"(?<![{_L}‌])"
NOT_WORD_AFTER = f"(?![{_L}‌])"

HARAKAT_RE = re.compile("[ً-ٰٟ]")
LETTER_RE = re.compile(PL)
LATIN_RE = re.compile(r"[A-Za-z]")


def is_arabic_like(text: str) -> bool:
    """Heavily diacritised or Arabic-looking text (Quran/hadith quotes, Arabic verse).

    Typography rules are skipped for these, because Arabic spelling
    conventions (ي, ة, harakat) are correct there.
    """
    letters = len(LETTER_RE.findall(text))
    if letters == 0:
        return False
    if len(HARAKAT_RE.findall(text)) / letters > 0.12:
        return True
    if text.count("ة") >= 2:
        return True
    return False


def has_persian(text: str) -> bool:
    return LETTER_RE.search(text) is not None


class Rule:
    def __init__(self, rid, pattern, repl, severity, category, fa, en, when=None):
        self.id = rid
        self.re = re.compile(pattern)
        self.repl = repl
        self.severity = severity
        self.category = category
        self.fa = fa
        self.en = en
        self.when = when  # optional filter(text, match) -> bool

    def suggestion(self, m):
        if callable(self.repl):
            return self.repl(m)
        return m.expand(self.repl)

    def sub(self, text):
        n = 0

        def _r(m):
            nonlocal n
            if self.when and not self.when(m.string, m):
                return m.group(0)
            new = self.suggestion(m)
            if new != m.group(0):
                n += 1
            return new

        return self.re.sub(_r, text), n


def _persian_near(text, m, span=2):
    before = text[max(0, m.start() - span): m.start()]
    after = text[m.end(): m.end() + span]
    return bool(LETTER_RE.search(before) or LETTER_RE.search(after)) and not (
        LATIN_RE.search(before) or LATIN_RE.search(after)
    )


def _has_persian_inside(text, m):
    return has_persian(m.group(1)) and not LATIN_RE.search(m.group(1))


PS = "[  ]"  # plain spaces (block text is already whitespace-collapsed)

RULES = [
    # --- character normalisation (safe) ---
    Rule("bom", "﻿", "", "safe", "typography",
         "نویسه‌ی نامرئی BOM", "Stray byte-order mark"),
    Rule("presentation_forms", "[ﭐ-﷿ﹰ-ﻼ]+",
         lambda m: unicodedata.normalize("NFKC", m.group(0)), "safe", "typography",
         "حروف نمایشی (Presentation Forms) به‌جای حروف استاندارد",
         "Arabic presentation-form characters instead of standard letters"),
    Rule("arabic_yeh", "[يى]", "ی", "safe", "typography",
         "«ي» عربی به‌جای «ی» فارسی", "Arabic yeh instead of Persian yeh"),
    Rule("arabic_kaf", "ك", "ک", "safe", "typography",
         "«ك» عربی به‌جای «ک» فارسی", "Arabic kaf instead of Persian kaf"),
    Rule("other_heh", "[ھە]", "ه", "safe", "typography",
         "نویسه‌ی «ه» نادرست", "Wrong heh code point"),
    Rule("arabic_digits", "[٠-٩]",
         lambda m: chr(ord(m.group(0)) - 0x0660 + 0x06F0), "safe", "typography",
         "رقم عربی به‌جای رقم فارسی", "Arabic-Indic digit instead of Persian digit"),

    # --- punctuation (safe) ---
    Rule("latin_comma", f"(?<={PL}){PS}*,", "،", "safe", "typography",
         "ویرگول لاتین به‌جای «،»", "Latin comma after Persian word"),
    Rule("latin_question", rf"(?<={PL}){PS}*\?", "؟", "safe", "typography",
         "علامت سؤال لاتین به‌جای «؟»", "Latin question mark after Persian word"),
    Rule("latin_semicolon", f"(?<={PL}){PS}*;", "؛", "safe", "typography",
         "نقطه‌ویرگول لاتین به‌جای «؛»", "Latin semicolon after Persian word"),
    Rule("space_before_punct", rf"(?<={PL}){PS}+(?=[،؛؟!:]|\.(?!\.))", "", "safe", "typography",
         "فاصله‌ی اضافه پیش از نشانه‌ی سجاوندی", "Space before punctuation"),
    Rule("space_after_punct", f"([،؛؟!])(?={PL})", r"\1 ", "safe", "typography",
         "نبودِ فاصله پس از نشانه‌ی سجاوندی", "Missing space after punctuation"),

    # --- ZWNJ (half-space) ---
    Rule("zwnj_duplicate", "‌{2,}", ZWNJ, "safe", "typography",
         "نیم‌فاصله‌ی تکراری", "Repeated ZWNJ"),
    Rule("zwnj_dangling", f"‌+(?!{PL})|(?<!{PL})‌+", "", "safe", "typography",
         "نیم‌فاصله‌ی بی‌جا (کنار فاصله یا نشانه)", "ZWNJ next to a space or punctuation"),
    Rule("zwnj_mi", f"{NOT_WORD_BEFORE}(ن?می){PS}(?={PL}+[میدت]{NOT_WORD_AFTER})",
         r"\1" + ZWNJ, "safe", "typography",
         "«می/نمی» باید با نیم‌فاصله به فعل بچسبد", "Verb prefix می/نمی needs a ZWNJ, not a space"),
    Rule("zwnj_ha", f"(?<={PL}){PS}(ها|های|هایی|هایم|هایت|هایش|هایمان|هایتان|هایشان){NOT_WORD_AFTER}",
         ZWNJ + r"\1", "safe", "typography",
         "پسوند جمع «ها» باید با نیم‌فاصله بچسبد", "Plural suffix ها needs a ZWNJ, not a space"),

    # --- review only ---
    Rule("zwnj_mi_other", f"{NOT_WORD_BEFORE}(ن?می){PS}(?={PL}{{2,}})",
         r"\1" + ZWNJ, "review", "typography",
         "احتمالاً «می» پیشوند فعل است و نیم‌فاصله می‌خواهد (اگر «می» به معنای شراب نیست)",
         "Probably verb prefix می needing ZWNJ (unless it means 'wine')"),
    Rule("zwnj_tar", f"(?<={PL}){PS}(تر|ترین){NOT_WORD_AFTER}", ZWNJ + r"\1", "review", "typography",
         "پسوند «تر/ترین» معمولاً با نیم‌فاصله می‌آید (مگر «تر» به معنای خیس)",
         "Comparative تر/ترین usually takes a ZWNJ (unless تر means 'wet')"),
    Rule("zwnj_after_heh", f"(?<={PL}ه){PS}(ام|ای|ایم|اید|اند){NOT_WORD_AFTER}", ZWNJ + r"\1",
         "review", "typography",
         "پس از «ه» پایانی، «ام/ای/اند…» با نیم‌فاصله می‌آید",
         "After final ه, the clitic ام/ای/اند takes a ZWNJ"),
    Rule("repeated_word",
         f"{NOT_WORD_BEFORE}(از|به|در|که|را|با|این|آن|تا|برای|یک){PS}+\\1{NOT_WORD_AFTER}",
         r"\1", "review", "grammar",
         "واژه‌ی تکراری", "Repeated word"),
    Rule("period_no_space", f"(?<={PL})\\.(?={PL})", ". ", "review", "typography",
         "نبودِ فاصله پس از نقطه (مگر در اختصار)", "Missing space after full stop (unless abbreviation)"),
    Rule("tatweel", "ـ+", "", "review", "typography",
         "کشیده (ـ) در متن", "Tatweel/kashida in running text"),

    # --- house style ---
    Rule("latin_digits", "[0-9]+",
         lambda m: m.group(0).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")),
         "style", "style", "رقم لاتین در متن فارسی", "Latin digits in Persian text",
         when=_persian_near),
    Rule("latin_quotes", r'"([^"\n]{1,300})"', r"«\1»", "style", "style",
         "گیومه‌ی لاتین به‌جای «»", "Latin quotes around Persian text",
         when=_has_persian_inside),
]

SAFE_RULES = [r for r in RULES if r.severity == "safe"]
ONE_TO_ONE = {"arabic_yeh", "arabic_kaf", "other_heh", "arabic_digits"}
RULES_BY_ID = {r.id: r for r in RULES}


def find_issues(text: str):
    """Return rule hits in a block of text (non-overlapping, earliest rule wins)."""
    if is_arabic_like(text) or not has_persian(text):
        return []
    hits = []
    taken = []
    # One-to-one character fixes are found on the original text; the other
    # rules run on a copy with those applied, so e.g. «مي خواند» still gets
    # its ZWNJ hit. Offsets stay valid because lengths don't change.
    work = text
    for rule in RULES:
        target = text if rule.id in ONE_TO_ONE else work
        for m in rule.re.finditer(target):
            if rule.when and not rule.when(target, m):
                continue
            s, e = m.span()
            if rule.id not in ONE_TO_ONE and any(
                s < te and ts < e or (s == e == ts) for ts, te in taken
            ):
                continue
            sugg = rule.suggestion(m)
            if sugg == m.group(0):
                continue
            if rule.id not in ONE_TO_ONE:
                taken.append((s, e))
            hits.append({
                "off": s, "len": e - s, "orig": text[s:e], "sugg": sugg,
                "rule": rule.id, "severity": rule.severity, "category": rule.category,
                "msg_fa": rule.fa, "msg_en": rule.en,
            })
        if rule.id in ONE_TO_ONE:
            work, _ = rule.sub(work)
    hits.sort(key=lambda h: h["off"])
    return hits


def apply_safe(text: str):
    """Apply every safe rule. Returns (new_text, {rule_id: count})."""
    counts = {}
    if is_arabic_like(text) or not has_persian(text):
        return text, counts
    for rule in SAFE_RULES:
        text, n = rule.sub(text)
        if n:
            counts[rule.id] = counts.get(rule.id, 0) + n
    return text, counts

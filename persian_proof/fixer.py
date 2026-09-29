"""Write corrected ePub copies with only the 'safe' typography fixes applied.

Fixes are applied to the text between tags in the raw XHTML, so markup,
DOCTYPE, entities and formatting are left byte-for-byte untouched.
"""
import re
import zipfile

from .rules import apply_safe

TOKEN = re.compile(
    r"(<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|<![^>]*>|<[^>]+>)", re.S
)
TAG = re.compile(r"<\s*(/)?\s*([A-Za-z][\w:.-]*)")
NO_TOUCH = {"script", "style", "head", "rt", "rp", "svg", "math", "code", "pre"}


def fix_markup(src: str):
    parts = TOKEN.split(src)
    skip = 0
    counts = {}
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            m = TAG.match(part)
            if m:
                name = m.group(2).split(":")[-1].lower()
                if name in NO_TOUCH and not part.rstrip().endswith("/>"):
                    skip += -1 if m.group(1) else 1
                    skip = max(skip, 0)
            continue
        if skip or not part.strip():
            continue
        new, c = apply_safe(part)
        if c:
            parts[idx] = new
            for k, v in c.items():
                counts[k] = counts.get(k, 0) + v
    return "".join(parts), counts


def write_fixed_epub(src_path, dst_path, spine):
    """Copy an ePub, fixing spine documents. Returns {rule: count}."""
    spine = set(spine)
    totals = {}
    with zipfile.ZipFile(src_path) as zin:
        infos = zin.infolist()
        # mimetype must be first and stored
        infos.sort(key=lambda i: 0 if i.filename == "mimetype" else 1)
        with zipfile.ZipFile(dst_path, "w") as zout:
            for info in infos:
                data = zin.read(info.filename)
                if info.filename in spine:
                    try:
                        text = data.decode("utf-8")
                    except UnicodeDecodeError:
                        text = None
                    if text is not None:
                        new, c = fix_markup(text)
                        if c:
                            data = new.encode("utf-8")
                            for k, v in c.items():
                                totals[k] = totals.get(k, 0) + v
                zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                zi.external_attr = info.external_attr
                zi.compress_type = (
                    zipfile.ZIP_STORED if info.filename == "mimetype" else info.compress_type
                )
                zout.writestr(zi, data)
    return totals

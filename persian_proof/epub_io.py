"""Reading ePubs: OPF/spine, titles, and text blocks with stable locations."""
import hashlib
import posixpath
import re
import zipfile
from urllib.parse import unquote

from lxml import etree
from lxml import html as lxml_html

BLOCK_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "td", "th",
    "dt", "dd", "figcaption", "caption", "pre", "div", "section", "article",
    "aside", "header", "footer", "body", "table", "tr", "ul", "ol", "dl", "hr",
    "figure", "nav", "main",
}
SKIP_TAGS = {"head", "script", "style", "svg", "math", "rt", "rp"}
WS = re.compile(r"[\s ]+")
_XML_PARSER = etree.XMLParser(
    recover=True, resolve_entities=False, huge_tree=True, no_network=True
)


class EpubError(Exception):
    pass


def book_id_for(rel_path: str) -> str:
    return hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:12]


def _xml(data: bytes):
    return etree.fromstring(data, _XML_PARSER)


def _lname(el):
    tag = el.tag
    if not isinstance(tag, str):
        return None
    return tag.rsplit("}", 1)[-1].lower()


class Epub:
    def __init__(self, path):
        self.path = str(path)
        try:
            self.zf = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as e:
            raise EpubError(f"not a zip file: {e}")
        self.names = self.zf.namelist()
        self._lower = {n.lower(): n for n in self.names}
        self.title, self.spine = self._read_opf()

    def close(self):
        self.zf.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _resolve(self, name):
        if name in self.names:
            return name
        return self._lower.get(name.lower())

    def _read_opf(self):
        cpath = self._resolve("META-INF/container.xml")
        if not cpath:
            raise EpubError("missing META-INF/container.xml")
        container = _xml(self.zf.read(cpath))
        rf = container.find(".//{*}rootfile") if container is not None else None
        if rf is None or not rf.get("full-path"):
            raise EpubError("container.xml has no rootfile")
        opf_path = self._resolve(rf.get("full-path"))
        if not opf_path:
            raise EpubError("OPF file listed in container.xml is missing")
        opf = _xml(self.zf.read(opf_path))
        if opf is None:
            raise EpubError("unreadable OPF")
        base = posixpath.dirname(opf_path)
        t = opf.find(".//{http://purl.org/dc/elements/1.1/}title")
        title = (t.text or "").strip() if t is not None else ""
        manifest = {}
        for it in opf.iterfind(".//{*}manifest/{*}item"):
            href = it.get("href")
            if not href:
                continue
            full = posixpath.normpath(posixpath.join(base, unquote(href.split("#")[0])))
            manifest[it.get("id")] = (full, (it.get("media-type") or "").lower())
        spine, seen = [], set()
        for ref in opf.iterfind(".//{*}spine/{*}itemref"):
            m = manifest.get(ref.get("idref"))
            if not m:
                continue
            full, mt = m
            if "html" not in mt and not full.lower().endswith((".xhtml", ".html", ".htm")):
                continue
            real = self._resolve(full)
            if real and real not in seen:
                seen.add(real)
                spine.append(real)
        return title, spine

    def read(self, name) -> bytes:
        return self.zf.read(name)


def parse_doc(data: bytes):
    root = None
    try:
        root = _xml(data)
    except etree.XMLSyntaxError:
        root = None
    if root is None or _lname(root) != "html":
        try:
            root = lxml_html.fromstring(data)
        except Exception:
            return None
    return root


def extract_blocks(root):
    """Split a document into text blocks at block-level element boundaries.

    Inline markup (<b>, <span>, <a>…) is kept inside its block, so each block is
    one paragraph/heading/list item as a reader sees it.
    """
    out, buf = [], []
    anchor = [None]

    def flush():
        t = WS.sub(" ", "".join(buf)).strip()
        buf.clear()
        if t:
            out.append({"i": len(out), "id": anchor[0], "t": t})
        anchor[0] = None

    def walk(el):
        name = _lname(el)
        if name is None or name in SKIP_TAGS:
            if el.tail:
                buf.append(el.tail)
            return
        is_block = name in BLOCK_TAGS
        if is_block:
            flush()
            anchor[0] = el.get("id")
        elif name == "br":
            buf.append(" ")
        if el.text:
            buf.append(el.text)
        for c in el:
            walk(c)
        if is_block:
            flush()
        if el.tail:
            buf.append(el.tail)

    body = next((e for e in root.iter() if _lname(e) == "body"), root)
    walk(body)
    flush()
    return out


def iter_book_blocks(epub: Epub):
    """Yield (file_name, blocks) for each spine document."""
    for name in epub.spine:
        root = parse_doc(epub.read(name))
        if root is None:
            continue
        yield name, extract_blocks(root)

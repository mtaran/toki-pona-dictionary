"""Build a Dash docset from dictionary.json.

Pages are grouped so related entries are visible together:
- Toki Pona → English: one page per primary word (the first space-separated
  token of the headword). Compounds like "toki ala", "toki pona", "toki musi"
  all live on tp/toki.html, sorted alphabetically. The primary word's own
  entry comes first.
- English → Toki Pona: one page per first letter (en/a.html .. en/z.html,
  plus en/other.html for entries beginning with non-letters).

Every entry gets a `dashAnchor` so Dash builds a per-page table of contents
and can jump directly to each entry from search results.

Output:
  TokiPona.docset/   -- the docset folder
  TokiPona.tgz       -- gzipped tarball for distribution
"""

import json
import re
import shutil
import sqlite3
import tarfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

ROOT = Path("dictionary/TokiPona.docset")
CONTENTS = ROOT / "Contents"
RESOURCES = CONTENTS / "Resources"
DOCS = RESOURCES / "Documents"

INFO_PLIST = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key>
  <string>tokipona</string>
  <key>CFBundleName</key>
  <string>Toki Pona</string>
  <key>DocSetPlatformFamily</key>
  <string>tokipona</string>
  <key>isDashDocset</key>
  <true/>
  <key>dashIndexFilePath</key>
  <string>index.html</string>
  <key>DashDocSetFamily</key>
  <string>unsorteddashtoc</string>
  <key>isJavaScriptEnabled</key>
  <false/>
</dict>
</plist>
"""

CSS = """\
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
       margin: 0; padding: 16px 20px 60px; color: #1c1c1c; background: #fbfaf3;
       line-height: 1.45; max-width: 860px; }
.nav { font-size: 14px; margin: 0 0 14px; }
.nav a { display: inline-block; padding: 2px 7px; color: #1e6f5c;
         text-decoration: none; border-radius: 3px; }
.nav a:hover { background: #ecebd9; }
.nav a.current { background: #1e6f5c; color: #fff; }
.entry { padding: 8px 0; border-bottom: 1px solid #ece8d0; }
.entry:last-child { border-bottom: none; }
h2 { font-size: 19px; margin: 0 0 2px; color: #1e6f5c; font-weight: 600; }
.dir { display: none; }  /* page label already conveys direction */
.translations { margin: 4px 0 0; }
.pos-group { margin: 1px 0; }
.pos-label { font-style: italic; color: #888; font-size: 13px; margin-right: 6px; }
.trans { display: inline-block; margin-right: 12px; margin-bottom: 2px; }
.trans .score { font-size: 11px; color: #888; vertical-align: super; margin-left: 1px; }
.trans a { color: #1e6f5c; text-decoration: none; border-bottom: 1px dotted #c8c4ae; }
.trans a:hover { border-bottom-style: solid; }
.more-section { margin-top: 6px; padding-top: 4px; border-top: 1px dashed #d9d4c0; }
.more-section::before { content: "more"; color: #888; font-style: italic;
                        font-size: 11px; text-transform: uppercase;
                        letter-spacing: 0.04em; margin-right: 8px; }
.footer { margin-top: 28px; padding-top: 8px; border-top: 1px solid #d9d4c0;
          color: #888; font-size: 12px; }
.index h1 { margin: 6px 0 4px; color: #1e6f5c; font-size: 22px; }
.index h2 { margin-top: 22px; font-size: 17px; border-bottom: 1px solid #d9d4c0;
            padding-bottom: 4px; }
.index .nav { font-size: 16px; }
.index p { color: #444; }
.index .count { color: #1e6f5c; font-weight: 600; }
@media (prefers-color-scheme: dark) {
  body { background: #1c1d1a; color: #ece8d8; }
  h2, .index h1, .index .count, .trans a, .nav a { color: #6fcca0; }
  .nav a:hover { background: #2b2d23; }
  .nav a.current { background: #6fcca0; color: #1c1d1a; }
  .entry { border-color: #2b2d23; }
  .index h2 { border-color: #3a3c33; }
  .more-section, .footer { border-color: #3a3c33; }
  .pos-label, .trans .score, .more-section::before, .footer { color: #999; }
  .trans a { border-bottom-color: #44473b; }
  .index p { color: #ccc; }
}
"""


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "x"


def escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def score_str(s) -> str:
    return "½" if s == 0.5 else str(int(s))


def first_letter(s: str) -> str:
    s = s.lower().lstrip("(").strip()
    if not s:
        return "other"
    c = s[0]
    return c if "a" <= c <= "z" else "other"


def first_word(s: str) -> str:
    return s.split(" ", 1)[0]


def dash_anchor(name: str) -> str:
    """Anchor that Dash picks up for the in-page TOC. Format is
    //apple_ref/<lang>/<type>/<name>; we use a placeholder lang and the type
    that matches the searchIndex (Word)."""
    return f"<a name='//apple_ref/cpp/Word/{quote(name, safe='')}' class='dashAnchor'></a>"


def build():
    with open("dictionary/dictionary.json", encoding="utf-8") as f:
        data = json.load(f)
    source = data.get("source", "")

    if ROOT.exists():
        shutil.rmtree(ROOT)
    DOCS.mkdir(parents=True)
    (DOCS / "en").mkdir()
    (DOCS / "tp").mkdir()
    (CONTENTS / "Info.plist").write_text(INFO_PLIST, encoding="utf-8")
    (DOCS / "style.css").write_text(CSS, encoding="utf-8")

    # ---- Group entries into pages ----

    # EN: by first letter.
    en_groups = defaultdict(list)
    for e in data["english_to_toki_pona"]:
        en_groups[first_letter(e["english"])].append(e)
    en_letters = sorted(en_groups)

    # TP: by primary (first space-separated token of headword).
    tp_groups = defaultdict(list)
    for e in data["toki_pona_to_english"]:
        tp_groups[first_word(e["toki_pona"])].append(e)
    tp_primaries = sorted(tp_groups)

    # Resolve unique slugs and within-page anchors.
    en_page = {l: f"en/{l}.html" for l in en_letters}
    tp_page = {}
    used_tp_pages = set()
    for primary in tp_primaries:
        slug = slugify(primary)
        candidate = slug
        n = 2
        while candidate in used_tp_pages:
            candidate = f"{slug}-{n}"
            n += 1
        used_tp_pages.add(candidate)
        tp_page[primary] = f"tp/{candidate}.html"

    en_anchor = {}
    for letter in en_letters:
        used = set()
        en_groups[letter].sort(key=lambda e: (e["english"].lower(), e["english"]))
        for e in en_groups[letter]:
            slug = slugify(e["english"])
            cand = slug
            n = 2
            while cand in used:
                cand = f"{slug}-{n}"
                n += 1
            used.add(cand)
            en_anchor[e["english"]] = (en_page[letter], cand)

    tp_anchor = {}
    for primary in tp_primaries:
        used = set()
        # Case-insensitive so proper-noun compounds ("toki Inli") interleave
        # with lowercase ones ("toki ike") instead of clumping at the top.
        tp_groups[primary].sort(key=lambda e: (e["toki_pona"].lower(), e["toki_pona"]))
        for e in tp_groups[primary]:
            slug = slugify(e["toki_pona"])
            cand = slug
            n = 2
            while cand in used:
                cand = f"{slug}-{n}"
                n += 1
            used.add(cand)
            tp_anchor[e["toki_pona"]] = (tp_page[primary], cand)

    def link_tp(phrase: str) -> str:
        loc = tp_anchor.get(phrase)
        if not loc:
            return escape(phrase)
        page, anchor = loc
        return f"<a href='../{page}#{anchor}'>{escape(phrase)}</a>"

    def link_en(phrase: str) -> str:
        loc = en_anchor.get(phrase)
        if not loc:
            return escape(phrase)
        page, anchor = loc
        return f"<a href='../{page}#{anchor}'>{escape(phrase)}</a>"

    def en_nav(current_letter: str) -> str:
        items = []
        for l in en_letters:
            label = l.upper() if l != "other" else "#"
            cls = " class='current'" if l == current_letter else ""
            items.append(f"<a href='{l}.html'{cls}>{label}</a>")
        return f"<nav class='nav'>{''.join(items)}</nav>"

    # ---- Render English pages ----

    for letter in en_letters:
        page_path = DOCS / en_page[letter]
        label = letter.upper() if letter != "other" else "Other"
        parts = [
            "<!doctype html><html><head><meta charset='utf-8'>",
            f"<title>English → Toki Pona ({label})</title>",
            "<link rel='stylesheet' href='../style.css'></head><body>",
        ]
        for e in en_groups[letter]:
            _, anchor = en_anchor[e["english"]]
            trans_html = " ".join(
                f"<span class='trans'>{link_tp(t['toki_pona'])}"
                f"<span class='score'>{score_str(t['score'])}</span></span>"
                for t in e["translations"]
            )
            parts.append(
                f"<section class='entry' id='{anchor}'>"
                f"{dash_anchor(e['english'])}"
                f"<h2>{escape(e['english'])}</h2>"
                f"<div class='translations'>{trans_html}</div>"
                f"</section>"
            )
        parts.append(f"<div class='footer'>{escape(source)}</div></body></html>")
        page_path.write_text("".join(parts), encoding="utf-8")

    # ---- Render Toki Pona pages ----

    def render_tp_entry(e):
        _, anchor = tp_anchor[e["toki_pona"]]
        # Group translations by (pos, more).
        groups = []
        for t in e["translations"]:
            key = (t.get("more", False), t.get("pos"))
            if not groups or (groups[-1][0], groups[-1][1]) != key:
                groups.append((key[0], key[1], []))
            groups[-1][2].append(t)
        main = [g for g in groups if not g[0]]
        more = [g for g in groups if g[0]]

        def render_group(g):
            _, pos, items = g
            items_html = " ".join(
                f"<span class='trans'>{link_en(t['english'])}"
                f"<span class='score'>{score_str(t['score'])}</span></span>"
                for t in items
            )
            label = f"<span class='pos-label'>{escape(pos)}</span>" if pos else ""
            return f"<div class='pos-group'>{label}{items_html}</div>"

        body_main = "".join(render_group(g) for g in main)
        body_more = ("<div class='more-section'>" + "".join(render_group(g) for g in more) + "</div>") if more else ""
        return (
            f"<section class='entry' id='{anchor}'>"
            f"{dash_anchor(e['toki_pona'])}"
            f"<h2>{escape(e['toki_pona'])}</h2>"
            f"<div class='translations'>{body_main}</div>{body_more}"
            f"</section>"
        )

    for primary in tp_primaries:
        page_path = DOCS / tp_page[primary]
        # Primary entry (exact match for the word) first if present, then
        # compounds alphabetically. The sort by toki_pona already alphabetizes
        # everything; the single-word primary naturally sorts before
        # "primary something" compounds.
        entries = tp_groups[primary]
        parts = [
            "<!doctype html><html><head><meta charset='utf-8'>",
            f"<title>{escape(primary)} — Toki Pona → English</title>",
            "<link rel='stylesheet' href='../style.css'></head><body>",
        ]
        for e in entries:
            parts.append(render_tp_entry(e))
        parts.append(f"<div class='footer'>{escape(source)}</div></body></html>")
        page_path.write_text("".join(parts), encoding="utf-8")

    # ---- Landing page ----

    # A-Z grid for EN, and a primary-word grid for TP.
    en_nav_full = en_nav(current_letter="")
    tp_links = " ".join(
        f"<a href='{tp_page[p]}'>{escape(p)}</a>"
        for p in sorted(tp_primaries)
    )
    en_n = len(data["english_to_toki_pona"])
    tp_n = len(data["toki_pona_to_english"])
    (DOCS / "index.html").write_text(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Toki Pona Dictionary</title>"
        "<link rel='stylesheet' href='style.css'></head>"
        "<body class='index'>"
        "<h1>Toki Pona Dictionary</h1>"
        f"<p><span class='count'>{en_n}</span> English → Toki Pona entries, "
        f"<span class='count'>{tp_n}</span> Toki Pona → English entries "
        f"(grouped under <span class='count'>{len(tp_primaries)}</span> primary words). "
        "Use Dash's search to look up either side; click translations to pivot.</p>"
        "<h2>English → Toki Pona</h2>"
        + en_nav_full +
        "<h2>Toki Pona → English (by primary word)</h2>"
        f"<nav class='nav'>{tp_links}</nav>"
        f"<div class='footer'>{escape(source)}</div></body></html>",
        encoding="utf-8",
    )

    # ---- SQLite index ----

    db_path = RESOURCES / "docSet.dsidx"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE searchIndex(id INTEGER PRIMARY KEY, name TEXT, type TEXT, path TEXT);")
    cur.execute("CREATE UNIQUE INDEX anchor ON searchIndex (name, type, path);")
    rows = []
    for e in data["english_to_toki_pona"]:
        page, anchor = en_anchor[e["english"]]
        rows.append((e["english"], "Word", f"{page}#{anchor}"))
    for e in data["toki_pona_to_english"]:
        page, anchor = tp_anchor[e["toki_pona"]]
        rows.append((e["toki_pona"], "Word", f"{page}#{anchor}"))
    cur.executemany(
        "INSERT OR IGNORE INTO searchIndex(name, type, path) VALUES (?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()

    # ---- Tarball ----

    tgz_path = Path("dictionary/TokiPona.tgz")
    if tgz_path.exists():
        tgz_path.unlink()
    with tarfile.open(tgz_path, "w:gz") as tar:
        tar.add(ROOT, arcname="TokiPona.docset")
    print(f"Built {ROOT}: {en_n} EN entries across {len(en_letters)} pages, "
          f"{tp_n} TP entries across {len(tp_primaries)} pages")
    print(f"Tarball: {tgz_path} ({tgz_path.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    build()

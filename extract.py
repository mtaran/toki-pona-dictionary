"""Extract entries from Toki Pona Dictionary (Sonja Lang 2021) PDF into JSON.

Layout: two columns per page. Headwords are bold; translations are regular
weight; POS tags ("n", "v", "adj", ...) and "(more)" markers are bold-italic;
scores (a digit or ½) are superscript-styled, smaller-font tokens attached to
each translation. Column boundaries and indentations vary across pages, so
they're detected per page from the line-start positions of full-size words.

Sections:
  - English→Toki Pona: pages 29..195 (1-indexed).
  - Toki Pona→English: pages 199..392.
"""

import json
import re
from collections import defaultdict

import pdfplumber

PDF_PATH = "Toki_Pona_Dictionary.pdf"

EN_TP_PAGES = range(28, 195)   # 0-indexed
TP_EN_PAGES = range(198, 392)


def is_score(tok: str) -> bool:
    return bool(re.fullmatch(r"\d+|½", tok))


def score_to_num(s: str):
    return 0.5 if s == "½" else int(s)


def font_flags(fontname: str):
    italic = "Italic" in fontname or "Oblique" in fontname
    bold = ("Bold" in fontname) and not italic
    return bold, italic


def detect_columns(words):
    """Return (l_head_x, r_head_x, split_x). Right-col fields may be None for
    a single-column page.

    Strategy: find the widest vertical whitespace gap between two columns of
    text by sweeping word x ranges. The right column's headword x is the
    leftmost word-start at or past the gap; the left column's headword x is
    the smallest x overall. Bold sub-entries can extend deep into the left
    column, so a bold-cluster-only approach misclassifies pages where the
    right column has no headwords (continuation-only pages).
    """
    if not words:
        return None
    full_size = [w for w in words if w.get("size", 0) >= 9]
    if not full_size:
        return None
    ranges = sorted((w["x0"], w["x1"]) for w in full_size)
    cur_end = ranges[0][1]
    biggest_gap = 0.0
    gap_pos = None
    for x0, x1 in ranges[1:]:
        if x0 > cur_end:
            gap = x0 - cur_end
            if gap > biggest_gap:
                biggest_gap = gap
                gap_pos = x0
        cur_end = max(cur_end, x1)
    l_head_x = min(w["x0"] for w in full_size)
    if biggest_gap < 10 or gap_pos is None:
        return l_head_x, None, None
    r_head_x = gap_pos
    # Sanity: the right column must hold a meaningful chunk of text.
    right_count = sum(1 for w in full_size if w["x0"] >= r_head_x)
    if right_count < 5:
        return l_head_x, None, None
    return l_head_x, r_head_x, r_head_x - 1.0


def extract_lines(page):
    """Return [{column, headword, tokens: [{text, bold, italic, size, x0, top}]}].

    Lines are baselines clustered from full-size tokens; superscripts join the
    nearest baseline.
    """
    words = page.extract_words(
        use_text_flow=True,
        keep_blank_chars=False,
        extra_attrs=["fontname", "size"],
    )
    if not words:
        return []
    cols = detect_columns(words)
    if cols is None:
        return []
    l_head_x, r_head_x, split_x = cols
    head_tol = 4.0

    by_col = {"L": defaultdict(list), "R": defaultdict(list)}

    def col_of(x):
        return "L" if (split_x is None or x < split_x) else "R"

    main_tops_by_col = {"L": [], "R": []}
    for w in words:
        if w.get("size", 0) >= 9:
            main_tops_by_col[col_of(w["x0"])].append(w["top"])

    centers_by_col = {"L": [], "R": []}
    for col in ("L", "R"):
        tops = sorted(main_tops_by_col[col])
        if not tops:
            continue
        groups = [[tops[0]]]
        for t in tops[1:]:
            if t - groups[-1][-1] < 6:
                groups[-1].append(t)
            else:
                groups.append([t])
        centers_by_col[col] = [sum(g) / len(g) for g in groups]

    for w in words:
        # Skip italic-only small text: footnotes (e.g., the parenthetical on
        # the yupekosi page) use this style and aren't part of any entry.
        size = w.get("size", 0)
        bold, italic = font_flags(w.get("fontname", ""))
        if italic and not bold and size < 9:
            continue
        col = col_of(w["x0"])
        centers = centers_by_col[col]
        if not centers:
            continue
        idx = min(range(len(centers)), key=lambda i: abs(centers[i] - w["top"]))
        by_col[col][idx].append(w)

    out = []
    for col in ("L", "R"):
        head_x = l_head_x if col == "L" else r_head_x
        for idx in sorted(by_col[col]):
            ws = sorted(by_col[col][idx], key=lambda w: w["x0"])
            leftmost = ws[0]
            leftmost_x = leftmost["x0"]
            leftmost_bold, _ = font_flags(leftmost.get("fontname", ""))
            # A line is a headword start only if its leftmost word sits at the
            # column's headword x AND is bold (translations can also start at
            # head_x when the previous line ended with a wrap; only bold means
            # "this is the start of a new entry/sub-entry").
            is_head = (
                head_x is not None
                and abs(leftmost_x - head_x) < head_tol
                and leftmost_bold
            )
            toks = []
            for w in ws:
                bold, italic = font_flags(w.get("fontname", ""))
                toks.append({
                    "text": w["text"],
                    "bold": bold,
                    "italic": italic,
                    "size": w.get("size", 0),
                    "x0": w["x0"],
                    "top": w["top"],
                })
            out.append({"column": col, "headword": is_head, "tokens": toks})
    return out


HEADER_PHRASES = {
    "English– Toki Pona", "English–Toki Pona",
    "Toki Pona– English", "Toki Pona–English",
    "English–", "Toki Pona–", "Toki Pona",
    "English", "Pona",
}


def collect_entries(pages):
    """Walk lines in reading order; group into entries by headword start."""
    entries = []
    current = None
    for page in pages:
        for line in extract_lines(page):
            toks = line["tokens"]
            if not toks:
                continue
            joined = " ".join(t["text"] for t in toks)
            # Page number footer (e.g., "29" alone) → skip.
            if len(toks) == 1 and re.fullmatch(r"\d+", toks[0]["text"]):
                continue
            if joined in HEADER_PHRASES:
                continue
            if line["headword"]:
                if current is not None:
                    entries.append(current)
                current = list(toks)
            else:
                if current is None:
                    current = list(toks)
                else:
                    current.extend(toks)
    if current is not None:
        entries.append(current)
    return entries


# ---------------- English → Toki Pona ----------------

def split_en_head_and_translations(toks):
    """English headword is bold; translations are regular weight.

    Returns (head_text, [(phrase, score_str), ...]).
    """
    head_end = 0
    for i, t in enumerate(toks):
        if t["bold"]:
            head_end = i + 1
        else:
            if head_end > 0:
                break
    head_text = re.sub(r"\s+", " ", " ".join(t["text"] for t in toks[:head_end])).strip()

    translations = []
    buf = []
    i = head_end
    while i < len(toks):
        txt = toks[i]["text"]
        if is_score(txt):
            phrase = re.sub(r"\s+", " ", " ".join(w["text"] for w in buf)).strip()
            translations.append((phrase, txt))
            buf = []
            i += 1
            if i < len(toks) and toks[i]["text"] == ",":
                i += 1
        elif txt == ",":
            i += 1
        else:
            buf.append(toks[i])
            i += 1
    return head_text, translations


def parse_en_tp(pages):
    out = []
    for ent in collect_entries(pages):
        head, trans = split_en_head_and_translations(ent)
        if not head or not trans:
            continue
        out.append({
            "english": head,
            "translations": [
                {"toki_pona": p, "score": score_to_num(s)} for p, s in trans if p
            ],
        })
    return out


# ---------------- Toki Pona → English ----------------

POS_TAGS = {
    "n", "v", "vt", "vi", "adj", "adv", "pv", "prep", "excl",
    "num", "art", "pn", "interj", "cont",
}


def parse_tp_en(pages):
    """Each entry: a bold toki-pona headword followed by translations grouped
    by POS (bold-italic), with optional "(more)" sections and sub-entries
    (bold compound phrases inline with translations on the same line).
    """
    results = []
    for ent in collect_entries(pages):
        head_end = 0
        for i, t in enumerate(ent):
            if t["bold"]:
                head_end = i + 1
            else:
                if head_end > 0:
                    break
        head_text = re.sub(r"\s+", " ", " ".join(t["text"] for t in ent[:head_end])).strip()
        if not head_text:
            continue

        body = ent[head_end:]
        translations = []
        current_pos = None
        in_more = False
        i = 0
        while i < len(body):
            tok = body[i]
            txt = tok["text"]
            # A POS marker is a bold-italic full-size token. Either a single
            # tag ("v") or a comma-joined sequence ("prep,vt") rendered as
            # one token with no surrounding space.
            if tok["bold"] is False and tok["italic"] and tok.get("size", 0) >= 9:
                parts = [p for p in txt.split(",") if p]
                if parts and all(p in POS_TAGS for p in parts):
                    pos_parts = list(parts)
                    j = i + 1
                    while j + 1 < len(body) and body[j]["text"] == "," and body[j + 1]["text"] in POS_TAGS:
                        pos_parts.append(body[j + 1]["text"])
                        j += 2
                    current_pos = ",".join(pos_parts)
                    in_more = False
                    i = j
                    continue
            if txt in POS_TAGS:
                pos_parts = [txt]
                j = i + 1
                while j + 1 < len(body) and body[j]["text"] == "," and body[j + 1]["text"] in POS_TAGS:
                    pos_parts.append(body[j + 1]["text"])
                    j += 2
                current_pos = ",".join(pos_parts)
                in_more = False
                i = j
                continue
            if txt == "(more)":
                in_more = True
                current_pos = None
                i += 1
                continue
            if txt == ",":
                i += 1
                continue
            if is_score(txt):
                i += 1
                continue
            buf = []
            while i < len(body) and not is_score(body[i]["text"]):
                if body[i]["text"] == "," and buf:
                    break
                buf.append(body[i])
                i += 1
            if i < len(body) and is_score(body[i]["text"]):
                phrase = re.sub(r"\s+", " ", " ".join(w["text"] for w in buf)).strip()
                if phrase:
                    translations.append({
                        "english": phrase,
                        "score": score_to_num(body[i]["text"]),
                        "pos": current_pos,
                        "more": in_more,
                    })
                i += 1
                if i < len(body) and body[i]["text"] == ",":
                    i += 1
        if translations:
            results.append({"toki_pona": head_text, "translations": translations})
    return results


def main():
    with pdfplumber.open(PDF_PATH) as pdf:
        en_pages = [pdf.pages[i] for i in EN_TP_PAGES]
        tp_pages = [pdf.pages[i] for i in TP_EN_PAGES]
        en_tp = parse_en_tp(en_pages)
        tp_en = parse_tp_en(tp_pages)
    out_path = "dictionary/dictionary.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "source": "Toki Pona Dictionary by Sonja Lang (2021)",
            "english_to_toki_pona": en_tp,
            "toki_pona_to_english": tp_en,
        }, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {out_path}: {len(en_tp)} EN→TP, {len(tp_en)} TP→EN entries")


if __name__ == "__main__":
    main()

"""Extract questions, answers, rationales and figures from the SCCM PDF.

Usage: python3 scripts/extract.py
Writes app/questions.json and app/img/*.jpg
"""
import html
import json
import os
import re

import pymupdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF = os.path.join(ROOT, "SCCM Self-Assessment Adult Multiprofessional Critical Care 2022.pdf")
OUT = os.path.join(ROOT, "app")
IMG_DIR = os.path.join(OUT, "img")
FIRST_CONTENT_PAGE = 24  # 0-based index of "Part 1." page

PART_Q = re.compile(r"^Part (\d+)\.$")
PART_A = re.compile(r"^Part (\d+) Answers:$")
Q_START = re.compile(r"^(\d+)\.\s+(.*)$")
OPT = re.compile(r"^([A-F])\.\s+(.*)$")
RATIONALE = re.compile(r"^(\d+)\.\s*Rationale\s*(?:Answer:\s*([A-F]))?$")
ANSWER = re.compile(r"^Answer:\s*([A-F])$")


LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl"}


def norm(s):
    s = s.replace("\xa0", " ").replace("\u00ad", "")
    for k, v in LIGATURES.items():
        s = s.replace(k, v)
    return re.sub(r"[ \t]+", " ", s).strip()


def span_html(sp):
    t = html.escape(sp["text"], quote=False)
    return f"<sup>{t}</sup>" if sp["flags"] & 1 and t.strip() else t


def page_items(page, pno):
    """Items in reading order: text rows {'kind': 'line', ...} and figures {'kind': 'img', ...}.

    'text' is plain text (for pattern matching); 'html' carries <sup> markup."""
    items = []
    d = page.get_text("dict")
    for b in d["blocks"]:
        if b["type"] == 1:
            items.append({"kind": "img", "bbox": b["bbox"], "y": b["bbox"][1]})
            continue
        rows = []
        for l in b["lines"]:
            text = "".join(sp["text"] for sp in l["spans"])
            if not text.strip():
                continue
            markup = "".join(span_html(sp) for sp in l["spans"])
            y0 = l["bbox"][1]
            # justified text is sometimes split into several "lines" on one row
            if rows and abs(rows[-1]["y"] - y0) < 3:
                rows[-1]["text"] += text
                rows[-1]["html"] += markup
                rows[-1]["x1"] = max(rows[-1]["x1"], l["bbox"][2])
                continue
            rows.append({"kind": "line", "text": text, "html": markup, "x": l["bbox"][0],
                         "x1": l["bbox"][2], "y": y0, "size": l["spans"][0]["size"],
                         "block": (pno, b["number"])})
        items.extend(rows)
    items.sort(key=lambda it: it["y"])
    right = max((it["x1"] for it in items if it["kind"] == "line"), default=0)
    for it in items:
        if it["kind"] == "line":
            # a line that stops well short of the right margin ends a paragraph
            it["short"] = it["x1"] < right - 35
    return items


def join_lines(lines):
    """Join wrapped lines into paragraphs. lines: list of (html, short_line)."""
    paras = []
    cur = ""
    prev_short = False
    for markup, short in lines:
        t = norm(markup)
        if not t:
            continue
        if cur and prev_short and re.search(r"[.:?!)]$", cur) and re.match(r"[A-Z0-9(•]", t):
            paras.append(cur)
            cur = t
        elif cur:
            if cur.endswith("-") and not cur.endswith(" -"):
                cur += t
            else:
                cur += " " + t
        else:
            cur = t
        prev_short = short
    if cur:
        paras.append(cur)
    return paras


def main():
    os.makedirs(IMG_DIR, exist_ok=True)
    doc = pymupdf.open(PDF)

    # Part titles from table of contents
    titles = {}
    for lvl, title, pg in doc.get_toc():
        m = re.match(r"Part (\d+): (.*)", title)
        if m:
            titles[int(m.group(1))] = m.group(2)

    stream = []  # (page_index, item)
    for pno in range(FIRST_CONTENT_PAGE, doc.page_count - 1):  # last page is the back cover
        page = doc[pno]
        for it in page_items(page, pno):
            it["page"] = pno
            stream.append(it)

    img_count = [0]
    last_block = [None]

    def save_img(it, label):
        page = doc[it["page"]]
        r = pymupdf.Rect(it["bbox"])
        pix = page.get_pixmap(clip=r, dpi=220)
        img_count[0] += 1
        name = f"{label}.jpg"
        pix.save(os.path.join(IMG_DIR, name), jpg_quality=88)
        return {"src": f"img/{name}", "w": pix.width, "h": pix.height}

    parts = {}
    mode = None  # 'q' or 'a'
    part = None
    cur = None  # current question/answer record
    section = None  # 'stem' | 'opt' | 'rat' | 'ref' | 'header'

    def finish():
        nonlocal cur
        cur = None

    for it in stream:
        if it["kind"] == "img":
            if cur is None:
                continue
            label = f"p{part}_{'q' if mode == 'q' else 'a'}{cur['num']}_{len(cur['imgs']) + 1}"
            info = save_img(it, label)
            info["after"] = section
            # position marker in the body
            cur["seq"].append(("img", info, section))
            cur["imgs"].append(info)
            continue

        text = norm(it["text"])
        if it["size"] > 18:
            m = PART_Q.match(text)
            if m:
                part = int(m.group(1)); mode = "q"; finish()
                parts.setdefault(part, {"num": part, "title": titles[part], "questions": {}, "answers": {}})
                continue
            m = PART_A.match(text)
            if m:
                part = int(m.group(1)); mode = "a"; finish()
                continue
            continue  # part subtitle
        if part is None:
            continue
        if text.startswith("Instructions: For each question") or text == "answer.":
            continue

        line_block, prev_block = it["block"], last_block[0]
        last_block[0] = line_block
        if mode == "q":
            qs = parts[part]["questions"]
            m = Q_START.match(text)
            expected = len(qs) + 1
            if m and int(m.group(1)) == expected and (cur is None or section == "opt"):
                cur = {"num": expected, "seq": [], "imgs": [], "opts": []}
                qs[expected] = cur
                section = "stem"
                cur["seq"].append(("line", Q_START.match(norm(it["html"])).group(2), it["short"]))
                continue
            if cur is None:
                continue
            m = OPT.match(text)
            letter_ok = m and ord(m.group(1)) - 65 == len(cur["opts"])
            if letter_ok and m.group(1) == "A" and it["block"] == prev_block:
                # "A." that merely wraps inside a stem paragraph (e.g. "influenza\nA. He ...")
                letter_ok = False
            if letter_ok:
                section = "opt"
                cur["opts"].append([OPT.match(norm(it["html"])).group(2)])
                continue
            if section == "opt":
                cur["opts"][-1].append(norm(it["html"]))
            else:
                cur["seq"].append(("line", it["html"], it["short"]))
        else:
            ans = parts[part]["answers"]
            m = RATIONALE.match(text)
            if m and int(m.group(1)) == len(ans) + 1:
                cur = {"num": int(m.group(1)), "seq": [], "imgs": [], "answer": m.group(2), "refs": []}
                ans[cur["num"]] = cur
                section = "rat" if m.group(2) else "header"
                continue
            if cur is None:
                continue
            m = ANSWER.match(text)
            if m and cur["answer"] is None:
                cur["answer"] = m.group(1)
                section = "rat"
                continue
            if text == "References:" or text == "Reference:":
                section = "ref"
                continue
            if section == "ref":
                cur["refs"].append((norm(it["html"]), it["block"]))
            else:
                cur["seq"].append(("line", it["html"], it["short"]))

    def render_seq(seq):
        """Return list of body blocks: {'p': text} or {'img': {...}}"""
        out = []
        buf = []
        for kind, a, b in seq:
            if kind == "img":
                if buf:
                    out += [{"p": p} for p in join_lines(buf)]
                    buf = []
                out.append({"img": {k: a[k] for k in ("src", "w", "h")}})
            else:
                buf.append((a, b))
        if buf:
            out += [{"p": p} for p in join_lines(buf)]
        return out

    result = []
    gid = 0
    for pn in sorted(parts):
        p = parts[pn]
        qlist = []
        for n in sorted(p["questions"]):
            q = p["questions"][n]
            a = p["answers"].get(n)
            gid += 1
            refs = []
            if a:
                cur_ref = ""
                for t, _ in a["refs"]:
                    if re.match(r"^\d+\.\s", t) and cur_ref:
                        refs.append(cur_ref); cur_ref = t
                    else:
                        cur_ref = (cur_ref + ("" if cur_ref.endswith("-") else " ") + t).strip() if cur_ref else t
                if cur_ref:
                    refs.append(cur_ref)
            qlist.append({
                "id": gid,
                "n": n,
                "stem": render_seq(q["seq"]),
                "options": [norm(" ".join(o)) for o in q["opts"]],
                "answer": a["answer"] if a else None,
                "rationale": render_seq(a["seq"]) if a else [],
                "refs": [re.sub(r"^(\d+)\.\s+", r"\1. ", r) for r in refs],
            })
        result.append({"part": pn, "title": p["title"], "questions": qlist})

    with open(os.path.join(OUT, "questions.json"), "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    total = sum(len(p["questions"]) for p in result)
    print("parts", len(result), "questions", total, "images", img_count[0])
    for p in result:
        print(p["part"], p["title"], len(p["questions"]),
              "missing answers:", [q["n"] for q in p["questions"] if not q["answer"]],
              "bad opts:", [q["n"] for q in p["questions"] if len(q["options"]) < 3])


if __name__ == "__main__":
    main()

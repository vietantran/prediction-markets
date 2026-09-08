"""Render a cited Markdown research report to portable HTML, PDF and Excel.

The renderer never fetches data. All charts and tables must already exist next
to the Markdown source, so the published report can be audited offline.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import math
from pathlib import Path
import re


CSS = """
:root{--ink:#172f46;--muted:#536575;--teal:#007f82;--line:#dce5eb}
*{box-sizing:border-box} body{font:16px/1.65 system-ui,sans-serif;color:var(--ink);margin:0;background:#eef3f6}
main{max-width:1160px;margin:auto;padding:50px 65px;background:white}
h1{font-size:38px;line-height:1.15;letter-spacing:-1px;margin:0 0 28px}
h2{font-size:26px;margin-top:52px;padding-bottom:10px;border-bottom:3px solid var(--teal)}
h3{font-size:20px;margin-top:32px}a{color:var(--teal);overflow-wrap:anywhere}
table{border-collapse:collapse;width:100%;margin:22px 0;font-size:14px}
th{background:var(--ink);color:white;text-align:left}td,th{padding:11px;border-bottom:1px solid var(--line);vertical-align:top}
tr:nth-child(even){background:#f2f6f8}img{display:block;max-width:100%;height:auto;margin:24px auto}
blockquote{background:#eaf5f4;border-left:5px solid var(--teal);margin:24px 0;padding:14px 20px}
code{background:#edf2f5;padding:2px 5px;font-size:13px}pre{padding:18px;background:#edf2f5;overflow:auto}
.meta,figcaption{color:var(--muted);font-size:13px}.downloads{padding:15px;background:#eaf5f4;margin-bottom:30px}
@media(max-width:720px){main{padding:25px 18px}h1{font-size:30px}table{display:block;overflow:auto}}
@media print{body{background:white}main{padding:0}a{color:inherit}h2{break-before:page}table,img{break-inside:avoid}}
"""


def markdown_table(rows, columns):
    """Escape delimiters and retain blank missing values in original tables."""
    def cell(value):
        if value is None:
            return "Unavailable"
        value = html.escape(str(value), quote=False)
        for symbol in ["\\", "*", "_", "[", "]", "!", "`", "|"]:
            value = value.replace(symbol, "\\" + symbol)
        return value.replace("\n", "<br>")
    header = "| " + " | ".join(label for _, label in columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(cell(row.get(key)) for key, _ in columns) + " |" for row in rows]
    return "\n".join([header, divider] + body)


def workbook(csv_paths, out: Path, notes):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    book = Workbook()
    index = book.active
    index.title = "README"
    for row in [["Midterms issue research — auditable derived tables"], *[[x] for x in notes],
                [], ["Worksheet", "Rows", "Source file", "SHA256"]]:
        index.append(row)
    seen = {"README"}
    for path in csv_paths:
        base = re.sub(r"[\\/*?:\[\]]", "_", path.stem)[:27]
        name, counter = base, 1
        while name in seen:
            counter += 1
            name = f"{base[:26]}_{counter}"
        seen.add(name)
        sheet = book.create_sheet(name)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            for row_number, row in enumerate(reader):
                # Untrusted titles must never become executable spreadsheet formulas.
                safe = []
                for value in row:
                    if not value:
                        safe.append(None)
                    elif row_number and re.fullmatch(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?", value):
                        numeric = float(value)
                        # Excel has 15 significant digits; long identifiers stay text.
                        safe.append((int(value) if re.fullmatch(r"-?\d+", value) else numeric)
                                    if math.isfinite(numeric) and len(value.lstrip("-").split(".")[0]) < 16 else value)
                    else:
                        safe.append(("'" + value) if value.lstrip().startswith(("=", "+", "-", "@")) else value)
                sheet.append(safe)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for c in sheet[1]:
            c.fill = PatternFill("solid", fgColor="172F46")
            c.font = Font(color="FFFFFF", bold=True)
        for i in range(1, min(sheet.max_column, 45) + 1):
            lengths = [len(str(sheet.cell(j, i).value or "")) for j in range(1, min(sheet.max_row, 60) + 1)]
            sheet.column_dimensions[get_column_letter(i)].width = min(55, max(14, max(lengths, default=14) + 2))
        index.append([name, max(0, sheet.max_row - 1), path.name, hashlib.sha256(path.read_bytes()).hexdigest()])
    index.column_dimensions["A"].width = 95
    index.column_dimensions["B"].width = 15
    index.column_dimensions["C"].width = 40
    index.column_dimensions["D"].width = 68
    book.save(out)


def render(markdown_path: Path, title="US midterms: issue momentum and equity implications"):
    import markdown
    from bs4 import BeautifulSoup, NavigableString
    import matplotlib
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Image,
                                   Table, TableStyle, PageBreak, CondPageBreak, KeepTogether)

    root = markdown_path.parent
    content = markdown.markdown(markdown_path.read_text(encoding="utf-8"),
                                extensions=["tables", "fenced_code", "toc"])
    html_path = markdown_path.with_suffix(".html")
    html_path.write_text("<!doctype html><html lang='en'><meta charset='utf-8'>"
                        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                        f"<title>{html.escape(title)}</title><style>{CSS}</style><main>{content}</main></html>",
                        encoding="utf-8")
    fonts = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    for name, file in [("Research", "DejaVuSans.ttf"), ("ResearchBold", "DejaVuSans-Bold.ttf"),
                       ("ResearchItalic", "DejaVuSans-Oblique.ttf")]:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(fonts / file)))
    pdfmetrics.registerFontFamily("Research", normal="Research", bold="ResearchBold", italic="ResearchItalic", boldItalic="ResearchBold")
    styles = getSampleStyleSheet()
    for name in ["Normal", "BodyText", "Heading1", "Heading2", "Heading3", "Heading4", "Code"]:
        styles[name].fontName = "Research"
        styles[name].textColor = colors.HexColor("#172f46")
    styles["BodyText"].fontSize = 9.2
    styles["BodyText"].leading = 14
    styles["BodyText"].spaceAfter = 8
    styles["BodyText"].allowWidows = 0
    styles["BodyText"].allowOrphans = 0
    for name, size in [("Heading1", 26), ("Heading2", 18), ("Heading3", 12.5), ("Heading4", 11)]:
        styles[name].fontName = "ResearchBold"
        styles[name].fontSize = size
        styles[name].leading = size * 1.3
        styles[name].spaceBefore = 15
        styles[name].spaceAfter = 10
        styles[name].keepWithNext = True
    styles.add(ParagraphStyle("Cell", fontName="Research", fontSize=7.3, leading=10, spaceAfter=0,
                             alignment=TA_LEFT, wordWrap="CJK", textColor=colors.HexColor("#172f46")))
    styles.add(ParagraphStyle("HeaderCell", parent=styles["Cell"], fontName="ResearchBold", textColor=colors.white))
    styles.add(ParagraphStyle("Caption", parent=styles["BodyText"], fontSize=8, leading=11, textColor=colors.HexColor("#536575")))
    soup = BeautifulSoup(content, "html.parser")
    flow = []
    def inline(node):
        if isinstance(node, NavigableString):
            return html.escape(str(node))
        body = "".join(inline(c) for c in node.children)
        if node.name in {"strong", "b"}:
            return f"<b>{body}</b>"
        if node.name in {"em", "i"}:
            return f"<i>{body}</i>"
        if node.name == "a":
            url = html.escape(node.get("href", ""), quote=True)
            return f'<link href="{url}" color="#007f82">{body}</link>'
        if node.name == "br":
            return "<br/>"
        return body
    width = 499
    def add_image(node):
        path = (root / node["src"]).resolve()
        if root.resolve() not in path.parents:
            raise ValueError(f"Chart outside report directory: {path}")
        iw, ih = ImageReader(str(path)).getSize()
        scale = min(width / iw, 400 / ih)
        flow.append(Image(str(path), width=iw * scale, height=ih * scale))
        flow.append(Spacer(1, 8))
    sections_seen = 0
    for node in soup.children:
        if isinstance(node, NavigableString):
            continue
        if node.name in {"h1", "h2", "h3", "h4"}:
            if node.name == "h2":
                flow.append(PageBreak() if sections_seen == 0 else CondPageBreak(200))
                sections_seen += 1
            flow.append(Paragraph(inline(node), styles["Heading" + node.name[1]]))
        elif node.name == "p":
            if node.find("img"):
                for img in node.find_all("img"):
                    add_image(img)
            elif node.get_text(strip=True):
                flow.append(Paragraph(inline(node), styles["BodyText"]))
        elif node.name == "blockquote":
            flow.append(KeepTogether([Paragraph(inline(node), styles["BodyText"]), Spacer(1, 8)]))
        elif node.name in {"ul", "ol"}:
            for n, li in enumerate(node.find_all("li", recursive=False), 1):
                prefix = f"{n}. " if node.name == "ol" else "• "
                flow.append(Paragraph(prefix + inline(li), styles["BodyText"]))
        elif node.name == "table":
            data = []
            for tr in node.find_all("tr"):
                data.append([Paragraph(inline(cell), styles["HeaderCell" if cell.name == "th" else "Cell"])
                             for cell in tr.find_all(["th", "td"])])
            if data:
                table = Table(data, colWidths=[width / len(data[0])] * len(data[0]), repeatRows=1, hAlign="LEFT")
                table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#172f46")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f5f7")]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("LINEBELOW", (0, 0), (-1, -1), .25, colors.HexColor("#dce5eb")),
                ]))
                flow.extend([table, Spacer(1, 12)])
        elif node.name == "pre":
            flow.append(Paragraph(html.escape(node.get_text()).replace("\n", "<br/>"), styles["Caption"]))
    pdf_path = markdown_path.with_suffix(".pdf")
    doc = SimpleDocTemplate(str(pdf_path), pagesize=(595.28, 841.89), rightMargin=48, leftMargin=48,
                            topMargin=48, bottomMargin=46, title=title, author="Prediction Markets Research")
    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Research", 7)
        canvas.setFillColor(colors.HexColor("#536575"))
        canvas.drawString(48, 26, "US MIDTERMS | Public-data research | See source dates and coverage")
        canvas.drawRightString(547, 26, str(document.page))
        canvas.restoreState()
    doc.build(flow, onFirstPage=footer, onLaterPages=footer)
    manifest = {"markdown": markdown_path.name, "html": html_path.name, "pdf": pdf_path.name,
                "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in [markdown_path, html_path, pdf_path]}}
    (root / "render_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest

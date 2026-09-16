"""Render the narrative HTML white paper as a paginated PDF with ReportLab."""
from __future__ import annotations

import re
from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph,
    Spacer, Table, TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/design/agent-memory-explained.html"
OUTPUT = ROOT / "output/pdf/agent-memory-whitepaper.pdf"
SCRATCH = ROOT / "tmp/pdfs"
INK = colors.HexColor("#233a32")
GREEN = colors.HexColor("#276049")
MUTED = colors.HexColor("#657068")
LINE = colors.HexColor("#dce1d9")
PALE = colors.HexColor("#edf1e9")
WIDTH, HEIGHT = A4
MARGIN = 58
CONTENT = WIDTH - 2 * MARGIN


def fonts():
    base = Path("/System/Library/Fonts/Supplemental")
    for name, file in {
        "PaperSerif": "Georgia.ttf", "PaperSerif-Bold": "Georgia Bold.ttf",
        "PaperSerif-Italic": "Georgia Italic.ttf",
        "PaperSerif-BoldItalic": "Georgia Bold Italic.ttf",
        "PaperSans": "Arial.ttf", "PaperSans-Bold": "Arial Bold.ttf",
        "PaperSans-Italic": "Arial Italic.ttf",
        "PaperSans-BoldItalic": "Arial Bold Italic.ttf",
    }.items():
        pdfmetrics.registerFont(TTFont(name, str(base / file)))
    for family in ("PaperSerif", "PaperSans"):
        pdfmetrics.registerFontFamily(family, normal=family, bold=family + "-Bold",
                                     italic=family + "-Italic", boldItalic=family + "-BoldItalic")


fonts()
styles = {
    "body": ParagraphStyle("body", fontName="PaperSerif", fontSize=10.6,
                           leading=15.1, textColor=INK, spaceAfter=8,
                           allowWidows=0, allowOrphans=0),
}


def style(name, **kwargs):
    styles[name] = ParagraphStyle(name, parent=styles["body"], **kwargs)


style("eye", fontName="PaperSans", fontSize=8.2, leading=12,
      textColor=GREEN, spaceAfter=10, keepWithNext=True)
style("title", fontSize=45, leading=51, spaceAfter=13)
style("subtitle", fontSize=21, leading=28, textColor=MUTED, spaceAfter=19)
style("meta", fontName="PaperSans", fontSize=8.5, leading=13,
      textColor=MUTED, spaceAfter=21)
style("abstract", fontSize=11.4, leading=17, spaceAfter=13)
style("h2", fontSize=23, leading=28, spaceAfter=19, keepWithNext=True)
style("h3", fontSize=13.3, leading=17, spaceBefore=9, spaceAfter=7, keepWithNext=True)
style("note", fontName="PaperSans", fontSize=8.7, leading=12.5, textColor=MUTED, spaceAfter=13)
style("quote", fontSize=11.1, leading=16.5, leftIndent=15,
      borderColor=GREEN, borderWidth=0.7, borderPadding=10,
      spaceBefore=9, spaceAfter=19)
style("cell", fontName="PaperSans", fontSize=9, leading=13, spaceAfter=0)
style("cellhead", fontName="PaperSans-Bold", fontSize=9, leading=13, spaceAfter=0)
style("caption", fontName="PaperSans", fontSize=8.4, leading=12,
      textColor=MUTED, spaceBefore=6, spaceAfter=15)
style("formula", fontName="PaperSans", fontSize=10, leading=15,
      backColor=PALE, borderPadding=12, spaceBefore=10, spaceAfter=22)
style("toc", fontName="PaperSans", fontSize=9, leading=13, spaceAfter=0)
style("source", fontSize=10, leading=15.2, spaceAfter=15)


def inline(element):
    result = escape(element.text or "")
    for child in element:
        body = inline(child)
        if child.tag == "br":
            result += "<br/>"
        elif child.tag in ("strong", "b"):
            result += "<b>" + body + "</b>"
        elif child.tag in ("em", "i"):
            result += "<i>" + body + "</i>"
        elif child.tag == "a" and child.get("href", "").startswith("https://"):
            result += '<link href="' + escape(child.get("href"), quote=True) + '" color="#276049">' + body + "</link>"
        elif child.tag == "code":
            result += '<font name="PaperSans">' + body + "</font>"
        else:
            result += body
        result += escape(child.tail or "")
    return re.sub(r"\s+", " ", result).strip()


def para(element, kind="body"):
    return Paragraph(inline(element), styles[kind])


class Paper(BaseDocTemplate):
    def __init__(self, output):
        super().__init__(str(output), pagesize=A4, leftMargin=MARGIN,
                         rightMargin=MARGIN, topMargin=53, bottomMargin=51,
                         title="Agent Memory: Continuity, Evidence, and Learning",
                         author="Agent Memory", subject="A proposed architecture for durable agent work")
        self.heading_pages = {}
        self.addPageTemplates(PageTemplate(id="paper", frames=[Frame(
            MARGIN, 51, CONTENT, HEIGHT - 104, leftPadding=0, rightPadding=0,
            topPadding=0, bottomPadding=0)], onPage=self.furniture))

    def furniture(self, canvas, doc):
        canvas.saveState()
        if doc.page > 1:
            canvas.setFont("PaperSans", 8)
            canvas.setFillColor(MUTED)
            canvas.drawString(MARGIN, HEIGHT - 29, "AGENT MEMORY")
            canvas.drawRightString(WIDTH - MARGIN, HEIGHT - 29, "WHITE PAPER / SEPTEMBER 2026")
            canvas.setStrokeColor(LINE)
            canvas.setLineWidth(0.5)
            canvas.line(MARGIN, HEIGHT - 37, WIDTH - MARGIN, HEIGHT - 37)
        canvas.setStrokeColor(LINE)
        canvas.line(MARGIN, 39, WIDTH - MARGIN, 39)
        canvas.setFillColor(MUTED)
        canvas.setFont("PaperSans", 8)
        canvas.drawString(MARGIN, 25, "Design proposal / Version 1.0")
        canvas.drawRightString(WIDTH - MARGIN, 25, f"{doc.page:02d}")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if hasattr(flowable, "paper_id"):
            self.canv.bookmarkPage(flowable.paper_id)
            self.canv.addOutlineEntry(flowable.paper_label, flowable.paper_id, level=0)
            self.heading_pages[flowable.paper_id] = self.page


def body_elements(parent, story, source=False):
    for el in parent:
        tag = el.tag
        cls = el.get("class", "")
        if tag == "p":
            kind = "eye" if cls == "section-number" else "note" if cls in ("note", "endnote") else "caption" if cls == "caption" else "source" if source else "body"
            story.append(para(el, kind))
        elif tag == "h2":
            p = para(el, "h2")
            p.paper_id = parent.get("id")
            p.paper_label = (parent.get("data-number", "") + " / " + "".join(el.itertext())).strip(" / ")
            story.append(p)
        elif tag == "h3":
            story.append(para(el, "h3"))
        elif tag == "blockquote":
            for p in el.findall("p"):
                story.append(para(p, "quote"))
        elif tag == "table":
            rows = []
            for tr in el.findall(".//tr"):
                rows.append([para(cell, "cellhead" if cell.tag == "th" else "cell") for cell in tr])
            widths = [CONTENT * v for v in ([.245, .30, .455] if len(rows[0]) == 3 else [.265, .735])]
            table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), PALE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), .5, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]))
            story.extend([Spacer(1, 5), table])
        elif tag == "figure":
            cells = []
            for div in el.findall("./div/div"):
                title = div.find("b")
                detail = div.find("span")
                text = '<font color="#276049"><b>' + escape(title.text) + "</b></font><br/>" + escape(title.tail or "") + "<br/>" + escape(detail.text)
                cells.append(Paragraph(text, styles["cell"]))
            table = Table([cells], colWidths=[CONTENT / len(cells)] * len(cells))
            table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, -1), PALE),
                ("TOPPADDING", (0, 0), (-1, -1), 11),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]))
            story.extend([Spacer(1, 6), table, para(el.find("figcaption"), "caption")])
        elif tag == "div" and cls == "formula":
            story.append(para(el, "formula"))


def build_story(root, pages):
    main = root.find(".//main")
    compact = main.get("data-format") == "one-page"
    cover = main.find("header")
    story = []
    for el in cover:
        cls = el.get("class", "")
        if el.tag == "nav":
            story.extend([Spacer(1, 10), Paragraph("READING GUIDE", styles["eye"])])
            rows = []
            for i, li in enumerate(el.findall("./ol/li"), 1):
                anchor = li.find("a")
                target = anchor.get("href")[1:]
                title = escape("".join(anchor.itertext()))
                label = f'<link href="#{target}" color="#276049">{i:02d}  {title}</link>'
                rows.append([Paragraph(label, styles["toc"]), Paragraph(str(pages.get(target, "")), styles["toc"])])
            toc = Table(rows, colWidths=[CONTENT - 25, 25])
            toc.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                     ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                     ("TOPPADDING", (0, 0), (-1, -1), 2),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
            story.append(toc)
        else:
            kind = "title" if el.tag == "h1" else {"eyebrow": "eye", "subtitle": "subtitle", "metadata": "meta", "abstract": "abstract", "note": "note"}.get(cls, "body")
            story.append(para(el, kind))
    for section in main.findall("section"):
        story.append(Spacer(1, 10) if compact else PageBreak())
        body_elements(section, story, source=section.get("id") == "sources")
    return story


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = ET.fromstring(SOURCE.read_text())
    if root.find(".//main").get("data-format") == "one-page":
        styles["h2"].fontSize = 13.3
        styles["h2"].leading = 17
        styles["h2"].spaceAfter = 9
        styles["subtitle"].fontSize = 18
        styles["subtitle"].leading = 24
        styles["subtitle"].spaceAfter = 15
    first = Paper(SCRATCH / "pagination.pdf")
    first.build(build_story(root, {}))
    final = Paper(OUTPUT)
    final.build(build_story(root, first.heading_pages))
    if first.heading_pages != final.heading_pages:
        raise RuntimeError("Pagination changed between table-of-contents passes")
    (SCRATCH / "pagination.pdf").unlink()
    print(OUTPUT)
    print("Chapter pages:", final.heading_pages)


if __name__ == "__main__":
    main()

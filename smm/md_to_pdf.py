"""Convert markdown text/files to PDF while preserving full content and structure."""

from __future__ import annotations

import re
from pathlib import Path


def _markdown_to_html(markdown_text: str) -> str:
    import markdown

    body = markdown.markdown(
        markdown_text,
        extensions=["extra", "sane_lists", "nl2br", "tables", "fenced_code"],
    )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
@page {{ size: A4; margin: 1.6cm; }}
body {{
  font-family: Helvetica, Arial, sans-serif;
  font-size: 10.5pt;
  line-height: 1.45;
  color: #111111;
}}
h1 {{ font-size: 18pt; margin: 0.8em 0 0.4em; page-break-after: avoid; }}
h2 {{ font-size: 14pt; margin: 0.9em 0 0.35em; page-break-after: avoid; }}
h3 {{ font-size: 12pt; margin: 0.7em 0 0.3em; page-break-after: avoid; }}
h4 {{ font-size: 11pt; margin: 0.6em 0 0.25em; page-break-after: avoid; }}
p, li {{ margin: 0.25em 0; }}
blockquote {{
  margin: 0.4em 0 0.4em 1em;
  padding-left: 0.6em;
  border-left: 3px solid #999999;
  color: #222222;
}}
hr {{ border: none; border-top: 1px solid #cccccc; margin: 1em 0; }}
strong {{ font-weight: bold; }}
em {{ font-style: italic; }}
code, pre {{ font-family: Courier, monospace; font-size: 9.5pt; }}
pre {{
  white-space: pre-wrap;
  background: #f7f7f7;
  padding: 8px;
  margin: 0.5em 0;
}}
ul, ol {{ margin: 0.3em 0 0.3em 1.2em; }}
table {{ border-collapse: collapse; width: 100%; margin: 0.6em 0; }}
th, td {{ border: 1px solid #cccccc; padding: 4px 6px; vertical-align: top; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def _write_pdf_xhtml2pdf(markdown_text: str, pdf_path: Path) -> None:
    from xhtml2pdf import pisa

    html = _markdown_to_html(markdown_text)
    with pdf_path.open("wb") as f:
        result = pisa.CreatePDF(html.encode("utf-8"), dest=f, encoding="utf-8")
    if result.err:
        raise RuntimeError(f"xhtml2pdf failed with {result.err} error(s) for {pdf_path}")


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _inline_md_to_reportlab(text: str) -> str:
    s = _escape_xml(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"__(.+?)__", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"<i>\1</i>", s)
    s = re.sub(r"`([^`]+)`", r"<font face='Courier' size='9'>\1</font>", s)
    return s


def _write_pdf_reportlab(markdown_text: str, pdf_path: Path) -> None:
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        HRFlowable,
        ListFlowable,
        ListItem,
        Paragraph,
        Preformatted,
        SimpleDocTemplate,
        Spacer,
    )

    base = getSampleStyleSheet()
    styles = {
        "body": ParagraphStyle(
            "MDBody",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            spaceAfter=6,
            alignment=TA_LEFT,
        ),
        "h1": ParagraphStyle(
            "MDH1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=20,
            spaceBefore=14,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "MDH2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=17,
            spaceBefore=12,
            spaceAfter=6,
        ),
        "h3": ParagraphStyle(
            "MDH3",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=11.5,
            leading=15,
            spaceBefore=10,
            spaceAfter=5,
        ),
        "h4": ParagraphStyle(
            "MDH4",
            parent=base["Heading4"],
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=14,
            spaceBefore=8,
            spaceAfter=4,
        ),
        "quote": ParagraphStyle(
            "MDQuote",
            parent=base["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=10,
            leading=14,
            leftIndent=14,
            spaceBefore=4,
            spaceAfter=6,
            textColor="#222222",
        ),
        "li": ParagraphStyle(
            "MDLi",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            leftIndent=12,
            spaceAfter=2,
        ),
        "code": ParagraphStyle(
            "MDCode",
            parent=base["Code"],
            fontName="Courier",
            fontSize=8.5,
            leading=11,
            leftIndent=8,
            spaceBefore=4,
            spaceAfter=6,
        ),
    }

    story = []
    lines = markdown_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    in_code = False
    code_lines: list[str] = []
    para_buf: list[str] = []
    list_buf: list[str] = []
    list_ordered = False

    def flush_para():
        nonlocal para_buf
        if not para_buf:
            return
        text = " ".join(x.strip() for x in para_buf if x.strip())
        if text:
            story.append(Paragraph(_inline_md_to_reportlab(text), styles["body"]))
        para_buf = []

    def flush_list():
        nonlocal list_buf, list_ordered
        if not list_buf:
            return
        items = [
            ListItem(
                Paragraph(_inline_md_to_reportlab(item), styles["li"]),
                leftIndent=18,
                bulletColor="#111111",
            )
            for item in list_buf
        ]
        story.append(
            ListFlowable(
                items,
                bulletType="1" if list_ordered else "bullet",
                start="1",
                leftIndent=18,
                bulletFontName="Helvetica",
                bulletFontSize=10,
                spaceBefore=2,
                spaceAfter=6,
            )
        )
        list_buf = []
        list_ordered = False

    while i < len(lines):
        line = lines[i]

        if line.strip().startswith("```"):
            if in_code:
                flush_para()
                flush_list()
                story.append(Preformatted("\n".join(code_lines) if code_lines else " ", styles["code"]))
                code_lines = []
                in_code = False
            else:
                flush_para()
                flush_list()
                in_code = True
                code_lines = []
            i += 1
            continue

        if in_code:
            code_lines.append(line)
            i += 1
            continue

        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", line):
            flush_para()
            flush_list()
            story.append(Spacer(1, 4))
            story.append(HRFlowable(width="100%", thickness=0.7, color="#CCCCCC"))
            story.append(Spacer(1, 6))
            i += 1
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            flush_para()
            flush_list()
            story.append(
                Paragraph(_inline_md_to_reportlab(m.group(2).strip()), styles[f"h{len(m.group(1))}"])
            )
            i += 1
            continue

        if line.lstrip().startswith(">"):
            flush_para()
            flush_list()
            quote_lines = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                quote_lines.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            story.append(
                Paragraph(_inline_md_to_reportlab(" ".join(quote_lines)), styles["quote"])
            )
            continue

        m_ul = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if m_ul:
            flush_para()
            if list_buf and list_ordered:
                flush_list()
            list_ordered = False
            list_buf.append(m_ul.group(1))
            i += 1
            continue

        m_ol = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if m_ol:
            flush_para()
            if list_buf and not list_ordered:
                flush_list()
            list_ordered = True
            list_buf.append(m_ol.group(1))
            i += 1
            continue

        if not line.strip():
            flush_para()
            flush_list()
            i += 1
            continue

        if list_buf and re.match(r"^\s{2,}\S", line):
            list_buf[-1] = list_buf[-1] + " " + line.strip()
            i += 1
            continue

        flush_list()
        para_buf.append(line)
        i += 1

    if in_code:
        story.append(Preformatted("\n".join(code_lines) if code_lines else " ", styles["code"]))
    flush_para()
    flush_list()

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=1.6 * cm,
        rightMargin=1.6 * cm,
        topMargin=1.6 * cm,
        bottomMargin=1.6 * cm,
        title=pdf_path.stem,
    )
    doc.build(story)


def markdown_to_pdf(markdown_text: str, pdf_path: str | Path) -> Path:
    """Write the full markdown document to PDF. Prefers xhtml2pdf for fidelity."""
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import xhtml2pdf  # noqa: F401
        import markdown  # noqa: F401

        _write_pdf_xhtml2pdf(markdown_text, pdf_path)
        return pdf_path
    except Exception:
        _write_pdf_reportlab(markdown_text, pdf_path)
        return pdf_path


def markdown_file_to_pdf(md_path: str | Path, pdf_path: str | Path | None = None) -> Path:
    md_path = Path(md_path)
    pdf_path = Path(pdf_path) if pdf_path else md_path.with_suffix(".pdf")
    text = md_path.read_text(encoding="utf-8")
    return markdown_to_pdf(text, pdf_path)

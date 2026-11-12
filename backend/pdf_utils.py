from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

LOGO_DIR = Path(__file__).parent / "assets" / "logo"
AMBER = colors.HexColor("#F59E0B")
INK = colors.HexColor("#0F172A")

TITLE_STYLE = ParagraphStyle("kop_title", fontName="Helvetica-Bold", fontSize=12.5, leading=15, alignment=TA_CENTER)
SUB_STYLE = ParagraphStyle("kop_sub", fontName="Helvetica-Bold", fontSize=10.5, leading=13, alignment=TA_CENTER, textColor=INK)
ADDR_STYLE = ParagraphStyle("kop_addr", fontName="Helvetica", fontSize=8, leading=10, alignment=TA_CENTER, textColor=colors.HexColor("#334155"))
REPORT_TITLE_STYLE = ParagraphStyle("report_title", fontName="Helvetica-Bold", fontSize=13, leading=16, alignment=TA_CENTER, spaceAfter=2)
META_STYLE = ParagraphStyle("meta", fontName="Helvetica", fontSize=8.5, leading=12, alignment=TA_LEFT, textColor=colors.HexColor("#475569"))
SECTION_STYLE = ParagraphStyle("section", fontName="Helvetica-Bold", fontSize=10.5, leading=13, spaceBefore=10, spaceAfter=6)
SIGN_STYLE = ParagraphStyle("sign", fontName="Helvetica", fontSize=9, leading=13, alignment=TA_CENTER)


def _letterhead():
    kab_logo = str(LOGO_DIR / "kab-banjar.png")
    bpbd_logo = str(LOGO_DIR / "bpbd-banjar.png")
    text_cell = [
        Paragraph("PEMERINTAH KABUPATEN BANJAR", TITLE_STYLE),
        Paragraph("BADAN PENANGGULANGAN BENCANA DAERAH (BPBD)", SUB_STYLE),
        Paragraph("Jl. Sekumpul Ujung No. 8D, Martapura, Kabupaten Banjar, Kalimantan Selatan", ADDR_STYLE),
    ]
    head = Table(
        [[Image(kab_logo, width=15 * mm, height=15 * mm), text_cell, Image(bpbd_logo, width=15 * mm, height=15 * mm)]],
        colWidths=[20 * mm, None, 20 * mm],
    )
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (0, 0), "LEFT"), ("ALIGN", (2, 0), (2, 0), "RIGHT")]))
    rule = Table([[""]], colWidths=["100%"], rowHeights=[2])
    rule.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 2, AMBER)]))
    rule2 = Table([[""]], colWidths=["100%"], rowHeights=[1])
    rule2.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#CBD5E1"))]))
    return [head, Spacer(1, 4), rule, Spacer(1, 1.5), rule2, Spacer(1, 12)]


def _meta_table(meta_pairs):
    rows = [[Paragraph(f"<b>{k}</b>", META_STYLE), Paragraph(f": {v}", META_STYLE)] for k, v in meta_pairs]
    t = Table(rows, colWidths=[32 * mm, None])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    return t


def _data_table(headers, rows, col_widths=None):
    body_style = ParagraphStyle("cell", fontName="Helvetica", fontSize=8, leading=10)
    head_style = ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=8.2, leading=10, textColor=colors.white)
    data = [[Paragraph(str(h), head_style) for h in headers]]
    for r in rows:
        data.append([Paragraph(str(c) if c not in (None, "") else "-", body_style) for c in r])
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
    ]
    t.setStyle(TableStyle(style))
    return t


def _signature_block(printed_by):
    rows = [
        [Paragraph("Mengetahui,", SIGN_STYLE), ""],
        [Paragraph("Kepala Pelaksana BPBD Kabupaten Banjar", SIGN_STYLE), Paragraph(f"Dicetak oleh sistem SIPOSTLOG", SIGN_STYLE)],
        [Spacer(1, 30), Spacer(1, 30)],
        [Paragraph("( _______________________________ )", SIGN_STYLE), Paragraph(f"<b>{printed_by}</b>", SIGN_STYLE)],
    ]
    t = Table(rows, colWidths=["50%", "50%"])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def build_pdf_report(report_title, sections, printed_by, landscape_mode=True):
    """sections: list of {title, meta: [(k,v)], headers, rows, col_widths}"""
    buf = BytesIO()
    pagesize = landscape(A4) if landscape_mode else A4
    doc = SimpleDocTemplate(buf, pagesize=pagesize, topMargin=16 * mm, bottomMargin=14 * mm, leftMargin=14 * mm, rightMargin=14 * mm)
    elements = _letterhead()
    elements.append(Paragraph(report_title, REPORT_TITLE_STYLE))
    elements.append(Spacer(1, 10))
    for idx, sec in enumerate(sections):
        if idx > 0:
            elements.append(PageBreak())
            elements.extend(_letterhead())
            elements.append(Paragraph(report_title, REPORT_TITLE_STYLE))
            elements.append(Spacer(1, 10))
        if sec.get("title"):
            elements.append(Paragraph(sec["title"], SECTION_STYLE))
        if sec.get("meta"):
            elements.append(_meta_table(sec["meta"]))
            elements.append(Spacer(1, 8))
        elements.append(_data_table(sec["headers"], sec["rows"], sec.get("col_widths")))
        elements.append(Spacer(1, 4))
        elements.append(Paragraph(f"Total {len(sec['rows'])} baris data.", META_STYLE))
    elements.append(Spacer(1, 20))
    elements.append(_signature_block(printed_by))
    doc.build(elements)
    return buf.getvalue()

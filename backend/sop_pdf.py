"""Downloadable user module (PDF) built from sop_content."""
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from pdf_utils import INK, REPORT_TITLE_STYLE, SIGN_STYLE, _letterhead
from sop_content import ROLE_ACCESS, SECTIONS, SOP_META

ROLE_NAMES = {"admin": "Admin", "petugas": "Petugas", "opname": "Opname"}
BODY = ParagraphStyle("sop_body", fontName="Helvetica", fontSize=9.5, leading=13.5, spaceAfter=4)
SMALL = ParagraphStyle("sop_small", fontName="Helvetica", fontSize=8.5, leading=11)
H2 = ParagraphStyle("sop_h2", fontName="Helvetica-Bold", fontSize=11.5, leading=15, spaceBefore=12, spaceAfter=4, textColor=INK)
LABEL = ParagraphStyle("sop_label", fontName="Helvetica-Bold", fontSize=9, leading=12, spaceBefore=4, spaceAfter=2, textColor=colors.HexColor("#334155"))
TAG = ParagraphStyle("sop_tag", fontName="Helvetica-Oblique", fontSize=8, leading=10, textColor=colors.HexColor("#64748B"), spaceAfter=4)
SUB = ParagraphStyle("sop_sub", fontName="Helvetica", fontSize=9.5, leading=12, alignment=TA_CENTER, textColor=colors.HexColor("#475569"))
GRID = colors.HexColor("#CBD5E1")


def _p(text, style=BODY):
    return Paragraph(escape(text), style)


def _numbered(items, style=BODY):
    rows = [[Paragraph(f"{i}.", style), Paragraph(escape(t), style)] for i, t in enumerate(items, start=1)]
    t = Table(rows, colWidths=[8 * mm, None])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    return t


def _bullets(items):
    rows = [[Paragraph("•", BODY), Paragraph(escape(t), BODY)] for t in items]
    t = Table(rows, colWidths=[6 * mm, None])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    return t


def _identity_table():
    m = SOP_META
    blank = "........................................"
    rows = [
        ["Nomor SOP", m["doc_number"] or blank],
        ["Versi / Revisi", m["version"]],
        ["Tanggal Berlaku", m["effective"]],
        ["Disahkan oleh", m["approver_title"]],
        ["Berlaku untuk", "Admin, Petugas, dan Petugas Opname SIPOSTLOG"],
    ]
    t = Table([[Paragraph(f"<b>{escape(k)}</b>", SMALL), Paragraph(escape(v), SMALL)] for k, v in rows], colWidths=[40 * mm, None])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, GRID), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F1F5F9")),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return t


def _access_table():
    head = ParagraphStyle("h", parent=SMALL, fontName="Helvetica-Bold", textColor=colors.white)
    data = [[Paragraph("Kemampuan", head), Paragraph("Admin", head), Paragraph("Petugas", head), Paragraph("Opname", head)]]
    for label, *flags in ROLE_ACCESS:
        data.append([Paragraph(escape(label), SMALL)] + [Paragraph("Ya" if f else "—", SMALL) for f in flags])
    t = Table(data, colWidths=[None, 20 * mm, 20 * mm, 20 * mm], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), INK), ("GRID", (0, 0), (-1, -1), 0.5, GRID),
                           ("ALIGN", (1, 1), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                           ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return t


def _approval_block():
    rows = [
        ["", Paragraph("Ditetapkan di Martapura", SIGN_STYLE)],
        ["", Paragraph("pada tanggal ....................", SIGN_STYLE)],
        ["", Paragraph(escape(SOP_META["approver_title"]), SIGN_STYLE)],
        ["", Spacer(1, 32)],
        ["", Paragraph("( _______________________________ )", SIGN_STYLE)],
        ["", Paragraph("NIP. ..............................", SIGN_STYLE)],
    ]
    t = Table(rows, colWidths=[None, 80 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawString(16 * mm, 10 * mm, f"SOP Penggunaan SIPOSTLOG · Versi {SOP_META['version']}")
    canvas.drawRightString(A4[0] - 16 * mm, 10 * mm, f"Halaman {doc.page}")
    canvas.restoreState()


def build_sop_pdf(printed_by: str) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=14 * mm, bottomMargin=18 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
                            title=SOP_META["title"], author="BPBD Kabupaten Banjar")
    el = _letterhead()
    el.append(Paragraph(escape(SOP_META["title"]), REPORT_TITLE_STYLE))
    el.append(Paragraph(escape(SOP_META["subtitle"]), SUB))
    el.append(Spacer(1, 10))
    el.append(_identity_table())
    el.append(Spacer(1, 8))
    el.append(Paragraph("<b>Daftar Isi</b>", LABEL))
    el.append(_numbered([s["title"] for s in SECTIONS], SMALL))
    el.append(PageBreak())

    for n, sec in enumerate(SECTIONS, start=1):
        block = [Paragraph(f"{n}. {escape(sec['title'])}", H2),
                 Paragraph("Berlaku untuk: " + ", ".join(ROLE_NAMES[r] for r in sec["roles"]), TAG)]
        for para in sec.get("paragraphs", []):
            block.append(_p(para))
        if sec.get("access_table"):
            block.append(_access_table())
        el.append(KeepTogether(block))
        if sec.get("steps"):
            el.append(Paragraph("Langkah-langkah", LABEL))
            el.append(_numbered(sec["steps"]))
        if sec.get("admin_steps"):
            el.append(Paragraph("Khusus Admin", LABEL))
            el.append(_numbered(sec["admin_steps"]))
        if sec.get("rules"):
            el.append(Paragraph("Ketentuan", LABEL))
            el.append(_bullets(sec["rules"]))

    el.append(Spacer(1, 18))
    el.append(Paragraph(f"Dicetak dari SIPOSTLOG oleh {escape(printed_by)} pada {datetime.now().strftime('%d/%m/%Y %H:%M')}.", TAG))
    el.append(Spacer(1, 6))
    el.append(KeepTogether([_approval_block()]))
    doc.build(el, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()

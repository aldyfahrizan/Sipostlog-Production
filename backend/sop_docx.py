"""Editable Word (.docx) version of the SOP user module, built from sop_content."""
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor

from report_dates import dicetak_wita
from sop_content import ROLE_ACCESS, SECTIONS, SOP_META

LOGO_DIR = Path(__file__).parent / "assets" / "logo"
ROLE_NAMES = {"admin": "Admin", "petugas": "Petugas", "opname": "Opname"}
INK = RGBColor(0x0F, 0x17, 0x2A)
MUTED = RGBColor(0x64, 0x74, 0x8B)


def _shade(cell, hex_fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tc_pr.append(shd)


def _set_widths(table, widths):
    """Fixed column widths that both Word and LibreOffice honour."""
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tbl_pr.append(layout)
    for col, w in zip(table.columns, widths):
        col.width = w
        for cell in col.cells:
            cell.width = w


def _bottom_rule(paragraph, color="F59E0B", size=12):
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", str(size)), ("w:space", "1"), ("w:color", color)):
        bottom.set(qn(k), v)
    borders.append(bottom)
    p_pr.append(borders)


def _letterhead(doc):
    t = doc.add_table(rows=1, cols=3)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_widths(t, (Mm(22), Mm(130), Mm(22)))
    for idx, name in ((0, "kab-banjar.png"), (2, "bpbd-banjar.png")):
        logo = LOGO_DIR / name
        if logo.exists():
            t.rows[0].cells[idx].paragraphs[0].add_run().add_picture(str(logo), width=Mm(16))
    mid = t.rows[0].cells[1]
    lines = [("PEMERINTAH KABUPATEN BANJAR", 12.5, True), ("BADAN PENANGGULANGAN BENCANA DAERAH (BPBD)", 10.5, True),
             ("Jl. Sekumpul Ujung No. 8D, Martapura, Kabupaten Banjar, Kalimantan Selatan", 8, False)]
    for i, (text, size, bold) in enumerate(lines):
        p = mid.paragraphs[0] if i == 0 else mid.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(0)
        r = p.add_run(text)
        r.font.size, r.bold = Pt(size), bold
    rule = doc.add_paragraph()
    _bottom_rule(rule)


def _heading(doc, text, size=12, color=INK, space_before=10):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.keep_with_next = True
    r = p.add_run(text)
    r.bold, r.font.size, r.font.color.rgb = True, Pt(size), color
    return p


def _label(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.keep_with_next = True
    r = p.add_run(text)
    r.bold, r.font.size, r.font.color.rgb = True, Pt(9.5), RGBColor(0x33, 0x41, 0x55)


def _list(doc, items, style):
    for t in items:
        p = doc.add_paragraph(t, style=style)
        p.paragraph_format.space_after = Pt(2)


def _grid_table(doc, rows, header=None, widths=None):
    t = doc.add_table(rows=0, cols=len(rows[0]))
    t.style = "Table Grid"
    if header:
        cells = t.add_row().cells
        for c, text in zip(cells, header):
            c.text = ""
            run = c.paragraphs[0].add_run(text)
            run.bold, run.font.color.rgb, run.font.size = True, RGBColor(0xFF, 0xFF, 0xFF), Pt(9)
            _shade(c, "0F172A")
    for row in rows:
        cells = t.add_row().cells
        for c, text in zip(cells, row):
            c.text = ""
            run = c.paragraphs[0].add_run(str(text))
            run.font.size = Pt(9)
    if widths:
        _set_widths(t, widths)
    return t


def build_sop_docx(printed_by: str) -> bytes:
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.left_margin = sec.right_margin = Mm(18)
    sec.top_margin, sec.bottom_margin = Mm(14), Mm(16)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Arial", Pt(10)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")

    footer = sec.footer.paragraphs[0]
    footer.text = f"SOP Penggunaan SIPOSTLOG · Versi {SOP_META['version']}"
    footer.runs[0].font.size, footer.runs[0].font.color.rgb = Pt(8), MUTED

    _letterhead(doc)
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = title.add_run(SOP_META["title"])
    r.bold, r.font.size = True, Pt(14)
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = sub.add_run(SOP_META["subtitle"])
    r.font.size, r.font.color.rgb = Pt(10), MUTED

    ident = [
        ("Nomor SOP", SOP_META["doc_number"] or "........................................"),
        ("Versi / Revisi", SOP_META["version"]),
        ("Tanggal Berlaku", SOP_META["effective"]),
        ("Disahkan oleh", SOP_META["approver_title"]),
        ("Berlaku untuk", "Admin, Petugas, dan Petugas Opname SIPOSTLOG"),
    ]
    t = _grid_table(doc, ident, widths=(Mm(45), Mm(129)))
    for row in t.rows:
        row.cells[0].paragraphs[0].runs[0].bold = True
        _shade(row.cells[0], "F1F5F9")

    _heading(doc, "Daftar Isi", size=10.5)
    _list(doc, [s["title"] for s in SECTIONS], "List Number")
    doc.add_page_break()

    for n, s in enumerate(SECTIONS, start=1):
        _heading(doc, f"{n}. {s['title']}")
        tag = doc.add_paragraph()
        tag.paragraph_format.space_after = Pt(3)
        r = tag.add_run("Berlaku untuk: " + ", ".join(ROLE_NAMES[x] for x in s["roles"]))
        r.italic, r.font.size, r.font.color.rgb = True, Pt(8.5), MUTED
        for para in s.get("paragraphs", []):
            doc.add_paragraph(para)
        if s.get("access_table"):
            rows = [[label] + ["Ya" if f else "—" for f in flags] for label, *flags in ROLE_ACCESS]
            _grid_table(doc, rows, header=["Kemampuan", "Admin", "Petugas", "Opname"], widths=(Mm(108), Mm(22), Mm(22), Mm(22)))
        # Restart numbering per list by using plain numbered text (Word's shared list numbering continues otherwise).
        for key, label in (("steps", "Langkah-langkah"), ("admin_steps", "Khusus Admin")):
            if s.get(key):
                _label(doc, label)
                for i, step in enumerate(s[key], start=1):
                    p = doc.add_paragraph(f"{i}.\t{step}")
                    p.paragraph_format.left_indent = Mm(7)
                    p.paragraph_format.first_line_indent = Mm(-7)
                    p.paragraph_format.space_after = Pt(2)
        if s.get("rules"):
            _label(doc, "Ketentuan")
            _list(doc, s["rules"], "List Bullet")

    note = doc.add_paragraph()
    note.paragraph_format.space_before = Pt(14)
    r = note.add_run(f"Dicetak dari SIPOSTLOG oleh {printed_by} pada {dicetak_wita()}.")
    r.italic, r.font.size, r.font.color.rgb = True, Pt(8.5), MUTED

    for text, space in (("Ditetapkan di Martapura", 10), ("pada tanggal ....................", 0),
                        (SOP_META["approver_title"], 0), ("", 36), ("( _______________________________ )", 0),
                        ("NIP. ..............................", 0)):
        p = doc.add_paragraph(text)
        p.paragraph_format.left_indent = Mm(95)
        p.paragraph_format.space_before = Pt(space)
        p.paragraph_format.space_after = Pt(0)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()

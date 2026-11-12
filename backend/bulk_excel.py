"""Excel templates and parsing for bulk Barang Masuk / Penyaluran imports (one file = one BAST)."""
import re
from datetime import date, datetime, time
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from report_dates import dicetak_wita
from excel_utils import BORDER, HEADER_FILL, HEADER_FONT, TITLE_FONT

FILL_ME = PatternFill("solid", fgColor="FEF3C7")
LABEL_FONT = Font(bold=True, color="0F172A")

# kind -> (judul, kolom jumlah, daftar kepala (key, label, wajib))
KINDS = {
    "in": {
        "title": "Template Impor Barang Masuk",
        "qty_header": "Jumlah Masuk (Isi)",
        "fields": [
            ("source", "Sumber", True),
            ("date", "Tanggal (DD/MM/YYYY)", True),
            ("ref", "No. BAST/Dokumen", False),
        ],
    },
    "out": {
        "title": "Template Impor Penyaluran",
        "qty_header": "Jumlah Disalurkan (Isi)",
        "fields": [
            ("destination", "Lokasi Tujuan", True),
            ("incident_type", "Jenis Kejadian", True),
            ("date", "Tanggal (DD/MM/YYYY)", True),
            ("time", "Jam (HH:MM, WITA)", True),
            ("recipient_kk", "Jumlah KK", False),
            ("recipient_jiwa", "Jumlah Jiwa", False),
            ("ref", "No. BAST/Dokumen", False),
        ],
    },
}
TABLE_HEADERS = ["ID Item", "Nama Item", "Kategori", "Satuan", "Stok Sistem"]
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_QUANTITY = 1_000_000_000


def _norm(value) -> str:
    text = str(value).strip().lower() if value is not None else ""
    return text.rstrip(":").strip()


def build_bulk_template(kind: str, items: list, printed_by: str, incident_types: list) -> bytes:
    spec = KINDS[kind]
    wb = Workbook()
    ws = wb.active
    ws.title = "Barang Masuk" if kind == "in" else "Penyaluran"
    ws.append([spec["title"]])
    ws["A1"].font = TITLE_FONT
    ws.append([f"BPBD Kabupaten Banjar — SIPOSTLOG · Dicetak {dicetak_wita()} oleh {printed_by}"])
    ws.append(["Petunjuk: isi kotak kuning. Satu berkas = satu BAST. Jangan ubah kolom ID Item; kosongkan jumlah untuk barang yang tidak ada di BAST."])
    ws.append([])
    incident_row = None
    for key, label, required in spec["fields"]:
        ws.append([label + (" *" if required else ""), None])
        r = ws.max_row
        ws.cell(row=r, column=1).font = LABEL_FONT
        cell = ws.cell(row=r, column=2)
        cell.fill = FILL_ME
        cell.border = BORDER
        if key in ("date", "time"):
            cell.number_format = "@"
        if key == "incident_type":
            incident_row = r
    if incident_row:
        dv = DataValidation(type="list", formula1='"' + ",".join(incident_types) + '"', allow_blank=False)
        dv.error = "Pilih jenis kejadian dari daftar."
        dv.errorTitle = "Jenis Kejadian"
        dv.showErrorMessage = True
        ws.add_data_validation(dv)
        dv.add(f"B{incident_row}")
    ws.append([])
    headers = TABLE_HEADERS + [spec["qty_header"], "Catatan"]
    ws.append(headers)
    header_row = ws.max_row
    for col in range(1, len(headers) + 1):
        c = ws.cell(row=header_row, column=col)
        c.fill, c.font, c.border = HEADER_FILL, HEADER_FONT, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for i in items:
        ws.append([i["id"], i["name"], i["category"], i["unit"], i["currentStock"], None, None])
        r = ws.max_row
        for col in range(1, len(headers) + 1):
            ws.cell(row=r, column=col).border = BORDER
        ws.cell(row=r, column=6).fill = FILL_ME
    for col, width in zip("ABCDEFG", (26, 34, 22, 10, 13, 22, 30)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def parse_bulk_upload(kind: str, content: bytes) -> dict:
    """Return {"header": {key: raw}, "rows": [{"row", "item_id", "quantity", "note"}]}; raises ValueError on layout problems."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("Ukuran berkas melebihi 2 MB.")
    spec = KINDS[kind]
    wb = load_workbook(BytesIO(content), data_only=True)
    ws = wb.active
    labels = {_norm(label): key for key, label, _ in spec["fields"]}
    labels.update({_norm(label) + " *": key for key, label, _ in spec["fields"]})
    header = {}
    header_row, cols = None, {}
    for row in ws.iter_rows(min_row=1, max_row=40):
        first = _norm(row[0].value)
        if first == "id item":
            header_row = row[0].row
            cols = {_norm(c.value): idx for idx, c in enumerate(row) if c.value is not None}
            break
        if first in labels and len(row) > 1:
            header[labels[first]] = row[1].value
    if header_row is None:
        raise ValueError("Header tabel 'ID Item' tidak ditemukan. Gunakan template dari aplikasi.")
    qty_col = cols.get(_norm(spec["qty_header"]))
    other_kind = qty_col is None and _norm(KINDS["out" if kind == "in" else "in"]["qty_header"]) in cols
    missing = [label for key, label, required in spec["fields"] if required and key not in header]
    if missing and not other_kind:
        raise ValueError("Baris isian tidak ditemukan di berkas: " + ", ".join(missing) + ". Gunakan template dari aplikasi dan jangan ubah tulisan di kolom A.")
    if qty_col is None:
        other = KINDS["out" if kind == "in" else "in"]["qty_header"]
        if _norm(other) in cols:
            raise ValueError("Berkas ini adalah template " + ("Penyaluran" if kind == "in" else "Barang Masuk") + ". Unggah di panel yang sesuai.")
        raise ValueError(f"Kolom '{spec['qty_header']}' tidak ditemukan.")
    note_col = cols.get("catatan")
    rows = []
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        if not row or len(row) <= qty_col:
            continue
        item_id = row[cols["id item"]]
        qty = row[qty_col]
        if qty in (None, "") or (isinstance(qty, str) and not qty.strip()):
            continue
        note = row[note_col] if note_col is not None and note_col < len(row) else None
        rows.append({
            "item_id": str(item_id).strip() if item_id not in (None, "") else "",
            "quantity": qty,
            "note": str(note).strip() if note not in (None, "") else "",
        })
    for idx, r in enumerate(rows, start=1):
        r["row"] = idx
    return {"header": header, "rows": rows}


# ---------- value coercion (raise ValueError with a user-facing message) ----------
def to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError("Tanggal tidak valid. Gunakan format DD/MM/YYYY.")


def to_time(value) -> str:
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, (int, float)):
        raise ValueError(f"Jam tertulis sebagai angka ({value}), bukan jam. Ketik sebagai teks dengan format HH:MM, mis. 14:30.")
    text = str(value or "").strip().replace(".", ":")
    if not re.fullmatch(r"\d{1,2}:\d{2}", text):
        raise ValueError("Jam tidak valid. Gunakan format HH:MM, mis. 14:30.")
    try:
        return datetime.strptime(text, "%H:%M").strftime("%H:%M")
    except ValueError:
        raise ValueError("Jam tidak valid. Gunakan format HH:MM, mis. 14:30.")


def to_int(value, *, positive: bool) -> int:
    if isinstance(value, bool):
        raise ValueError
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError
        value = int(value)
    if isinstance(value, str):
        value = int(value.strip()) if value.strip().isdigit() else None
    if not isinstance(value, int):
        raise ValueError
    if value < (1 if positive else 0):
        raise ValueError
    if value > MAX_QUANTITY:
        raise ValueError("Jumlah terlalu besar. Paling banyak 1.000.000.000 per baris.")
    return value

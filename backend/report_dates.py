"""Dates on reports: written in Indonesian words, always in the warehouse's WITA timezone (UTC+8)."""
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

WITA = timezone(timedelta(hours=8))
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today_wita() -> date:
    return now_utc().astimezone(WITA).date()


def tanggal_id(d: date) -> str:
    """30 September 2026"""
    return f"{d.day} {BULAN[d.month - 1]} {d.year}"


def dicetak_wita() -> str:
    """Value of every "Dicetak" line: 13/11/2026 01:30 WITA"""
    return now_utc().astimezone(WITA).strftime("%d/%m/%Y %H:%M WITA")


def parse_report_date(value: Optional[str]) -> date:
    """The `date` query parameter of a stock report; empty means today in WITA."""
    today = today_wita()
    if not value:
        return today
    try:
        if len(value) != 10:
            raise ValueError(value)
        d = date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Tanggal laporan tidak valid. Gunakan format YYYY-MM-DD, misalnya 2026-09-30.")
    if d > today:
        raise HTTPException(status_code=400, detail="Tanggal laporan tidak boleh melewati hari ini.")
    return d


def next_day_start_utc(d: date) -> datetime:
    """00:00 WITA of the day after `d`: anything from here on happened after the report date."""
    return datetime(d.year, d.month, d.day, tzinfo=WITA).astimezone(timezone.utc) + timedelta(days=1)


def periode_id(start: Optional[str], end: Optional[str]) -> str:
    """Period of a report in words. `start`/`end` are YYYY-MM-DD strings already validated by the caller."""
    if start and end:
        return f"Periode {tanggal_id(date.fromisoformat(start[:10]))} s.d. {tanggal_id(date.fromisoformat(end[:10]))}"
    last = date.fromisoformat(end[:10]) if end else today_wita()
    return f"Sampai Dengan Tanggal {tanggal_id(last)}"

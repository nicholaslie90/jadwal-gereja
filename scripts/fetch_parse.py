#!/usr/bin/env python3
"""Download the church roster spreadsheet and emit our duties as JSON on stdout.

Stdlib only -- an .xlsx is a zip of XML, so zipfile + ElementTree is enough and
saves a dependency install in CI.

Usage:
    python3 scripts/fetch_parse.py            # download, parse, print JSON
    python3 scripts/fetch_parse.py file.xlsx  # parse a local copy instead
"""

import io
import json
import re
import sys
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

SHEET_ID = "1dM5Upi_XoIe9VOo3gSVmXP2igBmXCZ4p"
EXPORT_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=xlsx"

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

WIB = timezone(timedelta(hours=7))
EXCEL_EPOCH = date(1899, 12, 30)

MONTHS = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
    7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November",
    12: "Desember",
}
# Longest-first so AGUSTUS wins over AGS, SEPTEMBER over SEPT over SEP.
MONTH_TOKENS = sorted(
    [
        ("JANUARI", 1), ("JAN", 1), ("FEBRUARI", 2), ("FEB", 2),
        ("MARET", 3), ("MAR", 3), ("APRIL", 4), ("APR", 4),
        ("MEI", 5), ("MAY", 5), ("JUNI", 6), ("JUN", 6),
        ("JULI", 7), ("JUL", 7), ("AGUSTUS", 8), ("AGS", 8), ("AUG", 8),
        ("SEPTEMBER", 9), ("SEPT", 9), ("SEP", 9),
        ("OKTOBER", 10), ("OKT", 10), ("OCT", 10),
        ("NOVEMBER", 11), ("NOV", 11), ("DESEMBER", 12), ("DES", 12), ("DEC", 12),
    ],
    key=lambda kv: -len(kv[0]),
)

# Verified across all 71 tabs: "Nicholas X" / "Nicholas Xie" is a different person
# and always sits in the PUJIAN column. Strip those before matching me.
NOT_ME = re.compile(r"\bnic(?:h)?olas\s+xie?\b", re.I)
ME = re.compile(r"\bnic(?:h)?olas\b", re.I)
# \bcindy\b will not match "Cindiana" or "Cindi" -- no trailing y. That is the
# whole guard; Cindiana is Eric's wife.
WIFE = re.compile(r"\bcindy\b", re.I)

TIME_RE = re.compile(r"(\d{1,2})[.:](\d{2})\s*[-–—]\s*(\d{1,2})[.:](\d{2})")
TITLE_MONTH_RE = re.compile(r"BULAN\s+([A-Z]+)\s*(\d{4})", re.I)
COL_RE = re.compile(r"([A-Z]+)")


def col_num(ref):
    """'AB12' -> 28 (1-based column index)."""
    n = 0
    for ch in COL_RE.match(ref).group(1):
        n = n * 26 + ord(ch) - 64
    return n


class Workbook:
    def __init__(self, blob):
        self.z = zipfile.ZipFile(blob)
        wb = ET.fromstring(self.z.read("xl/workbook.xml"))
        rels = {
            r.get("Id"): r.get("Target")
            for r in ET.fromstring(self.z.read("xl/_rels/workbook.xml.rels"))
        }
        self.sheets = []
        for sh in wb.find(NS + "sheets"):
            target = rels[sh.get(RID)].lstrip("/")
            path = target if target.startswith("xl/") else "xl/" + target
            self.sheets.append((sh.get("name"), path))
        try:
            shared = ET.fromstring(self.z.read("xl/sharedStrings.xml"))
            self.strings = [
                "".join(t.text or "" for t in si.iter(NS + "t")) for si in shared
            ]
        except KeyError:
            self.strings = []

    def grid(self, path):
        """Sheet -> list of {col_index: text}, one dict per row, 1-based rows."""
        ws = ET.fromstring(self.z.read(path))
        rows = {}
        for row in ws.iter(NS + "row"):
            cells = {}
            for c in row.iter(NS + "c"):
                t = c.get("t")
                if t == "inlineStr":
                    is_ = c.find(NS + "is")
                    val = "".join(x.text or "" for x in is_.iter(NS + "t")) if is_ is not None else ""
                else:
                    v = c.find(NS + "v")
                    if v is None or v.text is None:
                        val = ""
                    elif t == "s":
                        val = self.strings[int(v.text)]
                    else:
                        val = v.text
                val = val.strip()
                if val:
                    cells[col_num(c.get("r"))] = val
            rows[int(row.get("r"))] = cells
        if not rows:
            return []
        return [rows.get(i, {}) for i in range(1, max(rows) + 1)]


def sheet_month(grid, tab_name):
    """(year, month) from cell A1's title, falling back to the tab name."""
    title = grid[0].get(1, "") if grid else ""
    m = TITLE_MONTH_RE.search(title)
    if m:
        for token, num in MONTH_TOKENS:
            if m.group(1).upper() == token:
                return int(m.group(2)), num
    upper = tab_name.upper()
    year = re.search(r"(\d{2,4})\s*$", upper)
    if not year:
        return None
    y = int(year.group(1))
    y = y + 2000 if y < 100 else y
    for token, num in MONTH_TOKENS:
        if token in upper:
            return y, num
    return None


def parse_date(raw, year, month):
    """Cell text -> date. Handles Excel serials and strings like '1 MEI'."""
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        serial = float(raw)
    except ValueError:
        pass
    else:
        if serial > 20000:  # anything smaller is a bare day-of-month, not a serial
            return EXCEL_EPOCH + timedelta(days=int(serial))
    m = re.match(r"^(\d{1,2})\b", raw)
    if not m:
        return None
    day = int(m.group(1))
    upper = raw.upper()
    for token, num in MONTH_TOKENS:
        if token in upper:
            month = num
            break
    try:
        return date(year, month, day)
    except ValueError:
        return None


def block_label(grid, header_row, col):
    """Name of a sub-table, read from the nearest text label above its header."""
    for r in range(header_row - 1, max(header_row - 5, 0), -1):
        text = grid[r - 1].get(col, "")
        # 'Rabu / 20.00-21.15' is the schedule line, not the label -- skip digits.
        if text and not any(ch.isdigit() for ch in text):
            return text
    return "Lainnya"


def find_blocks(grid):
    """Every cell reading 'TGL' starts a sub-table. Yields (label, headers, rows).

    This is what lets one code path cover the main service table plus the PBK and
    PAMS tables sitting side-by-side below it, without hardcoding columns that
    move between months.
    """
    for row_idx, cells in enumerate(grid, start=1):
        tgl_cols = sorted(c for c, v in cells.items() if v.upper() == "TGL")
        for i, start in enumerate(tgl_cols):
            limit = tgl_cols[i + 1] if i + 1 < len(tgl_cols) else 10**6
            headers, cols = [], []
            col = start
            while col < limit and cells.get(col):
                headers.append(cells[col])
                cols.append(col)
                col += 1
            if len(headers) < 2:
                continue
            label = "Ibadah" if any(h.upper() == "HARI" for h in headers) \
                else block_label(grid, row_idx, start)
            yield label, headers, cols, row_idx


def read_block(grid, cols, header_row):
    """Rows below a header, stopping after two consecutive dateless rows."""
    out, blanks = [], 0
    for row_idx in range(header_row + 1, len(grid) + 1):
        cells = grid[row_idx - 1]
        first = cells.get(cols[0], "")
        if first.upper() == "TGL":
            break
        if not first:
            blanks += 1
            if blanks >= 2:
                break
            continue
        blanks = 0
        out.append(cells)
    return out


def parse(blob, today):
    wb = Workbook(blob)
    months, mine = [], []
    for tab, path in wb.sheets:
        grid = wb.grid(path)
        ym = sheet_month(grid, tab)
        if not ym:
            continue
        year, month = ym
        if (year, month) < (today.year, today.month):
            continue
        label = f"{MONTHS[month]} {year}"
        blocks = []
        for name, headers, cols, header_row in find_blocks(grid):
            rows = []
            for cells in read_block(grid, cols, header_row):
                when = parse_date(cells.get(cols[0], ""), year, month)
                if not when:
                    continue
                values = [cells.get(c, "") for c in cols[1:]]
                rows.append({"date": when.isoformat(), "cells": values})
                collect_mine(mine, name, label, headers, cols, cells, when)
            if rows:
                blocks.append({"name": name, "headers": headers, "rows": rows})
        if blocks:
            months.append({"label": label, "month": f"{year}-{month:02d}", "blocks": blocks})

    mine.sort(key=lambda d: (d["date"], d["start"] or "", d["who"]))
    return {
        "generated": datetime.now(WIB).replace(microsecond=0).isoformat(),
        "mine": mine,
        "months": months,
    }


def collect_mine(out, block, month_label, headers, cols, cells, when):
    """Append one entry per (person, role) found in this row."""
    day = time_text = ""
    for header, col in zip(headers, cols):
        upper = header.upper()
        if upper == "HARI":
            day = cells.get(col, "")
        elif upper == "JAM":
            time_text = cells.get(col, "")
    if not time_text and block != "Ibadah":
        # PBK/PAMS carry their time in the label line, e.g. 'Rabu / 20.00-21.15'.
        time_text = block

    start = end = None
    m = TIME_RE.search(time_text)
    if m:
        start = f"{int(m.group(1)):02d}:{m.group(2)}"
        end = f"{int(m.group(3)):02d}:{m.group(4)}"

    for header, col in zip(headers, cols):
        if header.upper() in ("TGL", "HARI", "JAM", "ACARA"):
            continue
        raw = cells.get(col, "")
        if not raw:
            continue
        stripped = NOT_ME.sub("", raw)
        for who, hit in (("Nicholas", ME.search(stripped)), ("Cindy", WIFE.search(raw))):
            if hit:
                out.append({
                    "date": when.isoformat(), "day": day, "time": time_text,
                    "start": start, "end": end, "who": who, "role": header,
                    "raw": raw, "block": block, "month": month_label,
                })


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], "rb") as fh:
            blob = io.BytesIO(fh.read())
    else:
        with urllib.request.urlopen(EXPORT_URL, timeout=120) as resp:
            blob = io.BytesIO(resp.read())
    json.dump(parse(blob, date.today()), sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

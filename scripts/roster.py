#!/usr/bin/env python3
"""Derive the rostering rules from the spreadsheet, then draft a new month.

The sheet is the source of truth; rules.json is just its cached summary, so a
changed pool or a retired volunteer flows through on the next derive. Edit
rules.json by hand to override anything (pools, rates, pairs, headcounts).

Usage:
    python3 scripts/roster.py derive [sheet.xlsx] > rules.json
    python3 scripts/roster.py draft 2026-10 [rules.json]   # TSV, paste into Sheets
    python3 scripts/roster.py xlsx  2026-10 [rules.json] [out.xlsx]
    python3 scripts/roster.py --selftest
"""

import calendar
import io
import json
import re
import sys
import tempfile
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import date, timedelta

from fetch_parse import (EXPORT_URL, MONTHS, Workbook, find_blocks, parse_date,
                         read_block, sheet_month)

# Rules are read off the recent past only -- 2025's pools are stale.
WINDOW = 9          # months of history feeding the pools
TEMPLATE_WINDOW = 4 # months deciding which roles exist and how many fill them
ACTIVE = 3          # months of silence before we treat someone as retired
SERVICES = ("Jumat", "Sabtu Pagi", "Sabtu Siang")
SIDE = ("PBK", "PAMS", "PEMUDA")
SKIP = ("TGL", "HARI", "JAM", "ACARA")

# One person, many spellings. Titles (Pdt/Dkn/Dks/Sdr) are stripped first, so
# only the genuinely different spellings need a line here.
# Spelling variants live in aliases.json, which is encrypted in git for the
# same reason rules.json is: it is a list of the congregation's names.
# Missing file = every spelling is treated as its own person.
ALIAS_FILE = "aliases.json"
# Placeholders that sit in a person column but name an event or the congregation.
NOT_A_PERSON = re.compile(
    r"^(KKR|KPI|PAMS|PEMUDA|RYF|Gereja|Fam Day|Sie\.?\s*Acara|Sabtu|Rabu|Jumat|\d)", re.I
)
TITLE = re.compile(r"^(Pdt|Pr|Dkn|Dks|Dk|Sdri|Sdr)\.?\s+", re.I)


def aliases(path=None):
    try:
        with open(path or ALIAS_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        print(f"warning: {path or ALIAS_FILE} not found -- spellings will not be merged",
              file=sys.stderr)
        return {}


ALIAS = {}


def canon(raw):
    """'Dks. Lisa' -> 'Lisa'. Returns None for event placeholders."""
    name = re.sub(r"\(.*?\)", "", raw).strip()
    name = re.sub(r"\s+", " ", TITLE.sub("", name)).strip(" .")
    if not name or NOT_A_PERSON.match(name):
        return None
    key = name.lower().replace("-", " ").replace(".", "").strip()
    return ALIAS.get(key, name.title() if name.islower() else name)


def split_cell(text):
    for part in re.split(r"\s*(?:&|,|/| dan )\s*", text):
        who = canon(part)
        if who:
            yield who, part.strip()


def history(blob):
    """Every assignment as (date, slot, role, person, jam, spelling-in-sheet)."""
    wb = Workbook(blob)
    out = []
    for tab, path in wb.sheets:
        grid = wb.grid(path)
        ym = sheet_month(grid, tab)
        if not ym:
            continue
        year, month = ym
        for block, headers, cols, header_row in find_blocks(grid):
            for cells in read_block(grid, cols, header_row):
                when = parse_date(cells.get(cols[0], ""), year, month)
                if not when:
                    continue
                # The JAN26 tab carries 2025 serials; the tab's own month wins.
                try:
                    when = date(year, month, when.day)
                except ValueError:
                    continue
                slot = jam = ""
                for head, col in zip(headers, cols):
                    if head.upper() == "HARI":
                        slot = cells.get(col, "").strip().title()
                    elif head.upper() == "JAM":
                        jam = cells.get(col, "").strip()
                if block != "Ibadah":
                    slot = block
                elif slot not in SERVICES:
                    continue        # KKR / PAMS rows parked in the service table
                for head, col in zip(headers, cols):
                    role = head.upper().replace("’", "'").strip()
                    if role in SKIP:
                        continue
                    role = "PENYAMBUT TAMU" if role.startswith("PENYAMBUT") else role
                    for who, raw in split_cell(cells.get(col, "")):
                        out.append((when, slot, role, who, jam, raw))
    return out


def layout(blob):
    """The newest month tab's shape: title, header row, side-table placement.

    A draft has to paste into the sheet without reformatting, so the columns
    (including the repeated PENYAMBUT TAMU / PETUGAS SNACK pair) are copied
    from the last month rather than invented here.
    """
    blob.seek(0)
    wb = Workbook(blob)
    latest = None
    for tab, path in wb.sheets:
        grid = wb.grid(path)
        ym = sheet_month(grid, tab)
        if ym and (latest is None or ym > latest[0]):
            latest = (ym, grid)
    (_year, _month), grid = latest
    out = {"title": grid[0].get(1, ""), "header": [], "side": []}
    for block, headers, cols, header_row in find_blocks(grid):
        if block == "Ibadah":
            out["header"] = [(h, c) for h, c in zip(headers, cols)]
            continue
        when = grid[header_row - 2].get(cols[0], "")
        out["side"].append({
            "label": block, "when": when,
            "cols": [(h, c) for h, c in zip(headers, cols)],
        })
    return out


def derive(blob):
    rows = history(blob)
    if not rows:
        raise SystemExit("no assignments parsed -- sheet layout changed?")
    last = max(r[0] for r in rows)
    months = sorted({(d.year, d.month) for d, *_ in rows})[-WINDOW:]
    recent = [r for r in rows if (r[0].year, r[0].month) in months]
    template_months = set(months[-TEMPLATE_WINDOW:])
    late = [r for r in recent if (r[0].year, r[0].month) in template_months]

    # Headcount template: the usual number of names in a cell, for roles that
    # are actually still being filled. This is what drops Konsumsi/Snack from
    # the Sabtu services after the June 2026 layout change.
    per_cell, services = defaultdict(Counter), defaultdict(set)
    for when, slot, role, _who, _jam, _raw in late:
        per_cell[(slot, role)][when] += 1
        services[slot].add(when)
    template, order = defaultdict(dict), {}
    for (slot, role), seen in per_cell.items():
        if len(seen) < 0.5 * len(services[slot]):
            continue
        template[slot][role] = Counter(seen.values()).most_common(1)[0][0]

    pools, rate, last_served, jam = defaultdict(lambda: defaultdict(Counter)), Counter(), {}, {}
    # Spelling drifts (a title gets abbreviated), so follow the newest months.
    spelling, recent_spelling = defaultdict(Counter), defaultdict(Counter)
    for _w, _s, _r, who, _j, raw in late:
        spelling[who][raw] += 1
    for when, slot, role, who, when_jam, raw in recent:
        recent_spelling[who][raw] += 1
        pools[slot][role][who] += 1
        rate[who] += 1
        last_served[who] = max(last_served.get(who, when), when)
        if when_jam and slot in SERVICES:
            jam.setdefault(slot, Counter())[when_jam] += 1

    # Someone unseen for a quarter has moved, is ill or has handed the job over
    # -- absensi changed hands that way in June 2026. Keep them out of next month.
    cutoff = date(*months[-ACTIVE], 1)
    retired = {w for w, d in last_served.items() if d < cutoff}
    for slot in pools:
        for role in pools[slot]:
            for who in retired:
                pools[slot][role].pop(who, None)
    pools = {s: {r: c for r, c in roles.items() if c} for s, roles in pools.items()}
    for who in retired:
        rate.pop(who, None)

    pairs = Counter()
    cell = defaultdict(set)
    for when, slot, role, who, _jam, _raw in recent:
        cell[(when, slot, role)].add(who)
    for names in cell.values():
        for a in sorted(names):
            for b in sorted(names):
                if a < b:
                    pairs[(a, b)] += 1
    # A pair is only a rule if they nearly always turn up together.
    duo = [[a, b] for (a, b), n in pairs.items()
           if n >= 3 and n >= 0.75 * min(rate[a], rate[b])]

    # Who may be booked in two different services on one day -- only the people
    # who already do it. Two roles inside one service is handled separately.
    day_slots = defaultdict(set)
    for when, slot, _role, who, _jam, _raw in recent:
        if slot in SERVICES:
            day_slots[(who, when)].add(slot)
    multislot = sorted({who for (who, _d), slots in day_slots.items() if len(slots) > 1})
    return {
        "derived": date.today().isoformat(),
        "layout": layout(blob),
        "history_through": last.isoformat(),
        "months": [f"{y}-{m:02d}" for y, m in months],
        "columns": ["TGL", "HARI", "JAM"] + [
            r for r in ["P'BICARA", "PUJIAN", "PEMUSIK", "PENCATAT", "ABSENSI", "TEAM AV",
                        "PENGUMUMAN", "PENYAMBUT TAMU", "KONSUMSI", "PETUGAS SNACK"]
            if any(r in t for t in template.values())
        ],
        "jam": {s: c.most_common(1)[0][0] for s, c in jam.items()},
        "template": {s: template[s] for s in list(SERVICES) + list(SIDE) if s in template},
        "retired": sorted(retired),
        # Written back with the sheet's own spelling, gelar and all.
        "display": {w: (spelling[w] or c).most_common(1)[0][0]
                    for w, c in recent_spelling.items() if w not in retired},
        "pools": {s: {r: dict(c.most_common()) for r, c in roles.items()}
                  for s, roles in pools.items()},
        "pairs": sorted(p for p in duo if not set(p) & retired),
        "multislot": multislot,
        "rate": {w: round(n / len(months), 2) for w, n in rate.most_common()},
        "cap": {w: max(1, round(n / len(months) * 1.5)) for w, n in rate.items()},
        "last_served": {w: d.isoformat() for w, d in sorted(last_served.items())
                        if w not in retired},
    }


def weekdays(year, month, weekday):
    days = calendar.monthrange(year, month)[1]
    first = date(year, month, 1)
    start = 1 + (weekday - first.weekday()) % 7
    return [date(year, month, d) for d in range(start, days + 1, 7)]


def draft(rules, year, month):
    """Assign every slot for the month. Returns [(slot, date, {role: [names]})]."""
    days = {slot: [d for d in weekdays(year, month, {"Jumat": 4, "PBK": 2}.get(slot, 5))]
            for slot in rules["template"]}
    # Each person's fair share of a role: their share of last months' turns,
    # scaled to the number of services this month.
    quota = {}
    for slot, roles in rules["template"].items():
        for role, need in roles.items():
            pool = rules["pools"].get(slot, {}).get(role, {})
            total = sum(pool.values()) or 1
            for who, weight in pool.items():
                quota[(slot, role, who)] = weight / total * len(days[slot]) * need

    filled, counts = Counter(), Counter()
    last = {w: date.fromisoformat(d) for w, d in rules["last_served"].items()}
    cap, multislot = rules["cap"], set(rules["multislot"])
    partner = {}
    for a, b in rules["pairs"]:
        partner.setdefault(a, b)
        partner.setdefault(b, a)

    schedule, booked = [], defaultdict(set)   # date -> people already serving
    for when in sorted({d for ds in days.values() for d in ds}):
        for slot in list(SERVICES) + list(SIDE):
            if when not in days.get(slot, ()):
                continue
            taken, picks = set(), {}
            for role, need in rules["template"][slot].items():
                pool = rules["pools"].get(slot, {}).get(role, {})
                chosen = []
                while len(chosen) < need:
                    # Serving two services in one day is only for those who
                    # already do it; inside one service nobody doubles up.
                    blocked = taken | (booked[when] - multislot)
                    who = best(pool, blocked, when, quota, filled, counts,
                               last, cap, slot, role)
                    if not who:
                        break
                    chosen.append(who)
                    taken.add(who)
                    mate = partner.get(who)
                    if len(chosen) < need and mate in pool and mate not in taken:
                        chosen.append(mate)
                        taken.add(mate)
                for who in chosen:
                    filled[(slot, role, who)] += 1
                    counts[who] += 1
                    last[who] = when
                    booked[when].add(who)
                picks[role] = chosen
            schedule.append((slot, when, picks))
    return schedule


def best(pool, blocked, when, quota, filled, counts, last, cap, slot, role):
    """Whoever is furthest behind their share of this role."""
    ranked = []
    for who, weight in pool.items():
        if who in blocked or counts[who] >= cap.get(who, 1):
            continue
        short = quota[(slot, role, who)] - filled[(slot, role, who)]
        gap = (when - last[who]).days if who in last else 999
        ranked.append((round(short, 3), gap, weight, who))
    if not ranked:
        return None
    return max(ranked)[3]


def show(rules, who):
    return rules.get("display", {}).get(who, who)


def role_of(header):
    role = header.upper().replace("\u2019", "'").strip()
    return "PENYAMBUT TAMU" if role.startswith("PENYAMBUT") else role


def spread(names, columns):
    """Names into however many columns the sheet gives that role."""
    if len(names) <= len(columns):
        return dict(zip(columns, names))
    keep = len(columns) - 1
    return dict(zip(columns, names[:keep] + [" & ".join(names[keep:])]))


def to_grid(rules, schedule, year, month):
    """The draft in the newest tab's own layout -- paste straight into Sheets."""
    lay = rules["layout"]
    grid, width = [], 1

    def put(cells):
        nonlocal width
        width = max(width, max(cells, default=1))
        grid.append(cells)

    put({1: re.sub(r"BULAN\s+[A-Z]+\s*\d{4}", f"BULAN {MONTHS[month].upper()} {year}",
                   lay["title"], flags=re.I)})
    put({col: head for head, col in lay["header"]})
    put({})
    columns = defaultdict(list)
    for head, col in lay["header"]:
        columns[role_of(head)].append(col)
    for slot, when, picks in schedule:
        if slot in SIDE:
            continue
        row = {columns["TGL"][0]: when.isoformat(),
               columns["HARI"][0]: slot,
               columns["JAM"][0]: rules["jam"].get(slot, "")}
        for role, names in picks.items():
            row.update(spread([show(rules, n) for n in names], columns.get(role, [])))
        put(row)
    put({})

    side = [b for b in lay["side"] if b["label"] in rules["template"]]
    labels, whens, heads = {}, {}, {}
    for block in side:
        first = block["cols"][0][1]
        labels[first] = block["label"]
        whens[first] = block["when"]
        heads.update({col: head for head, col in block["cols"]})
    put(labels), put(whens), put(heads)
    for i in range(max((len([1 for s, _w, _p in schedule if s == b["label"]])
                        for b in side), default=0)):
        row = {}
        for block in side:
            rows = [(w, p) for s, w, p in schedule if s == block["label"]]
            if i >= len(rows):
                continue
            when, picks = rows[i]
            for head, col in block["cols"]:
                row[col] = when.isoformat() if head.upper() == "TGL" else \
                    " & ".join(show(rules, n) for n in picks.get(role_of(head), []))
        put(row)

    return [[row.get(c, "") for c in range(1, width + 1)] for row in grid]


def to_tsv(grid):
    return "\n".join("\t".join(row).rstrip("\t") for row in grid)


def to_xlsx(grid, path, title):
    """Write a one-sheet workbook. An .xlsx is a zip of XML and every cell here
    is an inline string, so this needs no library -- same trade as the reader."""
    def ref(row, col):
        name = ""
        while col:
            col, rest = divmod(col - 1, 26)
            name = chr(65 + rest) + name
        return f"{name}{row}"

    def esc(text):
        return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    rows = "".join(
        f'<row r="{r}">' + "".join(
            f'<c r="{ref(r, c)}" t="inlineStr"><is><t xml:space="preserve">{esc(v)}</t></is></c>'
            for c, v in enumerate(cells, 1) if v
        ) + "</row>"
        for r, cells in enumerate(grid, 1)
    )
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    parts = {
        "[Content_Types].xml":
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml"'
            ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels":
            f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'relationships"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        "xl/workbook.xml":
            f'<?xml version="1.0"?><workbook xmlns="{ns}" xmlns:r="{rel}"><sheets>'
            f'<sheet name="{esc(title)[:31]}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'relationships"><Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>',
        "xl/worksheets/sheet1.xml":
            f'<?xml version="1.0"?><worksheet xmlns="{ns}"><sheetData>{rows}</sheetData></worksheet>',
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in parts.items():
            z.writestr(name, body)


def selftest(rules):
    year, month = 2026, 10
    schedule = draft(rules, year, month)
    assert schedule, "empty schedule"
    seen = Counter()
    for slot, when, picks in schedule:
        flat = [w for names in picks.values() for w in names]
        assert len(flat) == len(set(flat)), f"{when} {slot}: someone booked twice"
        for role, need in rules["template"][slot].items():
            assert len(picks[role]) == need, f"{when} {slot} {role}: {picks[role]} != {need}"
            for who in picks[role]:
                assert who in rules["pools"][slot][role], f"{who} not in {slot}/{role} pool"
        seen.update(flat)
    for a, b in rules["pairs"]:
        days = {(w, s) for s, w, p in schedule for n in p.values() if a in n}
        mates = {(w, s) for s, w, p in schedule for n in p.values() if a in n and b in n}
        assert not days or mates, f"{a} scheduled without {b}"
    hog = [w for w, n in seen.items() if n > max(4, rules["rate"].get(w, 0) * 2)]
    assert not hog, f"over-booked: {hog}"
    grid = to_grid(rules, schedule, year, month)
    assert grid[0][0].endswith("OKTOBER 2026"), grid[0][0]
    assert [h for h, _c in rules["layout"]["header"]] == \
        [c for c in grid[1] if c], "header row does not match the sheet"
    # The workbook we write has to be readable by the parser we already ship.
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tmp:
        to_xlsx(grid, tmp.name, "Test")
        wb = Workbook(io.BytesIO(open(tmp.name, "rb").read()))
        back = wb.grid(wb.sheets[0][1])
    assert [r.get(1, "") for r in back] == [r[0] for r in grid], "xlsx round-trip lost a row"
    print(f"ok: {len(schedule)} tugas, {len(seen)} petugas, "
          f"terbanyak {seen.most_common(1)[0]}, xlsx bisa dibaca balik")


def load_rules(path="rules.json"):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv):
    if argv[:1] == ["derive"]:
        ALIAS.update(aliases())
        if argv[1:]:
            blob = io.BytesIO(open(argv[1], "rb").read())
        else:
            with urllib.request.urlopen(EXPORT_URL, timeout=120) as resp:
                blob = io.BytesIO(resp.read())
        json.dump(derive(blob), sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
    elif argv[:1] in (["draft"], ["xlsx"]) and len(argv) > 1:
        year, month = (int(x) for x in argv[1].split("-"))
        rules = load_rules(argv[2] if len(argv) > 2 else "rules.json")
        grid = to_grid(rules, draft(rules, year, month), year, month)
        if argv[0] == "draft":
            print(to_tsv(grid))
        else:
            out = argv[3] if len(argv) > 3 else f"jadwal-{year}-{month:02d}.xlsx"
            to_xlsx(grid, out, f"{MONTHS[month]} {str(year)[2:]}")
            print(out)
    elif argv[:1] == ["--selftest"]:
        selftest(load_rules(argv[1] if len(argv) > 1 else "rules.json"))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])

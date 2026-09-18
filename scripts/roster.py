#!/usr/bin/env python3
"""Derive the rostering rules from the spreadsheet, then draft a new month.

The sheet is the source of truth; rules.json is just its cached summary, so a
changed pool or a retired volunteer flows through on the next derive. Edit
rules.json by hand to override anything (pools, rates, pairs, headcounts).

Usage:
    python3 scripts/roster.py derive [sheet.xlsx] > rules.json
    python3 scripts/roster.py draft 2026-10 [rules.json]   # TSV, paste into Sheets
    python3 scripts/roster.py --selftest
"""

import calendar
import io
import json
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import date, timedelta

from fetch_parse import EXPORT_URL, Workbook, find_blocks, parse_date, read_block, sheet_month

# Rules are read off the recent past only -- 2025's pools are stale.
WINDOW = 9          # months of history feeding the pools
TEMPLATE_WINDOW = 4 # months deciding which roles exist and how many fill them
ACTIVE = 3          # months of silence before we treat someone as retired
SERVICES = ("Jumat", "Sabtu Pagi", "Sabtu Siang")
SIDE = ("PBK", "PAMS", "PEMUDA")
SKIP = ("TGL", "HARI", "JAM", "ACARA")

# One person, many spellings. Titles (Pdt/Dkn/Dks/Sdr) are stripped first, so
# only the genuinely different spellings need a line here.
ALIAS = {
    "otniel": "Othniel", "hans": "Hans A", "ivan": "Ivan S", "ivan simadi": "Ivan S",
    "eric": "Eric K", "erick k": "Eric K", "melia": "Meila", "cindy": "Cindy W",
    "cindiana": "Cindiana W", "linda tj": "Linda Tg", "johon": "Johon L",
    "lina y": "Lina Yong", "mey khim": "Mei Khim", "mei kim": "Mei Khim",
    "yin yin": "Yin Yin", "yenny": "Yenny S", "yenny suryawan": "Yenny S",
    "yenny surjawan": "Yenny S", "kefas": "Kefas J", "fanuel fang": "Fanuel",
    "ng tjioe yung": "Ng Tjioe Yong", "steven x": "Steven Xie", "matthew": "Matthew Honggo",
    "chandra": "Ronny Chandra", "ronny": "Ronny Chandra", "rony chandra": "Ronny Chandra",
    "kwet kam": "Kwet Kam", "stevanie l": "Stevani L", "daniel": "Daniel (Dkn)",
    "dk daniel": "Daniel (Dkn)", "jonathan": "Jonathan H", "fenny c": "Fenny",
    "kevin g": "Kevin", "hana o": "Hana O", "lilik k": "Lilik", "james a": "James",
}
# Placeholders that sit in a person column but name an event or the congregation.
NOT_A_PERSON = re.compile(
    r"^(KKR|KPI|PAMS|PEMUDA|RYF|Gereja|Fam Day|Sie\.?\s*Acara|Sabtu|Rabu|Jumat|\d)", re.I
)
TITLE = re.compile(r"^(Pdt|Pr|Dkn|Dks|Dk|Sdri|Sdr)\.?\s+", re.I)


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
            yield who


def history(blob):
    """Every assignment in the sheet as (date, slot, role, person, jam)."""
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
                    for who in split_cell(cells.get(col, "")):
                        out.append((when, slot, role, who, jam))
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
    for when, slot, role, _who, _jam in late:
        per_cell[(slot, role)][when] += 1
        services[slot].add(when)
    template, order = defaultdict(dict), {}
    for (slot, role), seen in per_cell.items():
        if len(seen) < 0.5 * len(services[slot]):
            continue
        template[slot][role] = Counter(seen.values()).most_common(1)[0][0]

    pools, rate, last_served, jam = defaultdict(lambda: defaultdict(Counter)), Counter(), {}, {}
    for when, slot, role, who, when_jam in recent:
        pools[slot][role][who] += 1
        rate[who] += 1
        last_served[who] = max(last_served.get(who, when), when)
        if when_jam and slot in SERVICES:
            jam.setdefault(slot, Counter())[when_jam] += 1

    # Someone unseen for a quarter has moved, is ill or has handed the job over
    # (Lydia -> Fenny on absensi, June 2026). Keep them out of next month.
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
    for when, slot, role, who, _jam in recent:
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
    for when, slot, _role, who, _jam in recent:
        if slot in SERVICES:
            day_slots[(who, when)].add(slot)
    multislot = sorted({who for (who, _d), slots in day_slots.items() if len(slots) > 1})
    return {
        "derived": date.today().isoformat(),
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


def to_tsv(rules, schedule):
    cols = rules["columns"]
    lines = []
    for slot, when, picks in schedule:
        if slot in SIDE:
            continue
        row = [str(when.day), slot, rules["jam"].get(slot, "")]
        row += [" & ".join(picks.get(c, [])) for c in cols[3:]]
        lines.append("\t".join(row))
    for slot in SIDE:
        rows = [(w, p) for s, w, p in schedule if s == slot]
        if not rows:
            continue
        roles = list(rules["template"][slot])
        lines += ["", slot, "\t".join(["TGL"] + roles)]
        lines += ["\t".join([str(w.day)] + [" & ".join(p.get(r, [])) for r in roles])
                  for w, p in rows]
    return "\n".join(lines)


def selftest(rules):
    schedule = draft(rules, 2026, 10)
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
    print(f"ok: {len(schedule)} tugas, {len(seen)} petugas, "
          f"terbanyak {seen.most_common(1)[0]}")


def load_rules(path="rules.json"):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv):
    if argv[:1] == ["derive"]:
        if argv[1:]:
            blob = io.BytesIO(open(argv[1], "rb").read())
        else:
            with urllib.request.urlopen(EXPORT_URL, timeout=120) as resp:
                blob = io.BytesIO(resp.read())
        json.dump(derive(blob), sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
    elif argv[:1] == ["draft"] and len(argv) > 1:
        year, month = (int(x) for x in argv[1].split("-"))
        rules = load_rules(argv[2] if len(argv) > 2 else "rules.json")
        print(to_tsv(rules, draft(rules, year, month)))
    elif argv[:1] == ["--selftest"]:
        selftest(load_rules(argv[1] if len(argv) > 1 else "rules.json"))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])

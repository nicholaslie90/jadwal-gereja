#!/usr/bin/env python3
"""Guard rails on the parsed roster. CI runs this before publishing, so a
spreadsheet layout change breaks the build instead of silently emptying the page.

Usage: python3 scripts/test_parse.py plain.json
"""

import json
import re
import sys
from datetime import date

BAD_ME = re.compile(r"\bnic(?:h)?olas\s+xie?\b", re.I)   # a different person
BAD_WIFE = re.compile(r"\bcindiana?\b", re.I)            # Eric's wife


def main(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    mine, months = data["mine"], data["months"]
    floor = date.today().replace(day=1)

    assert mine, "no duties found for Nicholas or Cindy -- did the sheet layout change?"
    assert months, "no current-or-future month sheets parsed"

    for d in mine:
        when = date.fromisoformat(d["date"])          # raises on a bad date
        assert when >= floor, f"{d['date']} predates the current month"
        assert d["who"] in ("Nicholas", "Cindy"), f"unexpected person {d['who']!r}"
        assert d["role"], f"duty on {d['date']} has no role"
        assert not BAD_ME.search(d["raw"]), f"matched Nicholas Xie in {d['raw']!r}"
        if d["who"] == "Cindy":
            assert not BAD_WIFE.search(d["raw"]), f"matched Cindiana in {d['raw']!r}"
        if d["start"]:
            assert re.fullmatch(r"\d\d:\d\d", d["start"]), f"bad start {d['start']!r}"
            assert d["end"], f"start without end on {d['date']}"

    assert any(
        b["name"] == "Ibadah" and len(b["rows"]) >= 4
        for m in months for b in m["blocks"]
    ), "no month has a service table with at least 4 rows"

    for m in months:
        for b in m["blocks"]:
            for r in b["rows"]:
                date.fromisoformat(r["date"])
                assert len(r["cells"]) == len(b["headers"]) - 1, \
                    f"{m['label']}/{b['name']}: row width does not match headers"

    print(f"ok: {len(mine)} duties across {len(months)} months "
          f"({', '.join(m['label'] for m in months)})")


if __name__ == "__main__":
    main(sys.argv[1])

#!/usr/bin/env python3
"""Push an ntfy.sh reminder for duties that are 3 days and 1 day away.

The H-3 / H-1 rule is derived purely from today's date, so no state file is
needed to avoid duplicates -- as long as the workflow runs once a day.

Usage:
    NTFY_TOPIC=xxx python3 scripts/notify.py plain.json
    NTFY_TOPIC=xxx python3 scripts/notify.py plain.json --test   # send next duty now
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from itertools import groupby

WIB = timezone(timedelta(hours=7))
NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh/")
PAGE_URL = "https://nicholaslie90.github.io/jadwal-gereja/"
CHURCH = "Gereja Yesus Sejati (GYS) Tanjung Duren"
MAPS = "https://maps.app.goo.gl/YJafmvMSeuE1cbzCA"
LEAD_DAYS = (3, 1)

DAYS = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
MONTHS = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
          "Agustus", "September", "Oktober", "November", "Desember"]

ROLE_LABEL = {
    "P'BICARA": "Pembicara", "P’BICARA": "Pembicara", "TEAM AV": "Team AV",
    "PENYAMBUT TAMU (LT.1)": "Penyambut Tamu (Lt. 1)",
}

# Added to the Google Calendar guest list for whoever is on duty.
GUESTS = {"Nicholas": "nicholaslie90@gmail.com", "Cindy": "cindy.wijaya15@gmail.com"}


def role(name):
    return ROLE_LABEL.get(name.upper(), name.title() if name.isupper() else name)


def pretty(day):
    return f"{DAYS[day.weekday()]}, {day.day} {MONTHS[day.month - 1]}"


def calendar_url(items):
    from urllib.parse import urlencode

    first = items[0]
    day = date.fromisoformat(first["date"])
    roles = ", ".join(dict.fromkeys(f"{role(i['role'])} ({i['who']})" for i in items))
    if first.get("start") and first.get("end"):
        def stamp(hhmm):
            hh, mm = (int(x) for x in hhmm.split(":"))
            utc = datetime(day.year, day.month, day.day, hh, mm, tzinfo=WIB).astimezone(timezone.utc)
            return utc.strftime("%Y%m%dT%H%M%SZ")

        dates = f"{stamp(first['start'])}/{stamp(first['end'])}"
    else:
        dates = f"{day:%Y%m%d}/{day + timedelta(days=1):%Y%m%d}"
    params = {
        "action": "TEMPLATE",
        "text": f"Pelayanan GYS · {roles}",
        "location": CHURCH,
        "details": f"{first['block']}\n{roles}\n\n{MAPS}",
        "dates": dates,
    }
    guests = list(dict.fromkeys(
        GUESTS[i["who"]] for i in items if i["who"] in GUESTS
    ))
    if guests:
        params["add"] = ",".join(guests)
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


def send(topic, items, lead):
    day = date.fromisoformat(items[0]["date"])
    when = "hari ini" if lead == 0 else "besok" if lead == 1 else f"{lead} hari lagi"
    header = " · ".join(x for x in (items[0].get("day"), items[0].get("time")) if x)

    # ntfy's JSON endpoint rather than its header API: HTTP headers are latin-1,
    # and these titles carry an em dash and a middot.
    payload = {
        "topic": topic,
        "title": f"Tugas {pretty(day)} — {when}",
        "message": "\n".join([header] + [f"{i['who']} — {role(i['role'])}" for i in items]),
        "priority": 4 if lead <= 1 else 3,
        "tags": ["church", "calendar"],
        "click": PAGE_URL,
        "actions": [{"action": "view", "label": "Google Calendar", "url": calendar_url(items)}],
    }
    req = urllib.request.Request(
        NTFY_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()
    print(f"notified {day} (H-{lead}): {len(items)} duty/duties")


def main():
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("NTFY_TOPIC not set, skipping notifications")
        return

    with open(sys.argv[1], encoding="utf-8") as fh:
        duties = json.load(fh)["mine"]
    today = datetime.now(WIB).date()
    test = "--test" in sys.argv

    # groupby needs sorted input, otherwise a repeated date silently drops entries.
    by_date = {d: list(g) for d, g in groupby(sorted(duties, key=lambda x: x["date"]),
                                              key=lambda x: x["date"])}
    if test:
        upcoming = sorted(d for d in by_date if date.fromisoformat(d) >= today)
        if not upcoming:
            print("no upcoming duty to test with")
            return
        send(topic, by_date[upcoming[0]], (date.fromisoformat(upcoming[0]) - today).days)
        return

    sent = 0
    for iso, items in sorted(by_date.items()):
        lead = (date.fromisoformat(iso) - today).days
        if lead in LEAD_DAYS:
            send(topic, items, lead)
            sent += 1
    if not sent:
        print("nothing due at H-" + " / H-".join(map(str, LEAD_DAYS)))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as err:
        # A missed reminder must not fail the build that publishes the page.
        print(f"ntfy unreachable: {err}", file=sys.stderr)

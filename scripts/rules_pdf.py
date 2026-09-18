#!/usr/bin/env python3
"""rules.json -> a printable PDF of the rostering rules.

The only script here that needs something installed (weasyprint); the build
pipeline does not use it. On macOS weasyprint needs Homebrew's pango:

    DYLD_FALLBACK_LIBRARY_PATH=$(brew --prefix)/lib \
        python3 scripts/rules_pdf.py rules.json aturan-petugas-2026.pdf
"""
import html
import json
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict

R = json.load(open(sys.argv[1] if len(sys.argv) > 1 else 'rules.json'))
SERV = ["Jumat", "Sabtu Pagi", "Sabtu Siang"]
NAME = {"Jumat": "Sabat Malam (Jumat 19.00)", "Sabtu Pagi": "Sabat Pagi (Sabtu 09.30)",
        "Sabtu Siang": "Sabat Sore (Sabtu 14.00)"}
SHORT = {"Jumat": "Malam", "Sabtu Pagi": "Pagi", "Sabtu Siang": "Sore"}

# per person: counts per slot and per role
slot_n, role_n, total = defaultdict(Counter), defaultdict(Counter), Counter()
for slot, roles in R["pools"].items():
    for role, people in roles.items():
        for who, n in people.items():
            slot_n[who][slot] += n; role_n[who][role] += n; total[who] += n

def cls(who):
    ib = {s: n for s, n in slot_n[who].items() if s in SERV}
    if not ib: return "khusus " + "/".join(sorted(slot_n[who]))
    top, c = max(ib.items(), key=lambda kv: kv[1]); t = sum(ib.values())
    if len(ib) == 1: return f"<b>hanya {NAME[top].split(' (')[0]}</b>"
    if c / t >= 0.8: return f"dominan {NAME[top].split(' (')[0]} ({c}/{t})"
    return "fleksibel · " + " ".join(f"{SHORT[s]}&nbsp;{n}" for s, n in sorted(ib.items(), key=lambda kv: -kv[1]))

def roles_of(who):
    return ", ".join(r.title().replace("Team Av", "Team AV")
                     for r, n in role_n[who].most_common(3) if n >= max(2, .15 * total[who]))

e = html.escape
rows_people = "".join(
    f"<tr><td class=n>{e(who)}</td><td>{total[who]}</td><td>{roles_of(who)}</td>"
    f"<td>{cls(who)}</td><td>{R['rate'].get(who,0)}</td></tr>"
    for who in sorted(total, key=lambda w: -total[w]))

def pool_rows():
    out = []
    roles = ["P'BICARA", "PUJIAN", "PEMUSIK", "PENCATAT", "ABSENSI", "TEAM AV",
             "PENGUMUMAN", "PENYAMBUT TAMU", "KONSUMSI", "PETUGAS SNACK"]
    for role in roles:
        cells = []
        for slot in SERV:
            people = R["pools"].get(slot, {}).get(role, {})
            cells.append(", ".join(f"{e(w)}<span class=w>{n}</span>" for w, n in people.items()) or "<i>—</i>")
        out.append(f"<tr><td class=n>{role.title().replace('Team Av','Team AV')}</td>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return "".join(out)

def side_rows():
    out = []
    for blok in ("PBK", "PAMS", "PEMUDA"):
        for role, people in R["pools"].get(blok, {}).items():
            out.append(f"<tr><td class=n>{blok}</td><td>{role.title()}</td><td>"
                       + ", ".join(f"{e(w)}<span class=w>{n}</span>" for w, n in people.items()) + "</td></tr>")
    return "".join(out)

ROLES = ["P'BICARA","PUJIAN","PEMUSIK","PENCATAT","ABSENSI","TEAM AV","PENGUMUMAN",
         "PENYAMBUT TAMU","KONSUMSI","PETUGAS SNACK"]
tmpl_rows = "".join(
    f"<tr><td class=n>{r.title().replace("Team Av","Team AV")}</td>"
    + "".join(f"<td class=c>{R['template'][s].get(r,'—')}</td>" for s in SERV)
    + f"<td>{len(set().union(*(R['pools'].get(s,{}).get(r,{}) for s in SERV)))} orang</td></tr>"
    for r in ROLES)

pairs = " · ".join(f"<b>{e(a)}</b> + <b>{e(b)}</b>" for a, b in R["pairs"])
multi = ", ".join(e(w) for w in R["multislot"])
retired = ", ".join(e(w) for w in R["retired"])

HTML = f"""<!doctype html><meta charset=utf-8><title>Aturan Petugas Pelayanan 2026</title>
<style>
@page {{ size: A4; margin: 15mm 14mm 16mm; @bottom-right {{ content: counter(page) " / " counter(pages);
  font: 8pt/1 'Helvetica Neue', sans-serif; color: #999; }} }}
body {{ font: 9pt/1.45 'Helvetica Neue', Helvetica, sans-serif; color: #1a1a1a; }}
h1 {{ font-size: 19pt; margin: 0 0 2mm; letter-spacing: -.3pt; }}
h2 {{ font-size: 11pt; margin: 7mm 0 2mm; padding-bottom: 1mm; border-bottom: 1.2pt solid #1a1a1a;
     text-transform: uppercase; letter-spacing: .6pt; }}
h2:first-of-type {{ margin-top: 5mm; }}
.sub {{ color: #666; font-size: 8.5pt; margin-bottom: 4mm; }}
table {{ width: 100%; border-collapse: collapse; font-size: 8pt; }}
th {{ text-align: left; background: #f2f2f0; font-weight: 600; font-size: 7.5pt;
     text-transform: uppercase; letter-spacing: .4pt; }}
th, td {{ border-bottom: .4pt solid #ddd; padding: 1.5mm 1.8mm; vertical-align: top; }}
td.n {{ font-weight: 600; white-space: nowrap; }}
td.c {{ text-align: center; }} .f {{ font-weight: 400; color: #888; text-transform: none; letter-spacing: 0; }}
table.tmpl td, table.tmpl th {{ width: 19%; }} table.tmpl td.n, table.tmpl th:first-child {{ width: 24%; }}
.w {{ color: #aaa; font-size: 6.5pt; vertical-align: super; margin-left: .4mm; }}
.note {{ background: #fbf7ec; border-left: 2.5pt solid #d9a441; padding: 2.5mm 3mm; margin: 2mm 0; font-size: 8.5pt; }}
ul {{ margin: 2mm 0; padding-left: 4.5mm; }} li {{ margin-bottom: 1.2mm; }}
code {{ font: 8pt 'SF Mono', Menlo, monospace; background: #f2f2f0; padding: .3mm 1mm; }}
tr, li {{ break-inside: avoid; }}
</style>
<h1>Aturan Petugas Pelayanan</h1>
<div class=sub>GYS Tanjung Duren · diturunkan dari jadwal {R['months'][0]} s/d {R['months'][-1]}
({len(R['months'])} bulan, {sum(total.values())} penugasan) · dibuat {R['derived']}</div>

<h2>1. Struktur ibadah &amp; jumlah petugas per sel</h2>
<table class=tmpl><tr><th>Peran</th><th>Sabat Malam<br><span class=f>Jumat 19.00–20.30</span></th>
<th>Sabat Pagi<br><span class=f>Sabtu 09.30–11.00</span></th>
<th>Sabat Sore<br><span class=f>Sabtu 14.00–15.30</span></th><th>Ukuran pool</th></tr>{tmpl_rows}</table>
<p class=sub>Angka = berapa orang mengisi sel itu tiap ibadah.</p>
<div class=note><b>Berubah sejak Juni 2026:</b> kolom <code>ACARA</code> dihapus, <code>P'BICARA</code> pindah
ke depan <code>PUJIAN</code>, <code>TEAM AV</code> jadi satu kolom (boleh dua nama), dan
<b>Konsumsi + Snack hanya di Sabat Malam</b>. Sabat Pagi &amp; Sore tidak lagi punya kedua kolom itu.</div>
<p>Ibadah tambahan: <b>PBK</b> Rabu 20.00–21.15 (Pujian + Pembicara) · <b>PAMS</b> Sabtu 15.45–16.45 (Pembicara)
· <b>PEMUDA</b> Sabtu 15.45–16.45 (Pembicara) — baru ada sejak Agustus 2026.</p>

<h2>2. Siapa boleh di peran &amp; ibadah mana</h2>
<table><tr><th>Peran</th><th>Sabat Malam (Jumat)</th><th>Sabat Pagi</th><th>Sabat Sore</th></tr>{pool_rows()}</table>
<p class=sub>Angka kecil = berapa kali orang itu mengisi peran tersebut selama 9 bulan; itu juga porsinya saat jadwal baru disusun.</p>
<table><tr><th>Ibadah</th><th>Peran</th><th>Petugas</th></tr>{side_rows()}</table>

<h2>3. Aturan pasangan &amp; rangkap tugas</h2>
<ul>
<li><b>Selalu berpasangan</b> (kalau satu dijadwalkan, satunya ikut): {pairs}.</li>
<li><b>Boleh dua ibadah dalam satu hari</b> — hanya {len(R['multislot'])} orang ini: {multi}.
Selain mereka, satu orang satu ibadah per hari.</li>
<li><b>Dua peran dalam satu ibadah</b> boleh untuk kombinasi ringan saja (Absensi + Penyambut Tamu,
Pengumuman + Penyambut Tamu). Generator sengaja tidak memakai ini.</li>
<li><b>Batas beban:</b> tiap orang dibatasi 1,5&times; rata-rata tugas bulanannya sendiri.</li>
</ul>

<h2>4. Sudah tidak aktif (tidak dijadwalkan lagi)</h2>
<p>Tidak muncul 3 bulan terakhir: {retired}.</p>
<div class=note>Perhatikan peran yang berpindah tangan: pemegang Absensi lama berhenti Juni 2026 dan
digantikan orang lain, jadi pool Absensi di atas sudah yang terbaru, bukan yang Januari–Mei.</div>

<h2>5. Daftar lengkap petugas aktif</h2>
<table><tr><th>Nama</th><th>Tugas</th><th>Peran utama</th><th>Batasan ibadah</th><th>Rata2/bln</th></tr>{rows_people}</table>

<h2>6. Catatan data</h2>
<ul>
<li><b>Tab JAN26 salah tahun:</b> nilai tanggalnya serial 2025 (2–31 Januari 2025) padahal isinya jadwal
Januari 2026. Nama hari sudah benar. Sebaiknya diperbaiki di sheet.</li>
<li>Ejaan disatukan lewat <code>aliases.json</code>: gelar (Pdt/Dkn/Dks/Sdr) dibuang, tanda hubung dan
singkatan marga disamakan. Dua nama depan yang sama dengan inisial marga berbeda tetap dianggap
orang yang berbeda; satu nama depan yang kadang ditulis tanpa marga masih ambigu dan
diperlakukan sebagai orang tersendiri.</li>
<li>Penulis acara, bukan orang: <code>KKR</code>, <code>KPI</code>, <code>Gereja</code>, <code>RYF</code> — diabaikan.</li>
</ul>

<h2>7. Cara memakai</h2>
<p><code>python3 scripts/roster.py derive &gt; rules.json</code> — baca ulang aturan dari spreadsheet.<br>
<code>python3 scripts/roster.py draft 2026-10 rules.json</code> — draft bulan baru, format sama persis dengan
tab terakhir, langsung tempel ke Sheets.<br>
<code>python3 scripts/roster.py xlsx 2026-10 rules.json jadwal.xlsx</code> — draft yang sama sebagai file Excel.<br>
<code>python3 scripts/roster.py --selftest rules.json</code> — cek hasil tidak melanggar aturan di atas.</p>
<p class=sub>rules.json boleh diedit tangan untuk menimpa apa pun: pool, pasangan, jumlah orang per sel, batas beban.</p>
"""
out = sys.argv[2] if len(sys.argv) > 2 else 'aturan-petugas-2026.pdf'
with tempfile.NamedTemporaryFile('w', suffix='.html', encoding='utf-8') as page:
    page.write(HTML)
    page.flush()
    subprocess.run(['weasyprint', page.name, out], check=True)
print('->', out)

# Jadwal Pelayanan GYS — Encrypted GitHub Pages + ntfy

**Tanggal:** 2026-07-27
**Status:** Disetujui

## Tujuan

Melacak kapan Nicholas dan Cindy bertugas di Gereja Yesus Sejati Tanjung Duren,
dari Google Sheets yang dibagikan pihak gereja. Dua kanal:

1. **GitHub Pages** — halaman terenkripsi, dibuka dengan password, menampilkan
   tugas kami + jadwal lengkap bulan berjalan, dengan tombol "Add to Google Calendar".
2. **ntfy.sh** — notifikasi push H-3 dan H-1 sebelum bertugas.

## Sumber data

Google Sheets (public, bisa di-export tanpa auth):

```
https://docs.google.com/spreadsheets/d/1dM5Upi_XoIe9VOo3gSVmXP2igBmXCZ4p/export?format=xlsx
```

Karakteristik yang menentukan desain parser:

| Fakta | Konsekuensi |
| --- | --- |
| 71 tab, satu per bulan sejak Jan 2021 | Filter ke bulan >= bulan berjalan |
| Nama tab inkonsisten (`MEI26`, `JUNI 26`, `Sept`) | Bulan/tahun dibaca dari cell **A1**, bukan nama tab |
| A1 selalu `...BULAN <BULAN> <TAHUN>` | Sumber otoritatif bulan/tahun |
| Urutan kolom berubah antar bulan (Jan'26 punya `ACARA`, Jul'26 tidak) | Header dibaca dinamis dari row yang memuat `TGL` |
| Ada blok tambahan `PBK` dan `PAMS` di bawah blok utama, side-by-side | Setiap cell `TGL` = awal satu blok |
| Tanggal kadang serial Excel (`46228.0`), kadang string (`1 MEI`) | Dua-duanya di-handle, output ISO |
| Jam format `09.30 - 11.00` / `20.00-21.15` | Regex toleran spasi & separator |

## Identifikasi nama

Ada beberapa orang dengan nama serupa di sheet. Aturan match yang sudah
diverifikasi terhadap seluruh 71 tab:

**Nicholas (saya):** `Nicholas`, `NIcholas`, `Nicolas`, `Nicholas L`, `Nicholas Lie`

**BUKAN saya:** `Nicholas X`, `Nicholas Xie` — orang lain. Bukti: dua varian ini
selalu muncul di kolom `PUJIAN` (Ags23 s/d Des25) dan tidak pernah tumpang tindih
dengan `Nicholas` polos. Parser **membuang** substring ini sebelum mencocokkan.

**Cindy (istri):** `Cindy`, `Cindy W`

**BUKAN istri:** `Cindi`, `Cindiana`, `Cindiana W` — istri Eric (`Eric K & Cindiana`).
Regex `\bcindy\b` secara natural tidak match `Cindiana`/`Cindi` (tidak ada huruf `y`).

## Arsitektur

Zero dependency. Python stdlib (`zipfile` + `ElementTree`) cukup untuk membaca
`.xlsx`; Node 20 di runner sudah punya `crypto.webcrypto` untuk AES-GCM. Tidak ada
`pip install`, tidak ada `npm install`.

```
scripts/fetch_parse.py    download .xlsx -> parse -> JSON plaintext (stdout)
scripts/test_parse.py     assert-based self-check, jalan sebelum publish
scripts/encrypt.mjs       PBKDF2 + AES-256-GCM -> docs/data.json
scripts/notify.py         kirim ntfy untuk tugas H-3 / H-1
docs/index.html           single file: HTML + CSS + JS inline
docs/logo.png             logo GYS
docs/data.json            payload terenkripsi (di-commit oleh CI)
.github/workflows/build.yml
```

GitHub Pages disajikan dari `main` branch folder `/docs` — tanpa branch `gh-pages`,
tanpa action deploy terpisah.

## Alur data

```
cron 22:00 UTC (05:00 WIB)
  |
  v
fetch_parse.py --> /tmp/plain.json
  |
  +--> test_parse.py   (gagal => workflow stop, data.json lama tetap tayang)
  |
  +--> encrypt.mjs --> docs/data.json --> commit+push --> Pages
  |
  +--> notify.py --> ntfy.sh
```

## Format payload (plaintext, sebelum dienkripsi)

```json
{
  "generated": "2026-07-27T05:00:00+07:00",
  "mine": [
    { "date": "2026-08-01", "day": "Sabtu Pagi", "time": "09.30 - 11.00",
      "start": "09:30", "end": "11:00",
      "who": "Nicholas", "role": "PENYAMBUT TAMU",
      "raw": "Nicholas", "block": "Ibadah", "month": "Agustus 2026" }
  ],
  "months": [
    { "label": "Juli 2026",
      "blocks": [
        { "name": "Ibadah", "headers": ["TGL","HARI","JAM","P'BICARA", "..."],
          "rows": [ { "date": "2026-07-03", "cells": ["Jumat","19.00 - 20.30","..."] } ] }
      ] }
  ]
}
```

`mine` diurutkan berdasarkan tanggal, satu entry per (orang, peran) — jadi kalau
Nicholas dan Cindy bertugas di ibadah yang sama, ada dua entry.

## Enkripsi

| Parameter | Nilai |
| --- | --- |
| KDF | PBKDF2-HMAC-SHA256, 250.000 iterasi |
| Salt | 16 byte random, baru setiap build |
| Cipher | AES-256-GCM |
| IV | 12 byte random, baru setiap build |
| Output | `{ v, kdf, iter, salt, iv, ct, hash, generated }` base64 |

Password **tidak pernah masuk repo**. Disimpan sebagai GitHub Actions secret
`PAGE_PASSWORD`.

### Batas keamanan yang diketahui

Password yang dipakai = 6 karakter huruf kecil. PBKDF2 250k iterasi memperlambat
brute-force offline, tapi tidak menghentikannya — ruang pencarian terlalu kecil.
Sheet sumbernya sendiri sudah public. Jadi enkripsi ini **obfuscation-grade**:
cukup untuk mencegah nama-nama jemaat ter-index mesin pencari dan mencegah orang
lewat membaca isinya, bukan untuk menahan penyerang yang niat. Upgrade path:
ganti `PAGE_PASSWORD` ke passphrase 4 kata — tanpa perubahan kode.

### Commit noise

Salt dan IV random tiap build berarti ciphertext selalu berubah walau jadwalnya
sama, yang akan menghasilkan commit tiap hari. Karena itu `encrypt.mjs` menyimpan
`hash` = SHA-256 dari plaintext (tanpa field `generated`) di dalam `data.json`, dan
**tidak menulis ulang file** kalau hash-nya sama. Efek samping yang diinginkan:
`generated` jadi berarti "kapan jadwal terakhir berubah", bukan "kapan cron
terakhir jalan" — itu informasi yang lebih berguna, dan ditampilkan sebagai
"Data per: ...".

## UI

Brand color diambil dari logo: teal `#00A2B1`, deep blue `#00529B`.

**Lock screen.** Kartu putih di tengah, logo GYS di atas, satu field password.
Setelah benar, password disimpan di `localStorage` sehingga kunjungan berikutnya
langsung ter-decrypt tanpa prompt. Kalau decrypt gagal (mis. password diganti),
`localStorage` dibersihkan dan prompt muncul lagi. Tombol kunci untuk lock manual.

**Tampilan jadwal.**

```
+------------------------------+
|  logo Gereja Yesus Sejati    |
|  Jadwal Pelayanan            |
+------------------------------+
| TUGAS BERIKUTNYA             |  kartu gradient teal -> blue
|   Sabtu, 1 Agustus - 3 hari  |
|   09.30-11.00                |
|   NICHOLAS - Penyambut Tamu  |
|   CINDY    - Penyambut Tamu  |
|   [ + Google Calendar ]      |
+------------------------------+
| [ Tugas Kami ] [ Jadwal Lengkap ]
|  Agustus 2026                |
|  Sab 1  Nicholas - P.Tamu [+]|
|  Sab 1  Cindy    - P.Tamu [+]|
+------------------------------+
```

Tab **Jadwal Lengkap**: tabel per bulan, scroll horizontal di dalam containernya
sendiri (body tidak pernah scroll horizontal), baris tempat kami bertugas
di-highlight teal. Mobile-first.

Semua isi sheet dirender via `textContent`, tidak pernah `innerHTML` — menutup
jalur XSS dari isi spreadsheet, yang tidak kami kendalikan.

## Google Calendar

Link template `calendar.google.com/calendar/render?action=TEMPLATE`, jam
dikonversi dari WIB (UTC+7) ke UTC:

```
&dates=20260801T023000Z/20260801T040000Z
&text=Pelayanan GYS - Penyambut Tamu (Nicholas)
&location=Gereja Yesus Sejati (GYS) Tanjung Duren
&details=<link Google Maps + koordinat>
```

Kalau jam tidak bisa diparse, jatuh ke all-day event (`dates=20260801/20260802`).

Lokasi: Gereja Yesus Sejati (GYS) Tanjung Duren, `-6.175243, 106.7791305`,
https://maps.app.goo.gl/YJafmvMSeuE1cbzCA

## Notifikasi ntfy

Topic disimpan sebagai secret `NTFY_TOPIC` (topic URL ntfy = capability URL,
siapa pun yang tahu bisa subscribe dan publish).

**Aturan kirim:** untuk setiap tanggal tugas, kirim tepat sekali saat jarak ke
hari-H = 3 hari, dan sekali lagi saat = 1 hari. Deterministik dari tanggal, jadi
tidak butuh state file. Semua tugas di tanggal yang sama digabung jadi satu
notifikasi.

```
Title:    Tugas 1 Agustus - 3 hari lagi
Priority: default (H-3) / high (H-1)
Tags:     church, calendar
Click:    https://nicholaslie90.github.io/jadwal-gereja/
Actions:  view, Google Calendar, <link template>
Body:     Sabtu Pagi - 09.30 - 11.00
          Nicholas - PENYAMBUT TAMU
          Cindy - PENYAMBUT TAMU
```

`workflow_dispatch` punya input `test_notify` untuk mengirim notifikasi percobaan
tanpa menunggu H-3.

## Error handling

- Download gagal, atau A1 tidak bisa diparse di semua sheet, atau `mine` kosong
  => `test_parse.py` gagal => workflow berhenti sebelum menulis `data.json`.
  Halaman tetap menyajikan payload terakhir yang valid.
- Cell tanggal tidak bisa diparse => row di-skip, tidak membatalkan sheet.
- Jam tidak bisa diparse => event jadi all-day, bukan error.
- `NTFY_TOPIC` tidak di-set => `notify.py` keluar dengan pesan, exit 0.

## Test

`scripts/test_parse.py` jalan di CI sebelum encrypt, assert invariant nyata:

1. `mine` tidak kosong
2. semua `date` adalah ISO valid dan >= tanggal 1 bulan berjalan
3. tidak ada entry yang `raw`-nya mengandung `Nicholas X` / `Nicholas Xie`
4. tidak ada entry yang `raw`-nya mengandung `Cindiana`
5. minimal satu bulan punya blok `Ibadah` dengan >= 4 row
6. setiap `who` adalah `Nicholas` atau `Cindy`

Plus roundtrip encrypt -> decrypt di `encrypt.mjs --selftest`.

## Langkah manual

1. Settings -> Secrets and variables -> Actions:
   - `PAGE_PASSWORD` = password halaman
   - `NTFY_TOPIC` = topic ntfy

   Keduanya diisi lewat UI GitHub, tidak pernah ditulis di repo ini -- repo-nya
   public.
2. Settings -> Pages -> Source: `main`, folder `/docs`
3. Install app ntfy di HP, subscribe ke topic di atas

## Yang sengaja tidak dibuat

- **Dark mode** — logo GYS di background putih, brand-nya terang. Tambah kalau
  ternyata silau dipakai di gereja.
- **Filter/search di jadwal lengkap** — cuma 2 bulan data, scroll saja cukup.
- **State file untuk dedup notifikasi** — aturan H-3/H-1 sudah deterministik dari
  tanggal. Tambah kalau cron pernah jalan dua kali sehari.
- **Guard untuk `Eric & Cindy`** — satu-satunya kemunculan di Jun 2023, sudah di
  luar window bulan berjalan. Tambah kalau muncul lagi.

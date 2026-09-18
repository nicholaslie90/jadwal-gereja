# Jadwal Pelayanan GYS Tanjung Duren

Melacak kapan **Nicholas** dan **Cindy** bertugas, dibaca otomatis dari Google
Sheets jadwal pelayanan gereja.

- **Halaman terenkripsi:** https://nicholaslie90.github.io/jadwal-gereja/
- **Notifikasi:** ntfy.sh, H-3 dan H-1 sebelum bertugas

## Cara kerja

```
cron 12:00 WIB
  │
  ├─ fetch_parse.py  Google Sheets (.xlsx) → plain.json
  ├─ test_parse.py   gagal ⇒ berhenti, data.json lama tetap tayang
  ├─ encrypt.mjs     AES-256-GCM → docs/data.json → commit → Pages
  └─ notify.py       ntfy.sh untuk tugas H-3 / H-1
```

Nol dependency. `.xlsx` itu zip berisi XML, jadi `zipfile` + `ElementTree` dari
Python stdlib sudah cukup; AES-GCM pakai `crypto.webcrypto` yang sudah ada di
Node 20. Tidak ada `pip install`, tidak ada `npm install`.

## Setup

1. **Settings → Secrets and variables → Actions → New repository secret**

   | Nama | Isi |
   | --- | --- |
   | `PAGE_PASSWORD` | password untuk membuka halaman |
   | `NTFY_TOPIC` | `jadwal-gys-nic-cin-…` (topic ntfy) |

2. **Settings → Pages** → Source: `Deploy from a branch`, branch `main`, folder `/docs`

3. Install app [ntfy](https://ntfy.sh/app) di HP, subscribe ke topic yang sama.

4. **Actions → Build jadwal → Run workflow** untuk build pertama. Centang
   *Kirim notifikasi percobaan* kalau mau sekalian menguji ntfy.

## Menyusun jadwal bulan berikutnya

`scripts/roster.py` membaca aturan langsung dari spreadsheet (siapa boleh di
peran dan ibadah mana, pasangan tetap, seberapa sering tiap orang bertugas)
lalu menyusun draft bulan baru.

`rules.json` dan `aliases.json` menyebut nama jemaat, jadi yang masuk git cuma
versi terenkripsinya (`*.enc.json`, password sama dengan halaman).

```sh
node scripts/encrypt.mjs --decrypt aliases.enc.json aliases.json  # PAGE_PASSWORD
node scripts/encrypt.mjs --decrypt rules.enc.json   rules.json
python3 scripts/roster.py derive > rules.json      # atau: derive sheet.xlsx
node scripts/encrypt.mjs rules.json rules.enc.json # setelah derive ulang
python3 scripts/roster.py --selftest rules.json
python3 scripts/roster.py draft 2026-10 rules.json # TSV, tempel ke Sheets
python3 scripts/roster.py xlsx  2026-10 rules.json jadwal-oktober-2026.xlsx
```

Draft mengikuti layout tab terakhir (judul, kolom kembar Penyambut Tamu &
Petugas Snack, tabel PBK/PAMS/PEMUDA di samping), jadi tinggal tempel.

Ringkasan aturannya bisa dicetak jadi PDF -- satu-satunya skrip di sini yang
butuh install (`weasyprint`, dan di macOS `brew install pango`):

```sh
DYLD_FALLBACK_LIBRARY_PATH=$(brew --prefix)/lib \
  python3 scripts/rules_pdf.py rules.json aturan-petugas-2026.pdf
```

`rules.json` cuma ringkasan sheet, boleh diedit tangan untuk menimpa apa pun
(pool, kuota, pasangan, jumlah orang per sel). Yang tidak muncul 3 bulan
terakhir dianggap pensiun dan tidak dijadwalkan lagi.

## Jalan lokal

```sh
python3 scripts/fetch_parse.py > plain.json      # atau: … sheet.xlsx (file lokal)
python3 scripts/test_parse.py plain.json
node    scripts/encrypt.mjs --selftest
PAGE_PASSWORD=xxx node scripts/encrypt.mjs plain.json docs/data.json
NTFY_TOPIC=xxx python3 scripts/notify.py plain.json --test

python3 -m http.server -d docs 8000            # buka http://localhost:8000
```

## Catatan tentang enkripsi

Payload dienkripsi AES-256-GCM, kunci dari PBKDF2-HMAC-SHA256 250.000 iterasi,
salt + IV baru setiap build. Password tidak pernah masuk repo — hanya ada sebagai
GitHub Actions secret.

**Batasnya:** password pendek (6 karakter) tetap bisa di-brute-force offline oleh
orang yang niat, karena ciphertext-nya publik. Dan spreadsheet sumbernya sendiri
memang sudah bisa diakses siapa saja yang punya link. Jadi ini setingkat
*obfuscation*: cukup untuk mencegah halaman ter-index mesin pencari dan mencegah
orang lewat membaca nama-nama jemaat, bukan untuk menahan penyerang serius.
Mau lebih kuat? Ganti `PAGE_PASSWORD` jadi passphrase 4 kata — tanpa ubah kode.

Setelah password benar sekali, tersimpan di `localStorage` browser itu, jadi
kunjungan berikutnya langsung terbuka. Tombol 🔒 di kanan atas untuk mengunci lagi.

## Google Calendar guest list

Tombol *Tambahkan ke Google Calendar* (di halaman maupun di notifikasi ntfy)
otomatis mengisi guest list sesuai siapa yang bertugas:

| Bertugas | Guest yang ditambahkan |
| --- | --- |
| Nicholas | nicholaslie90@gmail.com |
| Cindy | cindy.wijaya15@gmail.com |

Kalau keduanya bertugas di ibadah yang sama, dua-duanya diundang. Alamat Anda
sendiri jadi no-op bagi Google, jadi efeknya: siapa pun yang membuat event,
yang satunya tetap dapat undangan.

## Pemetaan nama

Sudah diverifikasi terhadap seluruh 71 tab spreadsheet:

| | Cocok | Tidak cocok |
| --- | --- | --- |
| Nicholas | `Nicholas`, `NIcholas`, `Nicolas`, `Nicholas L`, `Nicholas Lie` | `Nicholas X`, `Nicholas Xie` (orang lain, selalu di kolom PUJIAN) |
| Cindy | `Cindy`, `Cindy W` | `Cindi`, `Cindiana`, `Cindiana W` (istri Eric) |

Kalau gereja mengubah cara menulis nama, `test_parse.py` akan gagal dan halaman
tidak akan ter-update dengan data yang salah.

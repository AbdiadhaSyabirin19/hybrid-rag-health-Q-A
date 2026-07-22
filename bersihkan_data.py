import pandas as pd
import re

def bersihkan_teks(teks: str) -> str:
    teks = re.sub(r'<[^>]+>', ' ', teks)          # hapus tag HTML
    teks = re.sub(r'\s+', ' ', teks).strip()      # normalisasi whitespace
    return teks

csv_path = 'Abdiadha Syabirin_berita-kesehatan.csv'

print("Membaca file data mentah...")
with open(csv_path, 'r', encoding='utf-8') as f:
    data = f.read().replace('\x00', '')

print("Memproses baris data (regex split)...")
raw_rows = re.split(r';{10,}\s*', data)

def parse_raw_row(raw_row):
    raw_row = raw_row.strip()
    if not raw_row:
        return None
    if raw_row.startswith('Judul,Isi Berita'):
        return None
    if raw_row.startswith(',,,'):
        parts = raw_row[3:].split(',')
        url = parts[0] if len(parts) > 0 else ''
        label = parts[1] if len(parts) > 1 else ''
        return ['', '', '', url, label]

    if raw_row.startswith('"') and raw_row.endswith('"'):
        raw_row = raw_row[1:-1]

    idx_url = raw_row.rfind(',http')
    if idx_url != -1:
        left_side = raw_row[:idx_url]
        right_side = raw_row[idx_url + 1:]
        if ',' in right_side:
            url, label = right_side.rsplit(',', 1)
        else:
            url, label = right_side, ''
            
        idx_tanggal = left_side.rfind('"",')
        if idx_tanggal != -1:
            tanggal = left_side[idx_tanggal + 3:].strip('"')
            judul_dan_isi = left_side[:idx_tanggal]
            idx_isi = judul_dan_isi.find(',""')
            if idx_isi != -1:
                judul = judul_dan_isi[:idx_isi].strip('"')
                isi = judul_dan_isi[idx_isi + 3:].strip('"')
                return [judul, isi, tanggal, url, label]
            else:
                return [judul_dan_isi.strip('"'), '', '', url, label]
        else:
            return [left_side.strip('"'), '', '', url, label]
    return None

parsed_dataset = []
current_row = None

print("Rekonstruksi data dan penggabungan fragmen...")
for r in raw_rows:
    res = parse_raw_row(r)
    if res:
        if current_row:
            parsed_dataset.append(current_row)
        current_row = res
    else:
        clean_frag = r.strip().strip('"').strip(';').strip()
        if clean_frag and current_row:
            if current_row[1]:
                current_row[1] += " " + clean_frag
            else:
                current_row[1] = clean_frag

if current_row:
    parsed_dataset.append(current_row)

# Konversi ke DataFrame pandas
df = pd.DataFrame(parsed_dataset, columns=['judul', 'konten', 'tanggal', 'url', 'label'])

print("Membersihkan konten teks...")
df['konten'] = df['konten'].fillna('').apply(bersihkan_teks)
df['judul'] = df['judul'].fillna('').apply(bersihkan_teks)

# Tulis ke file hasil
df.to_csv('artikel_kesehatan.csv', index=False)
print(f'Selesai! Total artikel bersih: {len(df)}')



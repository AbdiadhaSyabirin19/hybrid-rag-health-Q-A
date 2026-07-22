import pandas as pd
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Load dataset hasil pembersihan
df = pd.read_csv('artikel_kesehatan.csv')

# Ambil teks dari artikel pertama sebagai contoh
artikel_teks = df['konten'].iloc[0]
judul_artikel = df['judul'].iloc[0]

print(f"Judul Artikel Contoh: {judul_artikel}")
print(f"Panjang artikel asli: {len(artikel_teks)} karakter\n")

splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=80,
    separators=["\n\n", "\n", ". ", " ", ""],
)
chunks = splitter.split_text(artikel_teks)

print(f'Artikel terpecah menjadi {len(chunks)} chunk')
for idx, chunk in enumerate(chunks[:3]):
    print(f"\n--- Chunk {idx+1} ---")
    print(chunk)

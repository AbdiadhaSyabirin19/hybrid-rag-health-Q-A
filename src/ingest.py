import os
import json
import sys
import signal
import pandas as pd
from tqdm import tqdm
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_ollama import OllamaEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams

from src import config

# Variabel global untuk mendeteksi permintaan berhenti
stop_requested = False

def graceful_exit_handler(signum, frame):
    global stop_requested
    if not stop_requested:
        print("\n[!] Menangkap sinyal berhenti (Ctrl+C). Menyelesaikan batch aktif saat ini sebelum keluar secara aman...")
        stop_requested = True
    else:
        print("\n[!] Memaksa keluar segera...")
        sys.exit(1)


def muat_dokumen(csv_path: str) -> list[Document]:
    """Memuat CSV artikel dan mengubahnya menjadi list Document LangChain."""
    df = pd.read_csv(csv_path)
    df = df.dropna(subset=["konten"])  # pastikan konten tidak kosong
    dokumen = []
    for _, row in df.iterrows():
        dokumen.append(Document(
            page_content=str(row["konten"]),
            metadata={
                "judul": str(row["judul"]) if pd.notna(row["judul"]) else "",
                "tanggal": str(row["tanggal"]) if pd.notna(row["tanggal"]) else "",
                "url": str(row["url"]) if pd.notna(row["url"]) else "",
                "label": str(row["label"]) if pd.notna(row["label"]) else "",
            },
        ))
    return dokumen


def pecah_menjadi_chunk(dokumen: list[Document]) -> list[Document]:
    """Memecah setiap dokumen artikel menjadi beberapa chunk lebih kecil."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return splitter.split_documents(dokumen)


def main():
    global stop_requested
    
    # Registrasi handler untuk menangkap Ctrl+C secara anggun
    signal.signal(signal.SIGINT, graceful_exit_handler)

    checkpoint_path = "data/checkpoint.json"

    # Cek opsi reset
    if "--reset" in sys.argv:
        if os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)
        print("Checkpoint direset. Ingestion akan dimulai dari awal.")

    print("[1/4] Memuat dokumen dari CSV...")
    dokumen = muat_dokumen("data/artikel_kesehatan.csv")
    print(f"      Total artikel: {len(dokumen)}")

    print("[2/4] Memecah artikel menjadi chunk...")
    chunks = pecah_menjadi_chunk(dokumen)
    total_chunks = len(chunks)
    print(f"      Total chunk: {total_chunks}")

    print("[3/4] Menyiapkan model embedding lokal (Ollama)...")
    embeddings = OllamaEmbeddings(
        model=config.EMBEDDING_MODEL,
        base_url=config.OLLAMA_BASE_URL,
    )

    # Deteksi dimensi embedding secara dinamis
    print("      Menguji dimensi model embedding...")
    try:
        test_vector = embeddings.embed_query("test")
        vector_size = len(test_vector)
        print(f"      Dimensi model embedding terdeteksi: {vector_size}")
    except Exception as e:
        print(f"Error: Gagal terhubung ke Ollama. Pastikan Ollama berjalan di {config.OLLAMA_BASE_URL}.")
        print(f"Detail error: {e}")
        sys.exit(1)

    print("[4/4] Menginisialisasi koneksi Qdrant...")
    client = QdrantClient(url=f"http://{config.QDRANT_HOST}:{config.QDRANT_PORT}")

    # Buat koleksi jika belum ada
    if not client.collection_exists(config.COLLECTION_NAME):
        print(f"      Membuat koleksi baru '{config.COLLECTION_NAME}' di Qdrant...")
        client.create_collection(
            collection_name=config.COLLECTION_NAME,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
    else:
        # Jika --reset dipanggil, kita hapus dan buat ulang koleksinya
        if "--reset" in sys.argv:
            print(f"      Menghapus dan membuat ulang koleksi '{config.COLLECTION_NAME}'...")
            client.delete_collection(config.COLLECTION_NAME)
            client.create_collection(
                collection_name=config.COLLECTION_NAME,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )
        else:
            print(f"      Koleksi '{config.COLLECTION_NAME}' sudah ada. Melanjutkan...")

    # Deteksi progress secara dinamis dengan menyelaraskan status Qdrant dan Checkpoint File
    start_idx = 0
    if "--reset" not in sys.argv:
        print("      Menyelaraskan status database dan file checkpoint...")
        
        # 1. Ambil jumlah point aktual di Qdrant
        qdrant_count = 0
        try:
            qdrant_count = client.count(collection_name=config.COLLECTION_NAME).count
        except Exception as e:
            print(f"      [!] Gagal mendeteksi jumlah data di Qdrant: {e}")
            
        # 2. Ambil index terakhir dari checkpoint.json
        checkpoint_index = None
        if os.path.exists(checkpoint_path):
            try:
                with open(checkpoint_path, "r") as f:
                    checkpoint_data = json.load(f)
                    if checkpoint_data.get("embedding_model") == config.EMBEDDING_MODEL:
                        checkpoint_index = checkpoint_data.get("last_index_in_qdrant")
            except Exception as e:
                pass
                
        # 3. Bandingkan dan cetak keterangan ke terminal
        if checkpoint_index is not None:
            print(f"      -> Index di checkpoint.json : {checkpoint_index}")
            print(f"      -> Point aktual di Qdrant     : {qdrant_count}")
            if checkpoint_index == qdrant_count:
                print(f"      -> Status: SINKRON. Melanjutkan pengiriman dari indeks: {qdrant_count}")
            else:
                print(f"      -> Status: TIDAK SINKRON (interupsi sebelumnya).")
                print(f"         Menggunakan acuan aman database Qdrant ({qdrant_count}) untuk melanjutkan.")
        else:
            print(f"      -> Index di checkpoint.json : Tidak ditemukan / Baru")
            print(f"      -> Point aktual di Qdrant     : {qdrant_count}")
            print(f"      -> Status: Melanjutkan pengiriman dari indeks database Qdrant: {qdrant_count}")
            
        start_idx = qdrant_count
        
        if start_idx >= total_chunks:
            print(f"      Proses ingestion sudah selesai ({start_idx}/{total_chunks} chunk). Gunakan '--reset' jika ingin mengulang.")
            sys.exit(0)

    # Inisialisasi QdrantVectorStore LangChain
    vector_store = QdrantVectorStore(
        client=client,
        collection_name=config.COLLECTION_NAME,
        embedding=embeddings,
    )

    # Ukuran batch diperkecil agar progress update lebih sering/real-time
    batch_size = 100
    print(f"      Mengirim chunk ke Qdrant per batch ({batch_size} chunk)...")
    
    # Progress bar dengan tqdm
    progress_bar = tqdm(
        initial=start_idx,
        total=total_chunks,
        desc="Progress Ingest"
    )

    try:
        for i in range(start_idx, total_chunks, batch_size):
            if stop_requested:
                break
                
            batch = chunks[i:i + batch_size]
            vector_store.add_documents(batch)
            
            # Dapatkan jumlah point nyata terbaru dari Qdrant
            current_qdrant_count = client.count(collection_name=config.COLLECTION_NAME).count
            
            # Selaraskan progress bar dengan database Qdrant
            progress_bar.n = current_qdrant_count
            progress_bar.refresh()
            
            # Tulis checkpoint agar user bisa memeriksa filenya
            with open(checkpoint_path, "w") as f:
                json.dump({
                    "embedding_model": config.EMBEDDING_MODEL,
                    "last_index_in_qdrant": current_qdrant_count,
                    "total_chunks": total_chunks
                }, f)
                
        # Dapatkan status akhir setelah keluar loop
        final_count = client.count(collection_name=config.COLLECTION_NAME).count
        if stop_requested:
            print(f"\n[i] Proses dihentikan dengan aman. Total data tersimpan di Qdrant saat ini: {final_count}.")
        else:
            print(f"\nSelesai! Seluruh basis pengetahuan ({final_count} chunk) berhasil disimpan di Qdrant.")
    except Exception as e:
        print(f"\nTerjadi kesalahan selama ingestion: {e}")
        print("Proses dihentikan. Anda dapat menjalankannya kembali untuk melanjutkan.")
    finally:
        progress_bar.close()


if __name__ == "__main__":
    main()

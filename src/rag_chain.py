"""RAG chain untuk tanya-jawab kesehatan berbasis artikel.

Modul ini menyediakan pipeline retrieval-augmented generation (RAG)
yang mengambil konteks dari Qdrant vector database dan menghasilkan
jawaban terstruktur menggunakan LLM lokal via Ollama.
"""

import functools
import logging
from collections import defaultdict

from langchain_core.documents import Document
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_qdrant import QdrantVectorStore

from src import config
from src.schema import HasilRAG, JawabanTerkait

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inisialisasi komponen (lazy & cached)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _buat_embeddings() -> OllamaEmbeddings:
    """Membuat instance embedding Ollama (di-cache, hanya dibuat sekali)."""
    logger.info("Menginisialisasi model embedding: %s", config.EMBEDDING_MODEL)
    return OllamaEmbeddings(
        model=config.EMBEDDING_MODEL,
        base_url=config.OLLAMA_BASE_URL,
    )


@functools.lru_cache(maxsize=1)
def _buat_vector_store() -> QdrantVectorStore:
    """Membuat koneksi ke Qdrant vector store (di-cache, hanya dibuat sekali)."""
    logger.info("Menghubungkan ke Qdrant: %s:%s", config.QDRANT_HOST, config.QDRANT_PORT)
    return QdrantVectorStore.from_existing_collection(
        embedding=_buat_embeddings(),
        url=f"http://{config.QDRANT_HOST}:{config.QDRANT_PORT}",
        collection_name=config.COLLECTION_NAME,
    )


def _dapatkan_retriever():
    """Mengembalikan retriever dari vector store."""
    return _buat_vector_store().as_retriever(
        search_type="similarity",
        search_kwargs={"k": config.TOP_K_RETRIEVAL},
    )


@functools.lru_cache(maxsize=2)
def _dapatkan_llm_terstruktur(gunakan_thinking: bool):
    """Mengembalikan instance LLM dengan structured output (di-cache per mode thinking).

    Menggunakan ``lru_cache`` agar instance LLM hanya dibuat sekali per
    kombinasi parameter, menghindari overhead koneksi berulang.
    """
    logger.info(
        "Membuat instance LLM terstruktur (thinking=%s)",
        gunakan_thinking,
    )
    llm = ChatOllama(
        model=config.LLM_MODEL,
        base_url=config.OLLAMA_BASE_URL,
        temperature=0.2,
        extra_body={"think": gunakan_thinking},
    )
    return llm.with_structured_output(HasilRAG)


# ---------------------------------------------------------------------------
# Utilitas format & metadata
# ---------------------------------------------------------------------------


def _dapatkan_metadata_artikel(
    meta: dict,
) -> tuple[str, str, str, str, str]:
    """Mengekstrak metadata artikel secara aman dengan penanganan format/fallback.

    Returns:
        Tuple berisi (artikel_id, judul, sumber, url, tanggal_publikasi).
    """
    judul = meta.get("judul", "Tanpa Judul")
    url = meta.get("url", "")

    # Memisahkan penulis/sumber dan tanggal dari field 'tanggal' (format CSV)
    tanggal_raw = meta.get("tanggal", "")
    if "," in tanggal_raw:
        sumber, tanggal_publikasi = [x.strip() for x in tanggal_raw.split(",", 1)]
    else:
        sumber = meta.get("sumber", "KlikDokter")
        tanggal_publikasi = meta.get("tanggal_publikasi", tanggal_raw)

    artikel_id = meta.get("artikel_id", url if url else judul)
    return artikel_id, judul, sumber, url, tanggal_publikasi


def kelompokkan_per_artikel(
    dokumen_list: list[Document],
) -> list[dict]:
    """Mengelompokkan chunk retrieval berdasarkan artikel asalnya.

    Returns:
        List dict berisi metadata artikel dan gabungan teks chunk-nya,
        diurutkan berdasarkan tanggal publikasi terbaru lebih dulu.
    """
    kelompok: dict[str, list[Document]] = defaultdict(list)
    for doc in dokumen_list:
        artikel_id, *_ = _dapatkan_metadata_artikel(doc.metadata)
        kelompok[artikel_id].append(doc)

    artikel_list: list[dict] = []
    for artikel_id, chunks in kelompok.items():
        meta = chunks[0].metadata
        gabungan_teks = "\n".join(c.page_content for c in chunks)
        _, judul, sumber, url, tanggal_publikasi = _dapatkan_metadata_artikel(meta)

        artikel_list.append({
            "artikel_id": artikel_id,
            "judul": judul,
            "sumber": sumber,
            "url": url,
            "tanggal_publikasi": tanggal_publikasi,
            "teks": gabungan_teks,
        })

    # Urutkan artikel terbaru lebih dulu secara aman
    try:
        artikel_list.sort(key=lambda a: a["tanggal_publikasi"], reverse=True)
    except Exception:
        pass
    return artikel_list


def format_artikel_untuk_prompt(artikel_list: list[dict]) -> str:
    """Memformat list artikel menjadi teks bernomor untuk prompt LLM."""
    bagian: list[str] = []
    for i, a in enumerate(artikel_list, 1):
        bagian.append(
            f"[Artikel {i}] ID: {a['artikel_id']} | {a['judul']} "
            f"({a['sumber']}, {a['tanggal_publikasi']})\n{a['teks']}"
        )
    return "\n\n".join(bagian)


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

TEMPLATE_JAWABAN_TERKAIT = """\
Berdasarkan beberapa artikel kesehatan di bawah, \
susun jawaban dengan format berikut:
1. jawaban_utama: satu jawaban terpadu yang menjawab pertanyaan pengguna, \
menggabungkan informasi dari semua artikel yang relevan.
2. jawaban_terkait: untuk SETIAP artikel di bawah, tuliskan ringkasan 1-2 kalimat \
yang menjawab pertanyaan dari sudut pandang artikel tersebut secara spesifik.
3. perlu_rujukan_medis: true jika pertanyaan mengindikasikan gejala berat/darurat.

ATURAN PENTING:
- Jawab HANYA berdasarkan konteks artikel yang diberikan. \
Jangan menambahkan informasi dari pengetahuan lain.
- Jika konteks tidak cukup, katakan dengan jujur bahwa informasi tidak tersedia.
- Jangan memberikan diagnosis pasti maupun menjanjikan kesembuhan.
- Untuk gejala berat/darurat, sarankan menghubungi layanan gawat darurat.
- Gunakan bahasa Indonesia yang jelas dan mudah dipahami orang awam.

Pertanyaan pengguna: {question}

Artikel-artikel:
{artikel_terformat}
"""

# ---------------------------------------------------------------------------
# Fungsi utama
# ---------------------------------------------------------------------------


def tanya(pertanyaan: str) -> HasilRAG:
    """Menjawab pertanyaan kesehatan menggunakan pipeline RAG.

    Alur:
    1. Retrieve chunk relevan dari Qdrant.
    2. Kelompokkan chunk berdasarkan artikel asal.
    3. Kirim ke LLM untuk menghasilkan jawaban terstruktur.
    4. Lengkapi metadata (url, sumber) dari data asli.

    Args:
        pertanyaan: Pertanyaan kesehatan dari pengguna.

    Returns:
        Objek ``HasilRAG`` berisi jawaban utama, jawaban per-artikel,
        dan flag ``perlu_rujukan_medis``.
    """
    retriever = _dapatkan_retriever()
    dokumen_relevan = retriever.invoke(pertanyaan)
    artikel_list = kelompokkan_per_artikel(dokumen_relevan)
    artikel_terformat = format_artikel_untuk_prompt(artikel_list)

    # Nyalakan thinking mode otomatis jika pertanyaan butuh menggabungkan
    # informasi dari lebih dari satu artikel (indikasi kasus multi-hop)
    perlu_thinking = len(artikel_list) > 1
    llm_terstruktur = _dapatkan_llm_terstruktur(gunakan_thinking=perlu_thinking)

    prompt_lengkap = TEMPLATE_JAWABAN_TERKAIT.format(
        question=pertanyaan,
        artikel_terformat=artikel_terformat,
    )
    hasil: HasilRAG = llm_terstruktur.invoke(prompt_lengkap)

    # Lengkapi url & sumber jika LLM tidak menyalinnya dengan sempurna,
    # dengan mencocokkan judul_artikel ke metadata asli (lebih andal)
    peta_artikel = {a["judul"].strip().lower(): a for a in artikel_list}
    for jt in hasil.jawaban_terkait:
        cocok = peta_artikel.get(jt.judul_artikel.strip().lower())
        if cocok:
            jt.url = cocok["url"]
            jt.sumber = cocok["sumber"]
            jt.tanggal_publikasi = cocok["tanggal_publikasi"]

    return hasil


def cari_artikel_terkait_tambahan(
    hasil: HasilRAG,
    artikel_sudah_tampil: set[str],
    k: int = 3,
) -> list[dict]:
    """Mencari artikel tambahan yang topiknya berdekatan dengan jawaban utama.

    Melakukan similarity search kedua menggunakan ``jawaban_utama`` sebagai
    query, lalu memfilter artikel yang sudah ditampilkan.

    Args:
        hasil: Objek HasilRAG dari fungsi ``tanya()``.
        artikel_sudah_tampil: Set judul/ID artikel yang sudah ditampilkan.
        k: Jumlah maksimal artikel tambahan yang dikembalikan.

    Returns:
        List dict berisi judul, url, dan sumber artikel tambahan.
    """
    vs = _buat_vector_store()
    kandidat = vs.similarity_search(hasil.jawaban_utama, k=k + 5)

    artikel_terkait_tambahan: list[dict] = []
    id_sudah_ada: set[str] = set()
    for doc in kandidat:
        aid, judul, sumber, url, _ = _dapatkan_metadata_artikel(doc.metadata)
        if aid in artikel_sudah_tampil or aid in id_sudah_ada:
            continue
        id_sudah_ada.add(aid)
        artikel_terkait_tambahan.append({
            "judul": judul,
            "url": url,
            "sumber": sumber,
        })
        if len(artikel_terkait_tambahan) >= k:
            break

    return artikel_terkait_tambahan


# ---------------------------------------------------------------------------
# Entrypoint untuk testing langsung
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Menjalankan uji coba tanya()...")
    hasil_uji = tanya("Apa gejala awal demam berdarah dengue?")
    print(f"Jawaban: {hasil_uji.jawaban_utama}")
    print(f"Artikel terkait: {len(hasil_uji.jawaban_terkait)}")
    print(f"Perlu rujukan medis: {hasil_uji.perlu_rujukan_medis}")

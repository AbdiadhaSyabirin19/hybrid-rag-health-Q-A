"""Pipeline Hybrid RAG (Dense Qdrant + BM25 Lexical + Recency + RRF + Cross-Encoder Reranker + Structured LLM Output)."""

import functools
import logging
import re
from collections import defaultdict
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_qdrant import QdrantVectorStore

from src import config
from src.hybrid_rag.hybrid_retrival import (
    muat_index_bm25,
    retrieval_hybrid_dengan_recency,
)
from src.hybrid_rag.schema_hybrid import (
    ArtikelSumber,
    HasilLLM,
    HasilRAG,
    JawabanTerkait,
)

logger = logging.getLogger(__name__)

BM25_INDEX_PATH = Path("data/bm25_index.pkl")


@functools.lru_cache(maxsize=1)
def _buat_embeddings() -> OllamaEmbeddings:
    """Membuat instance embedding Ollama (di-cache)."""
    return OllamaEmbeddings(
        model=config.EMBEDDING_MODEL,
        base_url=config.OLLAMA_BASE_URL,
    )


@functools.lru_cache(maxsize=1)
def _buat_vector_store() -> QdrantVectorStore:
    """Membuat koneksi ke Qdrant vector store (di-cache)."""
    return QdrantVectorStore.from_existing_collection(
        embedding=_buat_embeddings(),
        url=f"http://{config.QDRANT_HOST}:{config.QDRANT_PORT}",
        collection_name=config.COLLECTION_NAME,
    )


@functools.lru_cache(maxsize=1)
def _muat_bm25_cached():
    """Memuat indeks BM25 dari berkas disk (di-cache)."""
    return muat_index_bm25(BM25_INDEX_PATH)


@functools.lru_cache(maxsize=2)
def _dapatkan_llm_terstruktur(gunakan_thinking: bool):
    """LLM dengan structured output untuk HasilLLM."""
    llm = ChatOllama(
        model=config.LLM_MODEL,
        base_url=config.OLLAMA_BASE_URL,
        temperature=0.2,
        extra_body={"think": gunakan_thinking},
    )
    return llm.with_structured_output(HasilLLM)


@functools.lru_cache(maxsize=1)
def _dapatkan_llm_ringan():
    """LLM ringan tanpa thinking mode untuk reformulasi pertanyaan."""
    return ChatOllama(
        model=config.LLM_MODEL,
        base_url=config.OLLAMA_BASE_URL,
        temperature=0.0,
        extra_body={"think": False},
    )


def _dapatkan_metadata_artikel(meta: dict) -> tuple[str, str, str, str, str]:
    """Mengekstrak metadata artikel secara aman."""
    judul = meta.get("judul", "Tanpa Judul")
    url = meta.get("url", "")
    tanggal_raw = meta.get("tanggal", "")
    if "," in tanggal_raw:
        sumber, tanggal_publikasi = [x.strip() for x in tanggal_raw.split(",", 1)]
    else:
        sumber = meta.get("sumber", "KlikDokter")
        tanggal_publikasi = meta.get("tanggal_publikasi", tanggal_raw)

    artikel_id = meta.get("artikel_id", url if url else judul)
    return artikel_id, judul, sumber, url, tanggal_publikasi


def kelompokkan_dan_beri_nomor(dokumen_list: list[Document]) -> list[dict]:
    """Mengelompokkan chunk retrieval per artikel dan menambahkan nomor urut sitasi [1], [2], dst.

    Hanya artikel yang memiliki URL yang disertakan — artikel tanpa URL
    dilewati agar tidak menjadi bahan jawaban dan tidak tampil di UI.
    """
    kelompok: dict[str, list[Document]] = defaultdict(list)
    for doc in dokumen_list:
        artikel_id, *_ = _dapatkan_metadata_artikel(doc.metadata)
        kelompok[artikel_id].append(doc)

    artikel_list: list[dict] = []
    nomor = 1
    for artikel_id, chunks in kelompok.items():
        meta = chunks[0].metadata
        gabungan_teks = "\n".join(c.page_content for c in chunks)
        _, judul, sumber, url, tanggal_publikasi = _dapatkan_metadata_artikel(meta)

        if not url:
            logger.debug("Melewati artikel tanpa URL: %s", judul)
            continue

        artikel_list.append({
            "nomor": nomor,
            "artikel_id": artikel_id,
            "judul": judul,
            "sumber": sumber,
            "url": url,
            "tanggal_publikasi": tanggal_publikasi,
            "teks": gabungan_teks,
        })
        nomor += 1
    return artikel_list



def format_artikel_untuk_prompt(artikel_list: list[dict]) -> str:
    """Memformat list artikel menjadi teks bernomor untuk prompt LLM."""
    bagian: list[str] = []
    for a in artikel_list:
        bagian.append(
            f"[{a['nomor']}] Judul: {a['judul']} ({a['sumber']}, {a['tanggal_publikasi']})\n"
            f"Konten:\n{a['teks']}"
        )
    return "\n\n".join(bagian)


def format_riwayat(riwayat: list[dict], max_turns: int = 3) -> str:
    """Memformat n giliran riwayat percakapan terakhir."""
    terpotong = riwayat[-max_turns * 2:]
    baris = []
    for p in terpotong:
        role = "Pengguna" if p.get("role") == "user" else "Asisten"
        baris.append(f"{role}: {p.get('content', '')}")
    return "\n".join(baris)


TEMPLATE_TULIS_ULANG = """Tuliskan ulang PERTANYAAN LANJUTAN pengguna menjadi
pertanyaan yang berdiri sendiri (standalone), dengan mengganti kata ganti atau
referensi implisit (seperti "nya", "itu", "tersebut") menggunakan informasi
dari riwayat percakapan di bawah.

ATURAN:
- JANGAN menjawab pertanyaannya. Hanya tuliskan ulang pertanyaannya saja.
- Jika pertanyaan lanjutan SUDAH berdiri sendiri, kembalikan apa adanya.

Riwayat percakapan sebelumnya:
{riwayat_terformat}

Pertanyaan lanjutan pengguna: {pertanyaan}

Pertanyaan hasil tulis ulang (standalone):"""


def tulis_ulang_pertanyaan_standalone(pertanyaan: str, riwayat: list[dict]) -> str:
    """Mengubah pertanyaan implisit dalam percakapan menjadi pertanyaan standalone."""
    if not riwayat:
        return pertanyaan

    riwayat_terformat = format_riwayat(riwayat)
    prompt = TEMPLATE_TULIS_ULANG.format(
        riwayat_terformat=riwayat_terformat,
        pertanyaan=pertanyaan,
    )
    llm = _dapatkan_llm_ringan()
    try:
        hasil = llm.invoke(prompt)
        pertanyaan_standalone = hasil.content.strip().strip('"')
        return pertanyaan_standalone or pertanyaan
    except Exception as e:
        logger.warning("Gagal me-rewrite pertanyaan standalone: %s", e)
        return pertanyaan


PROMPT_RAG_HYBRID = ChatPromptTemplate.from_messages([

    ("system", (
        "Anda adalah Asisten Informasi Kesehatan AI yang profesional dan berbasis bukti.\n\n"

        "PEDOMAN MENJAWAB:\n"
        "1. Jawab pertanyaan HANYA berdasarkan konteks artikel yang tersedia. Tuliskan jawaban yang padat, akurat, dan mengalir secara alami dalam bahasa Indonesia.\n"
        "2. Berikan sitasi bernomor [1], [2], dst. pada akhir kalimat fakta yang diambil dari artikel terkait.\n"
        "3. Hindari kalimat berulang dan JANGAN menuliskan teks metadata atau field schema (seperti 'perlu_rujukan_medis' atau 'jawaban_utama') ke dalam teks jawaban.\n"
        "4. Jika informasi tidak tersedia dalam konteks artikel, sampaikan dengan jujur bahwa informasi belum tersedia dalam artikel rujukan.\n"
        "5. Jangan memberikan diagnosis mutlak. Jika kondisi darurat, sarankan mencari bantuan medis atau faskes terdekat."
    )),

    ("human", (
        "Artikel yang tersedia:\n"
        "{artikel_terformat}\n\n"
        "Pertanyaan: {question}"
    )),

])


def ekstrak_nomor_sitasi_terpakai(teks_jawaban: str) -> list[int]:
    """Mengambil semua nomor [n] yang benar-benar muncul di teks jawaban."""
    nomor_ditemukan = re.findall(r"\[(\d+)\]", teks_jawaban)
    unik_berurutan = []
    for n in nomor_ditemukan:
        n_int = int(n)
        if n_int not in unik_berurutan:
            unik_berurutan.append(n_int)
    return unik_berurutan


def bangun_sitasi(hasil_llm: HasilLLM, artikel_list: list[dict]) -> list[ArtikelSumber]:
    """Membaca nomor sitasi dari teks jawaban LLM dan mencocokkan ke data asli."""
    nomor_terpakai = ekstrak_nomor_sitasi_terpakai(hasil_llm.jawaban_utama)
    peta_artikel = {a["nomor"]: a for a in artikel_list}

    sitasi_terpakai = []
    for nomor in nomor_terpakai:
        a = peta_artikel.get(nomor)
        if a:
            sitasi_terpakai.append(ArtikelSumber(
                nomor=nomor,
                judul=a["judul"],
                sumber=a["sumber"],
                url=a["url"],
                tanggal_publikasi=a["tanggal_publikasi"],
            ))
    return sitasi_terpakai


def tanya(pertanyaan: str, riwayat: list[dict] | None = None) -> HasilRAG:
    """Fungsi utama pipeline Hybrid RAG."""
    riwayat = riwayat or []

    # 1. Reformulasi pertanyaan jika ada riwayat
    pertanyaan_untuk_retrieval = tulis_ulang_pertanyaan_standalone(pertanyaan, riwayat)

    # 2. Hybrid Retrieval (BM25 + Dense Qdrant + Recency)
    vs = _buat_vector_store()
    bm25, chunks_bm25 = _muat_bm25_cached()
    dokumen_relevan = retrieval_hybrid_dengan_recency(
        pertanyaan_untuk_retrieval,
        vector_store=vs,
        bm25=bm25,
        chunks_bm25=chunks_bm25,
        k_final=config.TOP_K_RETRIEVAL,
    )

    if not dokumen_relevan:
        logger.info("Tidak ditemukan dokumen relevan pada hybrid retrieval.")
        return HasilRAG(
            jawaban_utama="Maaf, tidak ditemukan informasi yang relevan dalam basis data pengetahuan kesehatan kami untuk pertanyaan ini.",
            jawaban_terkait=[],
            sitasi_terpakai=[],
            perlu_rujukan_medis=False,
            pertanyaan_dipahami=(
                pertanyaan_untuk_retrieval
                if pertanyaan_untuk_retrieval.strip() != pertanyaan.strip()
                else None
            ),
        )

    artikel_list = kelompokkan_dan_beri_nomor(dokumen_relevan)
    artikel_terformat = format_artikel_untuk_prompt(artikel_list)

    # 3. Dynamic Thinking Mode & Structured Output
    perlu_thinking = len(artikel_list) > 1
    llm_terstruktur = _dapatkan_llm_terstruktur(gunakan_thinking=perlu_thinking)

    chain = PROMPT_RAG_HYBRID | llm_terstruktur
    hasil_llm: HasilLLM = chain.invoke({
        "question": pertanyaan,
        "artikel_terformat": artikel_terformat,
    })

    # 4. Bangun sitasi & jawaban_terkait
    sitasi_terpakai = bangun_sitasi(hasil_llm, artikel_list)
    jawaban_terkait = [
        JawabanTerkait(
            judul_artikel=a["judul"],
            ringkasan=a["teks"][:150] + "..." if len(a["teks"]) > 150 else a["teks"],
            sumber=a["sumber"],
            url=a["url"],
            tanggal_publikasi=a["tanggal_publikasi"],
        )
        for a in artikel_list
    ]

    return HasilRAG(
        jawaban_utama=hasil_llm.jawaban_utama,
        jawaban_terkait=jawaban_terkait,
        sitasi_terpakai=sitasi_terpakai,
        perlu_rujukan_medis=hasil_llm.perlu_rujukan_medis,
        pertanyaan_dipahami=(
            pertanyaan_untuk_retrieval
            if pertanyaan_untuk_retrieval.strip() != pertanyaan.strip()
            else None
        ),
        konteks_retrieval=[doc.page_content for doc in dokumen_relevan],
    )


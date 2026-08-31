"""Modul retrieval hybrid (BM25 + Dense Vector + Recency Reranking + RRF + Cross-Encoder)."""

import functools
import logging
import math
import pickle
import re
from datetime import datetime
from pathlib import Path

from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder

from src import config

logger = logging.getLogger(__name__)

KATA_KUNCI_TEMPORAL = [
    "terbaru", "terkini", "sekarang", "saat ini", "update", "terupdate",
    "kondisi terakhir", "situasi terakhir", "hari ini", "minggu ini",
    "bulan ini", "kasus terbaru", "perkembangan", "belakangan ini",
]


def tokenisasi_sederhana(teks: str) -> list[str]:
    """Tokenisasi teks sederhana: huruf kecil & hapus simbol non-alphanumerik."""
    teks = teks.lower()
    teks = re.sub(r"[^a-z0-9\s]", " ", teks)
    return teks.split()


def bangun_index_bm25(semua_chunks: list, path_simpan: str | Path):
    """Membangun dan menyimpan indeks BM25 dari daftar chunk dokumen."""
    korpus_token = [tokenisasi_sederhana(c.page_content) for c in semua_chunks]
    bm25 = BM25Okapi(korpus_token)

    path_obj = Path(path_simpan)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    with open(path_obj, "wb") as f:
        pickle.dump({"bm25": bm25, "chunks": semua_chunks}, f)
    return bm25, semua_chunks


def muat_index_bm25(path_simpan: str | Path) -> tuple[BM25Okapi | None, list]:
    """Memuat indeks BM25 dan daftar chunk dari file pickle jika ada."""
    path_obj = Path(path_simpan)
    if not path_obj.exists():
        return None, []
    with open(path_obj, "rb") as f:
        data = pickle.load(f)
        return data.get("bm25"), data.get("chunks", [])


def bm25_search(query: str, bm25: BM25Okapi, chunks: list, k: int) -> list[tuple]:
    """Melakukan pencarian kata kunci menggunakan BM25."""
    if not bm25 or not chunks:
        return []
    token_query = tokenisasi_sederhana(query)
    skor = bm25.get_scores(token_query)
    top_idx = sorted(range(len(skor)), key=lambda i: skor[i], reverse=True)[:k]
    return [(chunks[i], float(skor[i])) for i in top_idx]


def deteksi_intent_temporal(pertanyaan: str) -> bool:
    """Mendeteksi apakah pertanyaan mengandung kata kunci waktu/recency."""
    p = pertanyaan.lower()
    return any(kw in p for kw in KATA_KUNCI_TEMPORAL)


def hitung_skor_recency(tanggal_str: str, half_life_hari: float = 21) -> float:
    """Skor 1.0 untuk artikel hari ini, meluruh separuh setiap `half_life_hari`."""
    if not tanggal_str:
        return 0.5
    try:
        tanggal = datetime.strptime(str(tanggal_str)[:10], "%Y-%m-%d")
        usia_hari = max((datetime.now() - tanggal).days, 0)
        lam = math.log(2) / half_life_hari
        return math.exp(-lam * usia_hari)
    except Exception:
        return 0.5


def reciprocal_rank_fusion(daftar_hasil_per_metode: list[list], bobot: list[float], K: int = 60):
    """Menggabungkan beberapa hasil pencarian terurut menggunakan RRF."""
    skor_gabungan = {}

    for hasil_satu_metode, w in zip(daftar_hasil_per_metode, bobot):
        for rank, item in enumerate(hasil_satu_metode, start=1):
            chunk = item[0] if isinstance(item, tuple) else item
            key = id(chunk)
            skor_gabungan.setdefault(key, {"chunk": chunk, "skor": 0.0})
            skor_gabungan[key]["skor"] += w * (1.0 / (K + rank))

    hasil_terurut = sorted(skor_gabungan.values(), key=lambda x: x["skor"], reverse=True)
    return [(item["chunk"], item["skor"]) for item in hasil_terurut]


@functools.lru_cache(maxsize=1)
def _muat_cross_encoder(model_name: str | None = None) -> CrossEncoder:
    """Memuat model Cross-Encoder Reranker (di-cache & 100% offline)."""
    nama_model = model_name or config.CROSS_ENCODER_MODEL
    logger.info("Memuat model Cross-Encoder: %s", nama_model)
    path_obj = Path(nama_model)
    if path_obj.exists():
        return CrossEncoder(str(path_obj), local_files_only=True)
    try:
        return CrossEncoder(nama_model, local_files_only=True)
    except Exception:
        return CrossEncoder(nama_model)



def rerank_dengan_cross_encoder(
    pertanyaan: str,
    kandidat_chunks: list,
    score_threshold: float = config.CROSS_ENCODER_THRESHOLD,
    k_final: int = 8,
    model_name: str | None = None,
) -> list:
    """Melakukan reranking pada kandidat chunk menggunakan model Cross-Encoder.

    Match threshold hanya diperiksa pada tahap ini. Chunk dengan skor di bawah
    `score_threshold` akan difilter (dieliminasi).
    """
    if not kandidat_chunks:
        return []

    try:
        cross_encoder = _muat_cross_encoder(model_name)
        pasangan = [[pertanyaan, chunk.page_content] for chunk in kandidat_chunks]
        skor_list = cross_encoder.predict(pasangan)

        chunk_berpenilai = []
        for chunk, skor in zip(kandidat_chunks, skor_list):
            skor_float = float(skor)
            # Match threshold check
            if skor_float >= score_threshold:
                chunk_berpenilai.append((chunk, skor_float))

        # Urutkan dari skor Cross-Encoder tertinggi
        chunk_berpenilai.sort(key=lambda x: x[1], reverse=True)
        return [chunk for chunk, _ in chunk_berpenilai[:k_final]]
    except Exception as e:
        logger.warning("Gagal melakukan Cross-Encoder reranking: %s. Menggunakan kandidat asli.", e)
        return kandidat_chunks[:k_final]


def retrieval_hybrid_dengan_recency(
    pertanyaan: str,
    vector_store,
    bm25: BM25Okapi | None,
    chunks_bm25: list,
    k_final: int = 8,
    k_kandidat: int = 30,
    score_threshold: float = config.CROSS_ENCODER_THRESHOLD,
) -> list:
    """Melakukan hybrid retrieval menggabungkan BM25, Dense Vector, dan Recency,

    kemudian di-rerank dengan Cross-Encoder & difilter berdasarkan match threshold.
    """
    intent_temporal = deteksi_intent_temporal(pertanyaan)

    # Sinyal 1: BM25 (leksikal) - tanpa threshold
    hasil_bm25 = bm25_search(pertanyaan, bm25, chunks_bm25, k=k_kandidat) if bm25 else []

    # Sinyal 2: Semantic search (Qdrant) - tanpa threshold
    try:
        hasil_semantic = vector_store.similarity_search_with_score(pertanyaan, k=k_kandidat)
    except Exception:
        hasil_semantic = []

    # Jika tidak ada hasil sama sekali
    if not hasil_bm25 and not hasil_semantic:
        return []

    # Sinyal 3: Recency
    semua_chunk_unik = {id(c): c for c, _ in hasil_bm25}
    semua_chunk_unik.update({id(c): c for c, _ in hasil_semantic})
    urutan_by_tanggal = sorted(
        semua_chunk_unik.values(),
        key=lambda c: hitung_skor_recency(c.metadata.get("tanggal_publikasi") or c.metadata.get("tanggal", "")),
        reverse=True,
    )
    hasil_recency = [(c, None) for c in urutan_by_tanggal]

    bobot_recency = 1.6 if intent_temporal else 0.3
    bobot = [1.0, 1.0, bobot_recency]

    # Fusion RRF (BM25 + Cosine + Recency)
    hasil_gabungan = reciprocal_rank_fusion(
        [hasil_bm25, hasil_semantic, hasil_recency], bobot=bobot, K=60,
    )

    # Ambil pool kandidat hasil fusion untuk dinilai ulang oleh Cross-Encoder
    kandidat_fusion = [chunk for chunk, _ in hasil_gabungan[: max(k_kandidat, k_final * 2)]]

    # Reranking Cross-Encoder + Match Threshold filtering
    chunk_terpilih = rerank_dengan_cross_encoder(
        pertanyaan=pertanyaan,
        kandidat_chunks=kandidat_fusion,
        score_threshold=score_threshold,
        k_final=k_final,
    )
    return chunk_terpilih


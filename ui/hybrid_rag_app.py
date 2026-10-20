"""Streamlit app untuk asisten tanya-jawab kesehatan berbasis Hybrid RAG.

Menampilkan antarmuka chat interaktif yang mengambil jawaban dari
koleksi artikel kesehatan menggunakan pencarian kombinasi Lexical (BM25)
+ Dense Vector (Qdrant) + Recency Reranking + Multi-turn Chat.
"""

import logging
import sys
from pathlib import Path

# Memastikan root proyek ada di sys.path agar impor src selalu berhasil
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import streamlit as st

from src import config
from src.hybrid_rag.rag_chain import tanya
from src.hybrid_rag.schema_hybrid import ArtikelSumber, HasilRAG, JawabanTerkait

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konfigurasi halaman
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Tanya Kesehatan (Hybrid RAG Lokal)",
    page_icon="🩺",
    layout="wide",
)

st.title("🩺 Asisten Tanya-Jawab Kesehatan (Hybrid RAG)")
st.caption(
    "Jawaban dihasilkan dari pencarian kombinasi BM25, Qdrant, Recency, & Cross-Encoder Reranking. "
    "Bukan pengganti konsultasi dengan tenaga medis profesional."
)



# ---------------------------------------------------------------------------
# Komponen UI
# ---------------------------------------------------------------------------


def render_sitasi(sitasi_list: list[ArtikelSumber]) -> None:
    """Menampilkan daftar sitasi sumber artikel asli."""
    if not sitasi_list:
        return
    st.markdown("##### 📌 Sumber Sitasi Artikel:")
    for s in sitasi_list:
        url_text = f"[{s.judul}]({s.url})" if s.url else s.judul
        st.markdown(
            f"**[{s.nomor}]** {url_text}  \n"
            f"*{s.sumber} • {s.tanggal_publikasi}*"
        )


def _render_jawaban_terkait(
    jawaban_terkait: list[JawabanTerkait],
) -> None:
    """Menampilkan daftar referensi artikel terkait secara ringkas."""
    if not jawaban_terkait:
        return

    st.markdown("##### 🔗 Referensi & Artikel Terkait:")
    for jt in jawaban_terkait:
        if jt.url:
            st.markdown(f"- [{jt.judul_artikel}]({jt.url})")
        else:
            st.markdown(f"- {jt.judul_artikel}")



def _cek_koneksi_komponen() -> tuple[bool, bool]:
    """Melakukan pengecekan koneksi ke Qdrant dan Ollama."""
    qdrant_ok = False
    ollama_ok = False

    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(
            url=f"http://{config.QDRANT_HOST}:{config.QDRANT_PORT}",
            timeout=3,
        )
        client.get_collections()
        qdrant_ok = True
    except Exception:
        pass

    try:
        import httpx
        resp = httpx.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=3)
        ollama_ok = resp.status_code == 200
    except Exception:
        pass

    return qdrant_ok, ollama_ok


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("**Status Komponen**")

    qdrant_ok, ollama_ok = _cek_koneksi_komponen()
    if qdrant_ok:
        st.success(f"Qdrant: terhubung ({config.QDRANT_HOST}:{config.QDRANT_PORT})")
    else:
        st.error(f"Qdrant: tidak terhubung ({config.QDRANT_HOST}:{config.QDRANT_PORT})")
    if ollama_ok:
        st.success(f"Ollama: terhubung ({config.OLLAMA_BASE_URL})")
    else:
        st.error(f"Ollama: tidak terhubung ({config.OLLAMA_BASE_URL})")

    st.divider()
    if st.button("🗑️ Bersihkan Riwayat Chat"):
        st.session_state.riwayat = []
        st.rerun()

# ---------------------------------------------------------------------------
# Inisialisasi & render riwayat chat
# ---------------------------------------------------------------------------

if "riwayat" not in st.session_state:
    st.session_state.riwayat = []

for pesan in st.session_state.riwayat:
    with st.chat_message(pesan["role"]):
        if pesan.get("pertanyaan_dipahami"):
            st.caption(f"💡 Pertanyaan dipahami sebagai: \"{pesan['pertanyaan_dipahami']}\"")

        st.markdown(pesan["content"])

        if pesan["role"] == "assistant":
            if pesan.get("sitasi_terpakai"):
                render_sitasi(pesan["sitasi_terpakai"])
            if pesan.get("jawaban_terkait"):
                _render_jawaban_terkait(
                    pesan["jawaban_terkait"],
                )

# ---------------------------------------------------------------------------
# Input pertanyaan baru
# ---------------------------------------------------------------------------

pertanyaan_baru = st.chat_input("Ketik pertanyaan kesehatan Anda di sini...")

if pertanyaan_baru:
    # Snapshot riwayat percakapan sebelumnya untuk context rewriting
    riwayat_sebelumnya = list(st.session_state.riwayat)

    # Tampilkan pesan user
    st.session_state.riwayat.append({"role": "user", "content": pertanyaan_baru})
    with st.chat_message("user"):
        st.markdown(pertanyaan_baru)

    # Proses dan tampilkan jawaban assistant
    with st.chat_message("assistant"):
        with st.spinner("Mencari artikel relevan (BM25 + Qdrant + RRF + Cross-Encoder) & menyusun jawaban..."):

            try:
                hasil = tanya(pertanyaan_baru, riwayat=riwayat_sebelumnya)
            except Exception as e:
                st.error(
                    "Terjadi kesalahan saat memproses pertanyaan. "
                    "Pastikan Qdrant dan Ollama berjalan dengan benar."
                )
                logger.error("Error saat memproses pertanyaan: %s", e)
                st.stop()

            if hasil.pertanyaan_dipahami:
                st.caption(f"💡 Pertanyaan lanjutan dipahami sebagai: \"{hasil.pertanyaan_dipahami}\"")

            # Peringatan rujukan medis
            if hasil.perlu_rujukan_medis:
                st.warning(
                    "⚠️ Gejala yang Anda sebutkan berpotensi memerlukan "
                    "penanganan medis segera. Segera hubungi layanan gawat "
                    "darurat atau tenaga kesehatan terdekat."
                )

            st.markdown(hasil.jawaban_utama)

            if hasil.sitasi_terpakai:
                render_sitasi(hasil.sitasi_terpakai)

            _render_jawaban_terkait(hasil.jawaban_terkait)

    # Simpan ke riwayat
    st.session_state.riwayat.append({
        "role": "assistant",
        "content": hasil.jawaban_utama,
        "jawaban_terkait": hasil.jawaban_terkait,
        "sitasi_terpakai": hasil.sitasi_terpakai,
        "pertanyaan_dipahami": hasil.pertanyaan_dipahami,
    })

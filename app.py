"""Streamlit app untuk asisten tanya-jawab kesehatan berbasis RAG.

Menampilkan antarmuka chat interaktif yang mengambil jawaban dari
koleksi artikel kesehatan terpercaya di Qdrant vector database.
"""

import logging

import streamlit as st

from src import config
from src.rag_chain import cari_artikel_terkait_tambahan, tanya
from src.schema import HasilRAG, JawabanTerkait

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konfigurasi halaman
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Tanya Kesehatan (RAG Lokal)",
    page_icon="🩺",
    layout="wide",
)

st.title("🩺 Asisten Tanya-Jawab Kesehatan")
st.caption(
    "Jawaban dihasilkan dari koleksi berita kesehatan terpercaya. "
    "Bukan pengganti konsultasi dengan tenaga medis profesional."
)


# ---------------------------------------------------------------------------
# Komponen UI
# ---------------------------------------------------------------------------


def _render_jawaban_terkait(
    jawaban_terkait: list[JawabanTerkait],
    artikel_tambahan: list[dict],
) -> None:
    """Menampilkan kartu jawaban per-artikel dan daftar artikel tambahan.

    Args:
        jawaban_terkait: List objek JawabanTerkait dari hasil RAG.
        artikel_tambahan: List dict artikel tambahan (judul, url, sumber).
    """
    if not jawaban_terkait:
        return

    st.markdown("#### 🔗 Jawaban & Artikel Terkait")
    jumlah_kolom = min(len(jawaban_terkait), 3) or 1
    kolom = st.columns(jumlah_kolom)

    for i, jt in enumerate(jawaban_terkait):
        with kolom[i % jumlah_kolom]:
            with st.container(border=True):
                st.markdown(f"**{jt.judul_artikel}**")
                st.caption(f"{jt.sumber} · {jt.tanggal_publikasi}")
                st.write(jt.ringkasan)
                if jt.url:
                    st.link_button(
                        "Baca artikel lengkap",
                        jt.url,
                        use_container_width=True,
                    )

    if artikel_tambahan:
        with st.expander("📚 Topik lain yang mungkin relevan"):
            for a in artikel_tambahan:
                if a.get("url"):
                    st.markdown(
                        f"- [{a['judul']}]({a['url']}) — *{a['sumber']}*"
                    )
                else:
                    st.markdown(f"- {a['judul']} — *{a['sumber']}*")


def _cek_koneksi_komponen() -> tuple[bool, bool]:
    """Melakukan pengecekan koneksi ke Qdrant dan Ollama.

    Returns:
        Tuple (qdrant_ok, ollama_ok).
    """
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
    st.header("⚙️ Pengaturan")
    jumlah_terkait = st.slider("Jumlah artikel terkait tambahan", 0, 5, 3)
    st.divider()

    st.markdown("**Status Komponen**")
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
        st.markdown(pesan["content"])
        if pesan["role"] == "assistant" and pesan.get("jawaban_terkait"):
            _render_jawaban_terkait(
                pesan["jawaban_terkait"],
                pesan.get("artikel_tambahan", []),
            )

# ---------------------------------------------------------------------------
# Input pertanyaan baru
# ---------------------------------------------------------------------------

pertanyaan_baru = st.chat_input("Ketik pertanyaan kesehatan Anda di sini...")

if pertanyaan_baru:
    # Tampilkan pesan user
    st.session_state.riwayat.append({"role": "user", "content": pertanyaan_baru})
    with st.chat_message("user"):
        st.markdown(pertanyaan_baru)

    # Proses dan tampilkan jawaban assistant
    with st.chat_message("assistant"):
        with st.spinner("Mencari artikel relevan & menyusun jawaban..."):
            try:
                hasil = tanya(pertanyaan_baru)
            except Exception as e:
                st.error(
                    "Terjadi kesalahan saat memproses pertanyaan. "
                    "Pastikan Qdrant dan Ollama berjalan dengan benar."
                )
                logger.error("Error saat memproses pertanyaan: %s", e)
                st.stop()

            # Peringatan rujukan medis
            if hasil.perlu_rujukan_medis:
                st.warning(
                    "⚠️ Gejala yang Anda sebutkan berpotensi memerlukan "
                    "penanganan medis segera. Segera hubungi layanan gawat "
                    "darurat atau tenaga kesehatan terdekat."
                )

            st.markdown(hasil.jawaban_utama)

            # Cari artikel tambahan jika diminta
            artikel_tambahan: list[dict] = []
            if jumlah_terkait > 0:
                id_tampil = {jt.judul_artikel for jt in hasil.jawaban_terkait}
                try:
                    artikel_tambahan = cari_artikel_terkait_tambahan(
                        hasil, id_tampil, k=jumlah_terkait,
                    )
                except Exception as e:
                    logger.warning("Gagal mencari artikel tambahan: %s", e)

            _render_jawaban_terkait(hasil.jawaban_terkait, artikel_tambahan)

    # Simpan ke riwayat
    st.session_state.riwayat.append({
        "role": "assistant",
        "content": hasil.jawaban_utama,
        "jawaban_terkait": hasil.jawaban_terkait,
        "artikel_tambahan": artikel_tambahan,
    })

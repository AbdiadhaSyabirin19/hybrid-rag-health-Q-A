import streamlit as st
from src.rag_chain import tanya, cari_artikel_terkait_tambahan
 
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
 
# ---------- Sidebar ----------
with st.sidebar:
    st.header("⚙️ Pengaturan")
    jumlah_terkait = st.slider("Jumlah artikel terkait tambahan", 0, 5, 3)
    st.divider()
    st.markdown("**Status Komponen**")
    st.success("Qdrant: terhubung (localhost:6333)")
    st.success("Ollama: terhubung (localhost:11434)")
    st.divider()
    if st.button("🗑️ Bersihkan Riwayat Chat"):
        st.session_state.riwayat = []
        st.rerun()
 
# ---------- Inisialisasi riwayat chat ----------
if "riwayat" not in st.session_state:
    st.session_state.riwayat = []
 
# ---------- Render riwayat percakapan sebelumnya ----------
for pesan in st.session_state.riwayat:
    with st.chat_message(pesan["role"]):
        st.markdown(pesan["content"])
        if pesan["role"] == "assistant" and pesan.get("jawaban_terkait"):
            _render_jawaban_terkait(pesan["jawaban_terkait"], pesan.get("artikel_tambahan", []))
 
 
def _render_jawaban_terkait(jawaban_terkait, artikel_tambahan):
    st.markdown("#### 🔗 Jawaban & Artikel Terkait")
    kolom = st.columns(min(len(jawaban_terkait), 3) or 1)
    for i, jt in enumerate(jawaban_terkait):
        with kolom[i % len(kolom)]:
            with st.container(border=True):
                st.markdown(f"**{jt.judul_artikel}**")
                st.caption(f"{jt.sumber} · {jt.tanggal_publikasi}")
                st.write(jt.ringkasan)
                st.link_button("Baca artikel lengkap", jt.url, use_container_width=True)
    if artikel_tambahan:
        with st.expander("📚 Topik lain yang mungkin relevan"):
            for a in artikel_tambahan:
                st.markdown(f"- [{a['judul']}]({a['url']}) — *{a['sumber']}*")
 
 
# ---------- Input pertanyaan baru ----------
pertanyaan_baru = st.chat_input("Ketik pertanyaan kesehatan Anda di sini...")
 
if pertanyaan_baru:
    st.session_state.riwayat.append({"role": "user", "content": pertanyaan_baru})
    with st.chat_message("user"):
        st.markdown(pertanyaan_baru)
 
    with st.chat_message("assistant"):
        with st.spinner("Mencari artikel relevan & menyusun jawaban..."):
            hasil = tanya(pertanyaan_baru)
 
            if hasil.perlu_rujukan_medis:
                st.warning(
                    "⚠️ Gejala yang Anda sebutkan berpotensi memerlukan penanganan "
                    "medis segera. Segera hubungi layanan gawat darurat atau tenaga "
                    "kesehatan terdekat."
                )
 
            st.markdown(hasil.jawaban_utama)
 
            id_tampil = {jt.judul_artikel for jt in hasil.jawaban_terkait}
            artikel_tambahan = cari_artikel_terkait_tambahan(
                hasil, id_tampil, k=jumlah_terkait
            ) if jumlah_terkait > 0 else []
 
            _render_jawaban_terkait(hasil.jawaban_terkait, artikel_tambahan)
 
    st.session_state.riwayat.append({
        "role": "assistant",
        "content": hasil.jawaban_utama,
        "jawaban_terkait": hasil.jawaban_terkait,
        "artikel_tambahan": artikel_tambahan,
    })

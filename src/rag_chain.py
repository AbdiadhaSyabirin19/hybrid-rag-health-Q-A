from langchain_ollama import OllamaEmbeddings, ChatOllama
from langchain_qdrant import QdrantVectorStore
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from collections import defaultdict
from src import config
 
embeddings = OllamaEmbeddings(
    model=config.EMBEDDING_MODEL,
    base_url=config.OLLAMA_BASE_URL,
)
 
vector_store = QdrantVectorStore.from_existing_collection(
    embedding=embeddings,
    url=f"http://{config.QDRANT_HOST}:{config.QDRANT_PORT}",
    collection_name=config.COLLECTION_NAME,
)
 
retriever = vector_store.as_retriever(
    search_type="similarity",
    search_kwargs={"k": config.TOP_K_RETRIEVAL},
)


 
TEMPLATE_SISTEM = """Anda adalah asisten informasi kesehatan yang membantu \
menjelaskan topik kesehatan berdasarkan artikel berita kesehatan terpercaya.
 
ATURAN PENTING:
1. Jawab HANYA berdasarkan konteks artikel yang diberikan di bawah. \
Jangan menambahkan informasi dari pengetahuan lain di luar konteks.
2. Jika konteks tidak cukup untuk menjawab, katakan dengan jujur bahwa \
informasi tersebut tidak tersedia dalam basis data artikel, jangan mengarang.
3. Jangan memberikan diagnosis pasti maupun menjanjikan kesembuhan. \
Gunakan bahasa yang hati-hati, misalnya 'gejala ini dapat mengindikasikan...'.
4. Untuk gejala berat atau berpotensi darurat (misal nyeri dada hebat, \
sesak napas berat), sarankan pengguna segera menghubungi layanan gawat \
darurat atau tenaga medis, jangan hanya memberi info tekstual.
5. Gunakan bahasa Indonesia yang jelas dan mudah dipahami orang awam.
 
Konteks artikel:
{context}
"""
 
prompt = ChatPromptTemplate.from_messages([
    ("system", TEMPLATE_SISTEM),
    ("human", "{question}"),
])
 
llm = ChatOllama(
    model=config.LLM_MODEL,
    base_url=config.OLLAMA_BASE_URL,
    temperature=0.2,   # rendah agar jawaban konsisten & tidak terlalu kreatif
    extra_body={"think": config.THINKING_MODE_DEFAULT},  # matikan thinking mode Qwen3 secara default
)
 
def _dapatkan_metadata_artikel(meta):
    """Mengekstrak metadata artikel secara aman dengan penanganan format/fallback."""
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


def format_konteks(dokumen_list):
    bagian = []
    for i, doc in enumerate(dokumen_list, 1):
        _, judul, sumber, _, tanggal_publikasi = _dapatkan_metadata_artikel(doc.metadata)
        bagian.append(
            f"[Artikel {i}] {judul} "
            f"(sumber: {sumber}, {tanggal_publikasi})\n"
            f"{doc.page_content}"
        )
    return "\n\n".join(bagian)
 
rag_chain = (
    {"context": retriever | format_konteks, "question": RunnablePassthrough()}
    | prompt
    | llm
    | StrOutputParser()
)
 

 
def kelompokkan_per_artikel(dokumen_list):
    """Mengelompokkan chunk retrieval berdasarkan artikel asalnya."""
    kelompok = defaultdict(list)
    for doc in dokumen_list:
        artikel_id, _, _, _, _ = _dapatkan_metadata_artikel(doc.metadata)
        kelompok[artikel_id].append(doc)
 
    artikel_list = []
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


from src.schema import HasilRAG
from langchain_ollama import ChatOllama
 
def _buat_llm_terstruktur(gunakan_thinking: bool):
    """Membuat instance ChatOllama dengan thinking mode sesuai kebutuhan."""
    llm_dinamis = ChatOllama(
        model=config.LLM_MODEL,
        base_url=config.OLLAMA_BASE_URL,
        temperature=0.2,
        extra_body={"think": gunakan_thinking},
    )
    return llm_dinamis.with_structured_output(HasilRAG)
 
TEMPLATE_JAWABAN_TERKAIT = """Berdasarkan beberapa artikel kesehatan di bawah, \
susun jawaban dengan format berikut:
1. jawaban_utama: satu jawaban terpadu yang menjawab pertanyaan pengguna, \
menggabungkan informasi dari semua artikel yang relevan.
2. jawaban_terkait: untuk SETIAP artikel di bawah, tuliskan ringkasan 1-2 kalimat \
yang menjawab pertanyaan dari sudut pandang artikel tersebut secara spesifik.
3. perlu_rujukan_medis: true jika pertanyaan mengindikasikan gejala berat/darurat.
 
Ikuti ATURAN PENTING (safety) yang sudah dijelaskan sebelumnya. Jangan mengarang \
informasi yang tidak ada dalam artikel.
 
Pertanyaan pengguna: {question}
 
Artikel-artikel:
{artikel_terformat}
"""
 
def format_artikel_untuk_prompt(artikel_list):
    bagian = []
    for i, a in enumerate(artikel_list, 1):
        bagian.append(
            f"[Artikel {i}] ID: {a['artikel_id']} | {a['judul']} "
            f"({a['sumber']}, {a['tanggal_publikasi']})\n{a['teks']}"
        )
    return "\n\n".join(bagian)
 
def tanya(pertanyaan: str) -> HasilRAG:
    dokumen_relevan = retriever.invoke(pertanyaan)
    artikel_list = kelompokkan_per_artikel(dokumen_relevan)
    artikel_terformat = format_artikel_untuk_prompt(artikel_list)
 
    # nyalakan thinking mode otomatis jika pertanyaan butuh menggabungkan
    # informasi dari lebih dari satu artikel (indikasi kasus multi-hop)
    perlu_thinking = len(artikel_list) > 1
    llm_terstruktur = _buat_llm_terstruktur(gunakan_thinking=perlu_thinking)
 
    prompt_lengkap = TEMPLATE_JAWABAN_TERKAIT.format(
        question=pertanyaan,
        artikel_terformat=artikel_terformat,
    )
    hasil: HasilRAG = llm_terstruktur.invoke(prompt_lengkap)
 
    # lengkapi url & sumber jika LLM tidak menyalinnya dengan sempurna,
    # dengan mencocokkan judul_artikel ke metadata asli (lebih andal)
    peta_artikel = {a["judul"].strip().lower(): a for a in artikel_list}
    for jt in hasil.jawaban_terkait:
        cocok = peta_artikel.get(jt.judul_artikel.strip().lower())
        if cocok:
            jt.url = cocok["url"]
            jt.sumber = cocok["sumber"]
            jt.tanggal_publikasi = cocok["tanggal_publikasi"]
    return hasil


def cari_artikel_terkait_tambahan(hasil: HasilRAG, artikel_sudah_tampil: set, k: int = 3):
    """Mencari artikel tambahan yang topiknya berdekatan dengan jawaban utama."""
    kandidat = vector_store.similarity_search(hasil.jawaban_utama, k=k + 5)
    artikel_terkait_tambahan = []
    id_sudah_ada = set()
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


if __name__ == "__main__":
    # contoh pemanggilan jika dijalankan secara langsung:
    print("Menjalankan uji coba chain...")
    jawaban = rag_chain.invoke("Apa gejala awal demam berdarah dengue?")
    print(jawaban)



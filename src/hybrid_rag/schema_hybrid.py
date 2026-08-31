"""Schema Pydantic untuk output terstruktur dari Hybrid RAG chain."""

from pydantic import BaseModel, Field


class JawabanTerkait(BaseModel):
    """Ringkasan jawaban dari satu artikel sumber yang relevan."""

    judul_artikel: str = Field(
        description="Judul artikel sumber",
    )
    ringkasan: str = Field(
        description="Ringkasan jawaban 1-2 kalimat dari artikel ini",
    )
    sumber: str = Field(
        default="",
        description="Nama penerbit atau sumber artikel (misal: KlikDokter)",
    )
    url: str = Field(
        default="",
        description="URL lengkap menuju artikel asli",
    )
    tanggal_publikasi: str = Field(
        default="",
        description="Tanggal publikasi artikel",
    )


class ArtikelSumber(BaseModel):
    """Metadata satu artikel, SELALU diisi dari data retrieval asli (bukan dari LLM)."""

    nomor: int
    judul: str
    sumber: str
    url: str
    tanggal_publikasi: str


class HasilLLM(BaseModel):
    """Skema output MENTAH dari LLM -- hanya berisi hal yang memang perlu digenerate."""

    jawaban_utama: str = Field(
        description=(
            "Jawaban lengkap untuk pengguna. WAJIB menambahkan sitasi inline "
            "berformat [1], [2], dst tepat setelah klaim yang diambil dari artikel "
            "bernomor tersebut."
        )
    )
    perlu_rujukan_medis: bool = Field(
        default=False,
        description="True jika pertanyaan mengindikasikan kondisi berat/darurat",
    )


class HasilRAG(BaseModel):
    """Hasil lengkap dari pipeline RAG: jawaban utama, jawaban per-artikel, dan flag medis."""

    jawaban_utama: str = Field(
        description="Jawaban terpadu yang menggabungkan seluruh konteks artikel",
    )
    jawaban_terkait: list[JawabanTerkait] = Field(
        default_factory=list,
        description="Daftar ringkasan jawaban per-artikel yang relevan",
    )
    sitasi_terpakai: list[ArtikelSumber] = Field(
        default_factory=list,
        description="Daftar artikel sumber yang benar-benar disitasi",
    )
    perlu_rujukan_medis: bool = Field(
        default=False,
        description="True jika pertanyaan mengindikasikan kondisi berat/darurat",
    )
    pertanyaan_dipahami: str | None = Field(
        default=None,
        description="Pertanyaan yang telah direformulasi menjadi standalone (jika ada riwayat)",
    )

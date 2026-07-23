from pydantic import BaseModel, Field
 
class JawabanTerkait(BaseModel):
    judul_artikel: str = Field(description="Judul artikel sumber")
    ringkasan: str = Field(description="Ringkasan jawaban 1-2 kalimat dari artikel ini")
    sumber: str = ""
    url: str = ""
    tanggal_publikasi: str = ""
 
class HasilRAG(BaseModel):
    jawaban_utama: str = Field(description="Jawaban terpadu yang menggabungkan seluruh konteks")
    jawaban_terkait: list[JawabanTerkait] = Field(
        description="Daftar ringkasan jawaban per-artikel yang relevan dan saling terkait"
    )
    perlu_rujukan_medis: bool = Field(
        description="True jika pertanyaan mengindikasikan kondisi berat/darurat"
    )

# Panduan Menjalankan Layanan Lokal (Qdrant & Ollama)

Dokumen ini berisi panduan langkah demi langkah untuk mengaktifkan dan menjalankan Qdrant (Database Vektor) dan Ollama (Mesin LLM & Embedding Lokal) untuk sistem RAG Kesehatan Anda.

---

## 1. Menjalankan Qdrant (Database Vektor)

Qdrant dijalankan menggunakan Docker melalui konfigurasi `docker-compose.yml` yang sudah disediakan di root direktori proyek.

### Langkah-langkah:
1. **Buka Aplikasi Docker**: Pastikan aplikasi **Docker Desktop** di Windows Anda sudah terbuka dan berjalan (ikon Docker di taskbar menunjukkan warna hijau/aktif).
2. **Buka Terminal/PowerShell** di root direktori proyek (`c:\Dokumen\riset\project`).
3. **Jalankan Perintah Docker Compose**:
   ```bash
   docker-compose up -d
   ```
   *Catatan: Parameter `-d` (detached mode) digunakan agar container berjalan di latar belakang (background) sehingga terminal Anda tetap bisa digunakan.*
4. **Verifikasi**:
   Buka browser Anda dan akses tautan berikut untuk memastikan Qdrant Dashboard berjalan:
   [http://localhost:6333/dashboard](http://localhost:6333/dashboard)

---

## 2. Menjalankan Ollama (LLM & Embedding)

Ollama bertugas sebagai mesin kecerdasan buatan lokal yang memproses teks menjadi representasi vektor (embedding) dan menjalan model bahasa (LLM).

### Langkah-langkah:
1. **Jalankan Aplikasi Ollama**:
   Pastikan aplikasi **Ollama** di Windows Anda sudah aktif (biasanya otomatis berjalan di latar belakang dan memunculkan ikon kepala llama di system tray kanan bawah Windows).
2. **Download Model yang Dibutuhkan**:
   Proyek ini menggunakan model embedding `nomic-embed-text` dan model LLM `qwen3:8b` (atau model lain sesuai dengan `src/config.py`).
   Jalankan perintah berikut di terminal Anda untuk mengunduh model:
   * **Mengunduh Model Embedding (Wajib untuk Ingestion)**:
     ```bash
     ollama pull nomic-embed-text
     ```
   * **Mengunduh Model LLM (Wajib untuk RAG Chat)**:
     ```bash
     ollama pull qwen3:8b
     ```
3. **Verifikasi**:
   Pastikan port Ollama telah merespons dengan mengetikkan perintah berikut di browser atau terminal:
   [http://localhost:11434](http://localhost:11434) (Harus menampilkan pesan *"Ollama is running"*).

---

## 3. Menjalankan Pipeline Ingestion

Setelah kedua layanan di atas aktif, Anda dapat langsung melakukan pemrosesan data (ingestion) dengan mengeksekusi perintah berikut pada virtual environment:

```powershell
# 1. Aktifkan virtual environment
.\venv\Scripts\activate

# 2. Jalankan pemrosesan dan penyimpanan data ke Qdrant
python -m src.ingest
```

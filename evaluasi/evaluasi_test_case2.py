"""Script Evaluasi RAGAS Khusus Test Case 2 (QA Kesehatan).

Script ini mengevaluasi pipeline RAG (Hybrid RAG dan/atau Basic RAG) menggunakan
dataset kasus uji baru pada folder 'test case2/test_cases_qa_rag_kesehatan.json'.

Fitur Utama:
1. Membaca format hierarkis Test Case 2:
   - happy-path (60 kasus)
   - edge-cases (24 kasus)
   - out-of-scope (22 kasus)
   - adversarial (12 kasus)
2. Mendukung pemilihan pipeline:
   - Hybrid RAG (Vector + BM25 + Recency Decay)
   - Basic RAG (Vector-only Retrieval via Qdrant)
   - Both (menjalankan keduanya untuk perbandingan)
3. Filter kategori: bisa menguji kategori tertentu (contoh: --category adversarial).
4. Opsi pengujian cepat: --limit N (contoh: --limit 3 untuk smoke test).
5. Menyimpan laporan komprehensif (JSON, CSV, Excel berbobot multi-sheet)
   ke direktori khusus tanpa menimpa atau mempengaruhi evaluasi yang sudah ada.

Penggunaan CLI:
    # Evaluasi Hybrid RAG pada semua kategori test case 2 (smoke test 3 sampel)
    python evaluasi_test_case2.py --mode hybrid --limit 3

    # Evaluasi kategori tertentu (misal: adversarial)
    python evaluasi_test_case2.py --mode hybrid --category adversarial

    # Evaluasi Basic RAG
    python evaluasi_test_case2.py --mode basic --limit 5

    # Evaluasi Kedua Pipeline (Hybrid & Basic)
    python evaluasi_test_case2.py --mode both --limit 5
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

# Pengaturan encoding konsol Windows agar karakter UTF-8 aman
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Memastikan root proyek ada di sys.path agar impor src selalu berhasil
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Setup folder log
_log_dir = ROOT_DIR / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_log_dir / "evaluasi_test_case2.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("evaluasi_test_case2")

# Import komponen evaluasi eksisting
from src.eval_core import (
    _METRIC_COLS,
    _semua_nan,
    buat_evaluator_ragas,
    evaluasi_dengan_ragas,
    hitung_skor_heuristic_fallback,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Loader Khusus Test Case 2
# ─────────────────────────────────────────────────────────────────────────────

def muat_test_case2(
    file_path: str = "test case2/test_cases_qa_rag_kesehatan.json",
    kategori_filter: Optional[str] = None,
) -> tuple[dict, list[dict]]:
    """Memuat kasus uji dari format Test Case 2 yang memiliki struktur hierarkis.

    Returns:
        tuple (metadata_dataset, list_kasus_uji)
    """
    path = Path(file_path)
    if not path.is_file():
        # Coba fallback jika path relatif
        candidates = [
            ROOT_DIR / file_path,
            ROOT_DIR / "test case2" / "test_cases_qa_rag_kesehatan.json",
            Path("test case2") / "test_cases_qa_rag_kesehatan.json",
        ]
        found = False
        for cand in candidates:
            if cand.is_file():
                path = cand
                found = True
                break
        if not found:
            raise FileNotFoundError(f"File test case 2 tidak ditemukan di: {file_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    meta_dataset = {
        "dataset_name": data.get("dataset_name", "Test Case Evaluasi QA Kesehatan"),
        "language": data.get("language", "id"),
        "knowledge_base": data.get("knowledge_base", {}),
        "usage_notes": data.get("usage_notes", []),
        "summary": data.get("summary", {}),
    }

    raw_test_cases = data.get("test_cases", {})
    daftar_kasus = []

    valid_categories = list(raw_test_cases.keys())

    for kat, items in raw_test_cases.items():
        if kategori_filter and kategori_filter.lower() != "all":
            if kat.lower() != kategori_filter.lower():
                continue

        for item in items:
            tc = item.copy()
            # Standarisasi kolom
            tc["category"] = kat
            # expected_answer dijadikan ground_truth untuk evaluasi RAGAS
            tc["ground_truth"] = tc.get("expected_answer", "")
            # Ambil subtipe jika ada (edge_type, out_of_scope_type, attack_type)
            subtype = (
                tc.get("edge_type")
                or tc.get("out_of_scope_type")
                or tc.get("attack_type")
                or "-"
            )
            tc["subtype"] = subtype
            daftar_kasus.append(tc)

    logger.info(
        f"Memuat {len(daftar_kasus)} kasus uji dari '{path.as_posix()}' "
        f"(Filter Kategori: {kategori_filter or 'Semua'}, Kategori Tersedia: {valid_categories})"
    )
    return meta_dataset, daftar_kasus


# ─────────────────────────────────────────────────────────────────────────────
# 2. Fungsi Eksekusi Pipeline RAG
# ─────────────────────────────────────────────────────────────────────────────

def buat_eksekutor_hybrid() -> Callable[[str], tuple[str, list[str], dict]]:
    """Menyiapkan runner eksekusi Hybrid RAG."""
    from src.hybrid_rag.rag_chain import tanya as tanya_hybrid

    def eksekusi_hybrid_rag(pertanyaan: str) -> tuple[str, list[str], dict]:
        t0 = time.time()
        hasil = tanya_hybrid(pertanyaan)
        durasi = time.time() - t0

        konteks = getattr(hasil, "konteks_retrieval", None)
        if not konteks:
            konteks = ["Tidak ada konteks ditemukan."]

        meta_info = {
            "durasi_detik": round(durasi, 2),
            "perlu_rujukan_medis": getattr(hasil, "perlu_rujukan_medis", False),
            "jumlah_sitasi": len(getattr(hasil, "sitasi_terpakai", [])),
        }
        return hasil.jawaban_utama, konteks, meta_info

    return eksekusi_hybrid_rag


def buat_eksekutor_basic() -> Callable[[str], tuple[str, list[str], dict]]:
    """Menyiapkan runner eksekusi Basic RAG."""
    from src.basic_rag.rag_chain import dapatkan_dokumen_relevan, tanya as tanya_basic

    def eksekusi_basic_rag(pertanyaan: str) -> tuple[str, list[str], dict]:
        t0 = time.time()
        hasil = tanya_basic(pertanyaan)
        durasi = time.time() - t0

        dokumen_relevan = dapatkan_dokumen_relevan(pertanyaan)
        konteks = [doc.page_content for doc in dokumen_relevan] if dokumen_relevan else ["Tidak ada konteks ditemukan."]

        meta_info = {
            "durasi_detik": round(durasi, 2),
            "perlu_rujukan_medis": getattr(hasil, "perlu_rujukan_medis", False),
            "jumlah_sitasi": len(getattr(hasil, "jawaban_terkait", [])),
        }
        return hasil.jawaban_utama, konteks, meta_info

    return eksekusi_basic_rag


# ─────────────────────────────────────────────────────────────────────────────
# 3. Simpan Laporan dengan Analisis Kategori & Metadata Test Case 2
# ─────────────────────────────────────────────────────────────────────────────

def simpan_laporan_test_case2(
    df_eval: pd.DataFrame,
    mode_name: str,
    meta_dataset: dict,
    output_dir: Path | str,
    durasi_total: float,
) -> dict:
    """Menyimpan laporan evaluasi dalam format JSON, CSV, dan Excel dengan sheet terstruktur."""
    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        output_dir = ROOT_DIR / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = mode_name.lower().replace(" ", "_")

    file_csv = output_dir / f"laporan_evaluasi_tc2_{slug}.csv"
    file_excel = output_dir / f"laporan_evaluasi_tc2_{slug}.xlsx"
    file_json = output_dir / f"laporan_evaluasi_tc2_{slug}.json"

    print("\n" + "=" * 80)
    print(f"📊 RINGKASAN EVALUASI TEST CASE 2 - [{mode_name.upper()}]")
    print("=" * 80)

    metric_cols = [c for c in _METRIC_COLS if c in df_eval.columns]

    print(f"{'Metrik Evaluasi':<25} | {'Skor Rata-Rata':<15}")
    print("-" * 45)
    mean_scores = {}
    for m in metric_cols:
        val = df_eval[m].mean()
        mean_scores[m] = round(float(val), 4) if pd.notna(val) else 0.0
        print(f"{m:<25} | {mean_scores[m]:<15.4f}")

    if "durasi_detik" in df_eval.columns:
        avg_dur = df_eval["durasi_detik"].mean()
        mean_scores["avg_durasi_detik"] = round(float(avg_dur), 2)
        print(f"{'Rata-rata Durasi (detik)':<25} | {mean_scores['avg_durasi_detik']:<15.2f}")

    # Hitung hit rate source_article_title untuk kasus happy-path jika ada
    if "hit_source_article" in df_eval.columns:
        hp_mask = df_eval["category"] == "happy-path"
        if hp_mask.any():
            hp_hits = df_eval.loc[hp_mask, "hit_source_article"]
            valid_hits = hp_hits.dropna()
            if not valid_hits.empty:
                hit_rate = round(float(valid_hits.mean()), 4)
                mean_scores["happy_path_retrieval_hit_rate"] = hit_rate
                print(f"{'Happy-Path Hit Title':<25} | {hit_rate:<15.4f}")

    print("=" * 80)

    # 1. Simpan CSV
    df_eval.to_csv(file_csv, index=False, encoding="utf-8-sig")
    print(f"💾 Laporan CSV tersimpan ke   : {file_csv}")

    # 2. Simpan Excel (.xlsx) dengan lembar kerja informatif
    try:
        df_excel_detail = df_eval.copy()
        if "contexts" in df_excel_detail.columns:
            df_excel_detail["contexts"] = df_excel_detail["contexts"].apply(
                lambda c: "\n---\n".join(c) if isinstance(c, list) else str(c)
            )

        df_ringkasan = pd.DataFrame([
            {"Info / Metrik": "Pipeline Evaluasi", "Nilai": mode_name},
            {"Info / Metrik": "Dataset Sumber", "Nilai": meta_dataset.get("dataset_name", "Test Case 2")},
            {"Info / Metrik": "Total Kasus Dievaluasi", "Nilai": len(df_eval)},
            {"Info / Metrik": "Total Durasi (detik)", "Nilai": round(durasi_total, 2)},
            *[{"Info / Metrik": f"Rata-rata {m}", "Nilai": mean_scores[m]} for m in metric_cols],
            {"Info / Metrik": "Rata-rata Durasi per Soal (s)", "Nilai": mean_scores.get("avg_durasi_detik", "-")},
            {"Info / Metrik": "Happy-Path Retrieval Hit Rate", "Nilai": mean_scores.get("happy_path_retrieval_hit_rate", "-")},
        ])

        # Analisis per Kategori
        df_kategori = pd.DataFrame()
        if "category" in df_eval.columns:
            cols_to_agg = {m: "mean" for m in metric_cols}
            if "durasi_detik" in df_eval.columns:
                cols_to_agg["durasi_detik"] = "mean"
            if "id" in df_eval.columns:
                cols_to_agg["id"] = "count"

            df_kategori = df_eval.groupby("category").agg(cols_to_agg).round(4).reset_index()
            if "id" in df_kategori.columns:
                df_kategori.rename(columns={"id": "jumlah_kasus"}, inplace=True)

        with pd.ExcelWriter(file_excel, engine="openpyxl") as writer:
            df_ringkasan.to_excel(writer, sheet_name="Ringkasan_Metrik", index=False)
            if not df_kategori.empty:
                df_kategori.to_excel(writer, sheet_name="Analisis_Kategori", index=False)
            df_excel_detail.to_excel(writer, sheet_name="Rincian_Kasus_TC2", index=False)

        print(f"💾 Laporan Excel tersimpan ke : {file_excel}")
    except Exception as err:
        logger.warning(f"Gagal menyimpan ke Excel ({file_excel}): {err}")

    # 3. Simpan JSON
    def _safe_val(v):
        if isinstance(v, float) and (v != v):  # NaN check
            return None
        return v

    rincian_bersih = [
        {k: _safe_val(v) for k, v in rec.items()}
        for rec in df_eval.to_dict(orient="records")
    ]

    laporan_final = {
        "mode": mode_name,
        "dataset_name": meta_dataset.get("dataset_name"),
        "total_kasus": len(df_eval),
        "durasi_total_detik": round(durasi_total, 2),
        "skor_rata_rata": mean_scores,
        "rincian": rincian_bersih,
    }

    with open(file_json, "w", encoding="utf-8") as f:
        json.dump(laporan_final, f, ensure_ascii=False, indent=2)
    print(f"💾 Laporan JSON tersimpan ke  : {file_json}")
    print("=" * 80)

    return laporan_final


# ─────────────────────────────────────────────────────────────────────────────
# 4. Runner Utama Evaluasi Test Case 2
# ─────────────────────────────────────────────────────────────────────────────

def jalankan_evaluasi_test_case2(
    mode: str = "hybrid",
    kategori_filter: Optional[str] = "all",
    limit: Optional[int] = None,
    evaluator_mode: str = "ragas",
    test_case_file: str = "test case2/test_cases_qa_rag_kesehatan.json",
    output_dir: str = "hasil evaluasi/test_case2",
) -> dict:
    """Menjalankan evaluasi lengkap pada Test Case 2."""
    t_start = time.time()
    mode_name = "Hybrid RAG" if mode.lower() == "hybrid" else "Basic RAG"

    print("=" * 80)
    print(f"🚀 MEMULAI EVALUASI TEST CASE 2 - [{mode_name.upper()}]")
    print(f"📁 Sumber Test Case : {test_case_file}")
    print(f"🔍 Filter Kategori  : {kategori_filter or 'Semua'}")
    if limit:
        print(f"⚡ Batas Sampel     : {limit} soal")
    print("=" * 80)

    # 1. Muat Test Case
    meta_dataset, daftar_kasus = muat_test_case2(test_case_file, kategori_filter)
    if not daftar_kasus:
        logger.error("Tidak ada kasus uji yang ditemukan sesuai filter!")
        return {}

    if limit and limit > 0:
        daftar_kasus = daftar_kasus[:limit]

    total_kasus = len(daftar_kasus)
    print(f"📋 Total Kasus yang Akan Dievaluasi: {total_kasus}")

    # 2. Siapkan Eksekutor
    if mode.lower() == "hybrid":
        eksekutor = buat_eksekutor_hybrid()
    else:
        eksekutor = buat_eksekutor_basic()

    # 3. Loop Inferensi Pipeline
    dataset_records = []
    for idx, tc in enumerate(daftar_kasus, 1):
        tc_id = tc.get("id", f"TC-{idx:03d}")
        q = tc.get("question", "")
        kat = tc.get("category", "unknown")
        gt = tc.get("ground_truth", "")
        expected_behavior = tc.get("expected_behavior", "")
        source_title = tc.get("source_article_title", "")
        subtype = tc.get("subtype", "-")

        print(f"[{idx}/{total_kasus}] [{tc_id}] [{kat}] Memproses: \"{q[:60]}...\"")

        try:
            answer, contexts, meta = eksekutor(q)

            if not contexts:
                contexts = ["Tidak ada konteks ditemukan."]

            # Cek hit title pada retrieved context jika source_article_title ada
            hit_title = None
            if source_title:
                joined_ctx = " ".join(contexts).lower()
                hit_title = source_title.lower() in joined_ctx

            dataset_records.append({
                "id": tc_id,
                "category": kat,
                "subtype": subtype,
                "question": q,
                "answer": answer,
                "contexts": contexts,
                "ground_truth": gt,
                "expected_behavior": expected_behavior,
                "source_article_title": source_title,
                "hit_source_article": hit_title,
                "durasi_detik": meta.get("durasi_detik", 0),
                "perlu_rujukan_medis": meta.get("perlu_rujukan_medis", False),
            })
            print(f"   ✓ Selesai ({meta.get('durasi_detik')}s | {len(contexts)} konteks)")

        except Exception as err:
            logger.error(f"   ❌ Gagal inferensi pada {tc_id}: {err}", exc_info=True)
            dataset_records.append({
                "id": tc_id,
                "category": kat,
                "subtype": subtype,
                "question": q,
                "answer": f"Error: {err}",
                "contexts": ["Tidak ada konteks"],
                "ground_truth": gt,
                "expected_behavior": expected_behavior,
                "source_article_title": source_title,
                "hit_source_article": False if source_title else None,
                "durasi_detik": 0,
                "perlu_rujukan_medis": False,
            })

    # 4. Evaluasi Metrik
    if evaluator_mode == "heuristic":
        logger.info("Menggunakan mode evaluasi heuristik cepat (tanpa LLM RAGAS).")
        df_eval = hitung_skor_heuristic_fallback(dataset_records)
    else:
        evaluator_llm, evaluator_embeddings = buat_evaluator_ragas()

        if evaluator_llm is None or evaluator_embeddings is None:
            logger.warning(
                "Evaluator LLM/Embeddings Ollama tidak siap; "
                "menggunakan evaluasi heuristik fallback."
            )
            df_eval = hitung_skor_heuristic_fallback(dataset_records)
        else:
            df_eval = evaluasi_dengan_ragas(
                dataset_records=dataset_records,
                evaluator_llm=evaluator_llm,
                evaluator_embeddings=evaluator_embeddings,
            )

            if _semua_nan(df_eval, _METRIC_COLS):
                logger.warning(
                    "Skor evaluasi RAGAS masih NaN setelah kalkulasi. "
                    "Beralih ke evaluasi heuristik fallback."
                )
                df_eval = hitung_skor_heuristic_fallback(dataset_records)

    # Pastikan metadata tambahan dari test case 2 tetap ada di dataframe
    for col in [
        "id",
        "category",
        "subtype",
        "expected_behavior",
        "source_article_title",
        "hit_source_article",
        "durasi_detik",
        "perlu_rujukan_medis",
    ]:
        if dataset_records and col in dataset_records[0]:
            df_eval[col] = [r.get(col) for r in dataset_records]

    durasi_total = time.time() - t_start

    # 5. Simpan dan Format Laporan
    return simpan_laporan_test_case2(
        df_eval=df_eval,
        mode_name=mode_name,
        meta_dataset=meta_dataset,
        output_dir=Path(output_dir),
        durasi_total=durasi_total,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Evaluasi Sistem RAG Kesehatan Menggunakan Test Case 2 (Ollama & RAGAS)"
    )
    parser.add_argument(
        "--mode",
        choices=["hybrid", "basic", "both"],
        default="hybrid",
        help="Pilihan pipeline yang dievaluasi: hybrid (default), basic, atau both",
    )
    parser.add_argument(
        "--category",
        default="all",
        help="Filter kategori kasus uji: all (default), happy-path, edge-cases, out-of-scope, adversarial",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Batasi jumlah pertanyaan untuk uji coba cepat (contoh: --limit 3)",
    )
    parser.add_argument(
        "--evaluator",
        choices=["ragas", "heuristic"],
        default="ragas",
        help="Metode evaluasi metrik: ragas (default, via LLM Ollama) atau heuristic (fallback berbasis kata kunci)",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Shortcut untuk evaluasi kilat: menggunakan evaluator heuristic tanpa menunggu LLM RAGAS",
    )
    parser.add_argument(
        "--test-case-file",
        default=str(ROOT_DIR / "test case2" / "test_cases_qa_rag_kesehatan.json"),
        help="Lokasi file JSON Test Case 2 (default: test case2/test_cases_qa_rag_kesehatan.json)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT_DIR / "hasil evaluasi" / "test_case2"),
        help="Direktori penyimpanan laporan evaluasi (default: hasil evaluasi/test_case2)",
    )

    args = parser.parse_args()
    eval_mode = "heuristic" if args.fast else args.evaluator

    if args.mode == "both":
        print("\n🔹 [1/2] Menjalankan Evaluasi Pipeline Hybrid RAG...")
        jalankan_evaluasi_test_case2(
            mode="hybrid",
            kategori_filter=args.category,
            limit=args.limit,
            evaluator_mode=eval_mode,
            test_case_file=args.test_case_file,
            output_dir=args.output_dir,
        )

        print("\n🔹 [2/2] Menjalankan Evaluasi Pipeline Basic RAG...")
        jalankan_evaluasi_test_case2(
            mode="basic",
            kategori_filter=args.category,
            limit=args.limit,
            evaluator_mode=eval_mode,
            test_case_file=args.test_case_file,
            output_dir=args.output_dir,
        )
    else:
        jalankan_evaluasi_test_case2(
            mode=args.mode,
            kategori_filter=args.category,
            limit=args.limit,
            evaluator_mode=eval_mode,
            test_case_file=args.test_case_file,
            output_dir=args.output_dir,
        )


if __name__ == "__main__":
    main()

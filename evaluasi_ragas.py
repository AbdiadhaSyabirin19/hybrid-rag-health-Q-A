"""Script Evaluasi Sistem RAG Kesehatan Menggunakan Framework RAGAS (Full Lokal Ollama).

Script ini mengevaluasi pipeline RAG (Basic RAG atau Hybrid RAG) 100% secara lokal
menggunakan Ollama dan metrik RAGAS (Retrieval Augmented Generation Assessment):
1. Faithfulness (Keandalan / Faktualitas jawaban terhadap konteks)
2. Answer Relevancy (Kerelevanan jawaban terhadap pertanyaan)
3. Context Precision (Presisi retrieval konteks yang ditarik)
4. Context Recall (Kelengkapan retrieval konteks dibanding ground truth)

Menggunakan evaluator Ollama lokal (Qwen/Llama & model embedding lokal),
serta mendukung pengujian parsial atau seluruh berkas dari direktori 'test_cases/'.

Penggunaan CLI:
    python evaluasi_ragas.py --mode hybrid --target test_cases --limit 10
    python evaluasi_ragas.py --mode basic --target test_cases/happy_path/happy_path.json
"""

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

# Pengaturan encoding konsol Windows agar karakter UTF-8 / emoji aman
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("evaluasi_ragas")


# ---------------------------------------------------------------------------
# 1. Pipeline RAG Data Extraction
# ---------------------------------------------------------------------------


def muat_kasus_uji(target_path: str = "test_cases") -> list[dict]:
    """Memuat daftar kasus uji dari direktori atau file JSON tertentu."""
    path = Path(target_path)
    semua_kasus = []

    if path.is_file():
        with open(path, "r", encoding="utf-8") as f:
            items = json.load(f)
            for item in items:
                item["source_file"] = path.name
                semua_kasus.append(item)
    elif path.is_dir():
        for file_json in sorted(path.rglob("*.json")):
            with open(file_json, "r", encoding="utf-8") as f:
                items = json.load(f)
                for item in items:
                    item["source_file"] = file_json.name
                    semua_kasus.append(item)
    else:
        logger.error(f"Target path '{target_path}' tidak ditemukan!")
        sys.exit(1)

    return semua_kasus


def eksekusi_rag(pertanyaan: str, mode: str = "hybrid") -> tuple[str, list[str], dict]:
    """Menjalankan inferensi RAG dan mengekstrak teks jawaban & konteks yang diambil."""
    if mode.lower() == "hybrid":
        from src.hybrid_rag.rag_chain import (
            _buat_vector_store,
            _muat_bm25_cached,
            retrieval_hybrid_dengan_recency,
            tanya,
            tulis_ulang_pertanyaan_standalone,
        )

        t0 = time.time()
        hasil = tanya(pertanyaan)
        durasi = time.time() - t0

        # Ambil dokumen konteks asli hasil hybrid retrieval
        vs = _buat_vector_store()
        bm25, chunks_bm25 = _muat_bm25_cached()
        q_retrieval = tulis_ulang_pertanyaan_standalone(pertanyaan, [])
        dokumen_relevan = retrieval_hybrid_dengan_recency(
            q_retrieval,
            vector_store=vs,
            bm25=bm25,
            chunks_bm25=chunks_bm25,
        )

        konteks = [doc.page_content for doc in dokumen_relevan] if dokumen_relevan else ["Tidak ada konteks ditemukan."]
        meta_info = {
            "durasi_detik": round(durasi, 2),
            "perlu_rujukan_medis": getattr(hasil, "perlu_rujukan_medis", False),
            "jumlah_sitasi": len(getattr(hasil, "sitasi_terpakai", [])),
        }
        return hasil.jawaban_utama, konteks, meta_info

    else:
        from src.basic_rag.rag_chain import _dapatkan_retriever, tanya

        t0 = time.time()
        hasil = tanya(pertanyaan)
        durasi = time.time() - t0

        retriever = _dapatkan_retriever()
        dokumen_relevan = retriever.invoke(pertanyaan)
        konteks = [doc.page_content for doc in dokumen_relevan] if dokumen_relevan else ["Tidak ada konteks ditemukan."]

        meta_info = {
            "durasi_detik": round(durasi, 2),
            "perlu_rujukan_medis": getattr(hasil, "perlu_rujukan_medis", False),
            "jumlah_sitasi": 0,
        }
        return hasil.jawaban_utama, konteks, meta_info


# ---------------------------------------------------------------------------
# 2. Evaluator RAGAS Setup
# ---------------------------------------------------------------------------


def buat_evaluator_ragas():
    """Menyiapkan evaluator LLM & Embeddings RAGAS 100% berbasis Ollama lokal."""
    try:
        import warnings
        warnings.filterwarnings("ignore", category=DeprecationWarning)

        from langchain_ollama import ChatOllama, OllamaEmbeddings
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from src import config

        logger.info(f"Mengonfigurasi Ollama Lokal ({config.LLM_MODEL}) sebagai evaluator RAGAS...")
        evaluator_llm = LangchainLLMWrapper(
            ChatOllama(
                model=config.LLM_MODEL,
                base_url=config.OLLAMA_BASE_URL,
                temperature=0.0,
            )
        )
        evaluator_embeddings = LangchainEmbeddingsWrapper(
            OllamaEmbeddings(
                model=config.EMBEDDING_MODEL,
                base_url=config.OLLAMA_BASE_URL,
            )
        )
        return evaluator_llm, evaluator_embeddings
    except Exception as e:
        logger.warning(f"Tidak dapat membuat LLM wrapper RAGAS via Ollama: {e}. Menggunakan evaluasi fallback.")
        return None, None


def hitung_skor_heuristic_fallback(records: list[dict]) -> pd.DataFrame:
    """Fallback evaluator jika modul RAGAS mengalami error / belum terpasang."""
    df = pd.DataFrame(records)
    logger.info("Menjalankan analisis heuristik fallback...")

    faithfulness_scores = []
    relevancy_scores = []
    precision_scores = []
    recall_scores = []

    for _, row in df.iterrows():
        ans = str(row["answer"]).lower()
        q = str(row["question"]).lower()
        ctxs = " ".join([str(c) for c in row["contexts"]]).lower()
        gt = str(row["ground_truth"]).lower()

        # Faithfulness heuristic: seberapa banyak kata kunci dari jawaban ada di konteks
        words_ans = [w for w in ans.split() if len(w) > 3]
        f_score = sum(1 for w in words_ans if w in ctxs) / len(words_ans) if words_ans else 0.5
        faithfulness_scores.append(min(1.0, round(f_score, 3)))

        # Relevancy heuristic: seberapa banyak kata kunci pertanyaan ada di jawaban
        words_q = [w for w in q.split() if len(w) > 3]
        r_score = sum(1 for w in words_q if w in ans) / len(words_q) if words_q else 0.5
        relevancy_scores.append(min(1.0, round(r_score, 3)))

        # Precision heuristic: kecocokan konteks dengan ground_truth
        words_gt = [w for w in gt.split() if len(w) > 3]
        p_score = sum(1 for w in words_gt if w in ctxs) / len(words_gt) if words_gt else 0.5
        precision_scores.append(min(1.0, round(p_score, 3)))

        # Recall heuristic: kecocokan jawaban dengan ground_truth
        rc_score = sum(1 for w in words_gt if w in ans) / len(words_gt) if words_gt else 0.5
        recall_scores.append(min(1.0, round(rc_score, 3)))

    df["faithfulness"] = faithfulness_scores
    df["answer_relevancy"] = relevancy_scores
    df["context_precision"] = precision_scores
    df["context_recall"] = recall_scores
    return df


def evaluasi_dengan_ragas(dataset_records: list[dict], evaluator_llm=None, evaluator_embeddings=None) -> pd.DataFrame:
    """Evaluasi dataset menggunakan framework RAGAS."""
    try:
        import warnings
        warnings.filterwarnings("ignore", category=DeprecationWarning)

        from datasets import Dataset
        from ragas import evaluate

        try:
            from ragas.metrics.collections import (
                answer_relevancy,
                context_precision,
                context_recall,
                faithfulness,
            )
        except ImportError:
            from ragas.metrics import (
                answer_relevancy,
                context_precision,
                context_recall,
                faithfulness,
            )

        dataset = Dataset.from_dict({
            "question": [r["question"] for r in dataset_records],
            "answer": [r["answer"] for r in dataset_records],
            "contexts": [r["contexts"] for r in dataset_records],
            "ground_truth": [r["ground_truth"] for r in dataset_records],
        })

        metrics = [faithfulness, answer_relevancy, context_precision, context_recall]

        # Pasang evaluator custom jika tersedia
        if evaluator_llm and evaluator_embeddings:
            for m in metrics:
                m.llm = evaluator_llm
                if hasattr(m, "embeddings"):
                    m.embeddings = evaluator_embeddings

        logger.info("Memulai perhitungan metrik RAGAS...")
        hasil_eval = evaluate(
            dataset=dataset,
            metrics=metrics,
            llm=evaluator_llm,
            embeddings=evaluator_embeddings,
        )

        df_res = hasil_eval.to_pandas()
        # Gabungkan metadata id & kategori
        for col in ["id", "category", "type", "durasi_detik"]:
            if col in dataset_records[0]:
                df_res[col] = [r[col] for r in dataset_records]

        return df_res

    except Exception as e:
        logger.warning(f"RAGAS evaluation terganggu/gagal: {e}. Menggunakan evaluasi heuristic fallback.")
        return hitung_skor_heuristic_fallback(dataset_records)


# ---------------------------------------------------------------------------
# 3. Main Runner & Reporter
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Evaluasi Sistem RAG Kesehatan dengan Framework RAGAS")
    parser.add_argument(
        "--mode",
        choices=["hybrid", "basic"],
        default="hybrid",
        help="Mode pipeline RAG yang dievaluasi (default: hybrid)",
    )
    parser.add_argument(
        "--target",
        default="test_cases",
        help="Path folder test_cases atau file JSON spesifik (default: test_cases)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Membatasi jumlah pertanyaan untuk uji coba cepat (contoh: --limit 5)",
    )
    parser.add_argument(
        "--output",
        default="laporan_evaluasi_ragas.json",
        help="File output JSON hasil evaluasi (default: laporan_evaluasi_ragas.json)",
    )
    parser.add_argument(
        "--export-csv",
        default="laporan_evaluasi_ragas.csv",
        help="File output CSV hasil evaluasi (default: laporan_evaluasi_ragas.csv)",
    )
    args = parser.parse_args()

    print("=" * 80)
    print(f"🎯 MEMULAI EVALUASI RAGAS - MODE [{args.mode.upper()}]")
    print("=" * 80)

    # 1. Memuat kasus uji
    kasus_uji_list = muat_kasus_uji(args.target)
    if args.limit and args.limit > 0:
        kasus_uji_list = kasus_uji_list[: args.limit]

    total_kasus = len(kasus_uji_list)
    print(f"📂 Total Kasus Uji Dimuat: {total_kasus} dari target '{args.target}'")
    print("=" * 80)

    # 2. Menjalankan inferensi RAG & mengumpulkan dataset
    dataset_records = []
    for idx, tc in enumerate(kasus_uji_list, 1):
        tc_id = tc.get("id", f"TC-{idx:03d}")
        q = tc.get("question", "")
        kat = tc.get("category", tc.get("type", "general"))
        gt = tc.get("ground_truth") or tc.get("reference")

        # Jika ground_truth tidak tersedia secara eksplisit, susun dari expected_keywords
        if not gt:
            kws = tc.get("expected_keywords", [])
            if kws:
                gt = f"Jawaban yang benar harus menjelaskan tentang: {', '.join(kws)}."
            else:
                gt = tc.get("description", q)

        print(f"[{idx}/{total_kasus}] [{tc_id}] Memproses: \"{q[:60]}...\"")
        try:
            answer, contexts, meta = eksekusi_rag(q, mode=args.mode)
            dataset_records.append({
                "id": tc_id,
                "category": kat,
                "type": tc.get("type", "happy_path"),
                "question": q,
                "answer": answer,
                "contexts": contexts,
                "ground_truth": gt,
                "durasi_detik": meta.get("durasi_detik", 0),
            })
            print(f"   ✓ Inferensi selesai ({meta.get('durasi_detik')}s | {len(contexts)} chunk konteks)")
        except Exception as err:
            logger.error(f"   ❌ Gagal inferensi pada {tc_id}: {err}")
            dataset_records.append({
                "id": tc_id,
                "category": kat,
                "type": tc.get("type", "happy_path"),
                "question": q,
                "answer": f"Error: {err}",
                "contexts": ["Tidak ada konteks"],
                "ground_truth": gt,
                "durasi_detik": 0,
            })

    # 3. Menyiapkan Evaluator RAGAS
    evaluator_llm, evaluator_embeddings = buat_evaluator_ragas()

    # 4. Melakukan evaluasi RAGAS
    df_eval = evaluasi_dengan_ragas(
        dataset_records=dataset_records,
        evaluator_llm=evaluator_llm,
        evaluator_embeddings=evaluator_embeddings,
    )

    # 5. Menampilkan Ringkasan Hasil
    print("\n" + "=" * 80)
    print(f"📊 RINGKASAN SKOR EVALUASI RAGAS - MODE [{args.mode.upper()}]")
    print("=" * 80)

    metric_cols = [c for c in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"] if c in df_eval.columns]

    print(f"{'Metrik Evaluation':<25} | {'Skor Rata-Rata':<15}")
    print("-" * 45)
    mean_scores = {}
    for m in metric_cols:
        val = df_eval[m].mean()
        mean_scores[m] = round(val, 4) if pd.notna(val) else 0.0
        print(f"{m:<25} | {mean_scores[m]:<15.4f}")

    print("=" * 80)

    # 6. Menyimpan Laporan
    # CSV
    df_eval.to_csv(args.export_csv, index=False, encoding="utf-8-sig")
    print(f"💾 Laporan Rinci CSV disimpan ke: {args.export_csv}")

    # JSON
    laporan_final = {
        "mode": args.mode,
        "total_kasus": total_kasus,
        "skor_rata_rata": mean_scores,
        "rincian": df_eval.to_dict(orient="records"),
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(laporan_final, f, ensure_ascii=False, indent=2)
    print(f"💾 Laporan Rinci JSON disimpan ke: {args.output}")
    print("=" * 80)
    print("✨ Evaluasi RAGAS Selesai!")


if __name__ == "__main__":
    main()

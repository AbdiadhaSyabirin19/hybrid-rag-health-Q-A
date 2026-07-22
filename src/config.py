import os
from dotenv import load_dotenv
 
load_dotenv()
 
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "artikel_kesehatan")
 
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:8b")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
 
CHUNK_SIZE = 500
CHUNK_OVERLAP = 80
TOP_K_RETRIEVAL = 8   # jumlah chunk yang diambil per pertanyaan
 
# Thinking mode Qwen3: default mati agar jawaban cepat & konsisten,
# akan dinyalakan otomatis untuk pertanyaan multi-hop (lihat rag_chain.py)
THINKING_MODE_DEFAULT = False

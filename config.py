from pathlib import Path

from dotenv import load_dotenv
import os

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data" / "users"
DATA_DIR.mkdir(parents=True, exist_ok=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini").strip()

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "paraphrase-multilingual-MiniLM-L12-v2",
)

MAX_FILE_BYTES = 20 * 1024 * 1024
CHUNK_SIZE = 700
CHUNK_OVERLAP = 120
TOP_K = 6
TELEGRAM_MAX_LEN = 4000
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_SUFFIXES = {
    ".docx",
    ".pdf",
    ".xlsx",
    ".pptx",
    ".txt",
    ".md",
    ".csv",
}

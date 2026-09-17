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

# Billing
BILLING_DB_PATH = Path(
    os.getenv("BILLING_DB_PATH", str(ROOT / "data" / "billing.sqlite3"))
)
BILLING_HTTP_HOST = os.getenv("BILLING_HTTP_HOST", "0.0.0.0").strip()
BILLING_HTTP_PORT = int(os.getenv("BILLING_HTTP_PORT", "8088"))
BILLING_PUBLIC_BASE_URL = os.getenv("BILLING_PUBLIC_BASE_URL", "").strip().rstrip("/")

UNITPAY_PROJECT_ID = os.getenv("UNITPAY_PROJECT_ID", "").strip()
UNITPAY_SECRET_KEY = os.getenv("UNITPAY_SECRET_KEY", "").strip()
UNITPAY_PUBLIC_KEY = os.getenv("UNITPAY_PUBLIC_KEY", "").strip()
UNITPAY_SKIP_IP_CHECK = os.getenv("UNITPAY_SKIP_IP_CHECK", "").strip() in {
    "1",
    "true",
    "yes",
}

# Optional admin telegram ids for test grants: "123,456"
_ADMIN_RAW = os.getenv("BILLING_ADMIN_IDS", "").strip()
BILLING_ADMIN_IDS = {
    int(x) for x in _ADMIN_RAW.replace(" ", "").split(",") if x.isdigit()
}

STARS_SUBSCRIPTION_PERIOD = 30 * 24 * 60 * 60  # 2592000

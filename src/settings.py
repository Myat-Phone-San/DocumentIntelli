import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
CHUNKS_DIR = DATA_DIR / "chunks"
CHROMA_DIR = DATA_DIR / "chroma"

load_dotenv(ROOT_DIR / ".env", override=False)

SUPPORTED_BANKS = ["KBZ", "AYA", "CB", "YOMA", "A", "UAB", "MAB"]

SUPPORTED_EXTENSIONS = {
    ".docx": "docx",
    ".pdf": "pdf",
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".txt": "txt",
}

DEFAULT_PROVIDER_PRIORITY = ["groq", "openai", "gemini"]
SUPPORTED_PROVIDERS = DEFAULT_PROVIDER_PRIORITY.copy()

LLM_PROVIDER = os.getenv("LLM_PROVIDER", ",".join(DEFAULT_PROVIDER_PRIORITY)).strip().lower()

if LLM_PROVIDER == "none":
    LLM_PROVIDER_PRIORITY = []
else:
    provider_order = []
    for provider in [item.strip().lower() for item in LLM_PROVIDER.split(",") if item.strip()]:
        if provider in SUPPORTED_PROVIDERS and provider not in provider_order:
            provider_order.append(provider)

    if not provider_order:
        provider_order = DEFAULT_PROVIDER_PRIORITY.copy()
    else:
        for provider in DEFAULT_PROVIDER_PRIORITY:
            if provider not in provider_order:
                provider_order.append(provider)

    LLM_PROVIDER_PRIORITY = provider_order

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano").strip()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b").strip()


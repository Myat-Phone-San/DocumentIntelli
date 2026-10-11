from __future__ import annotations

import json
from pathlib import Path

from src.settings import CHUNKS_DIR, PROCESSED_DIR, RAW_DIR


def save_processed_documents(documents: list[dict]) -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    output_file = PROCESSED_DIR / "processed_documents.json"
    output_file.write_text(json.dumps(documents, ensure_ascii=False, indent=2), encoding="utf-8")


def save_extracted_text_files(documents: list[dict]) -> None:
    """Save one readable UTF-8 text file for every extracted source document."""
    output_root = PROCESSED_DIR / "extracted_text"
    for document in documents:
        source_path = Path(document["path"])
        try:
            relative_path = source_path.relative_to(RAW_DIR)
        except ValueError:
            relative_path = Path(document["source"])

        # Keep the original extension in the name so equally named DOCX/PDF files
        # cannot overwrite each other (for example, catalog.docx.txt).
        output_path = output_root / relative_path.with_suffix(relative_path.suffix + ".txt")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(document["text"], encoding="utf-8")


def save_chunks(chunks: list[dict]) -> None:
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    output_file = CHUNKS_DIR / "chunks.json"
    output_file.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")


def load_processed_documents_from_disk() -> list[dict]:
    output_file = PROCESSED_DIR / "processed_documents.json"
    if not output_file.exists():
        return []
    return json.loads(output_file.read_text(encoding="utf-8"))


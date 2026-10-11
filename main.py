from src.pipeline.chunking import chunk_documents
from src.pipeline.ingestion import load_processed_documents
from src.pipeline.parser import save_chunks, save_extracted_text_files, save_processed_documents
from src.pipeline.retrieval import ChromaIndex


def main() -> None:
    documents = load_processed_documents()
    save_processed_documents(documents)
    save_extracted_text_files(documents)

    chunks = chunk_documents(documents)
    save_chunks(chunks)

    index = ChromaIndex()
    index.rebuild(chunks)
    print(f"Indexed {len(chunks)} chunks from {len(documents)} source documents.")


if __name__ == "__main__":
    main()

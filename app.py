import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

import streamlit as st

from src.pipeline.chunking import chunk_documents
from src.pipeline.ingestion import process_file
from src.pipeline.rag_chain import BankingRAG
from src.pipeline.retrieval import ChromaIndex


def build_uploaded_rag(files):
    documents = []
    errors = []
    with TemporaryDirectory() as directory:
        for position, uploaded in enumerate(files):
            filename = uploaded.name.replace("\\", "/").rsplit("/", 1)[-1]
            path = Path(directory) / str(position) / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(uploaded.getvalue())
            try:
                document = process_file(path)
                if not document["text"].strip():
                    raise ValueError("No readable text found. Scanned PDFs require OCR.")
                document.update(source=filename, path=f"uploads/{position}/{filename}",
                                bank="Uploaded", category="Document", language="auto")
                documents.append(document)
            except Exception as exc:
                errors.append(f"{filename}: {exc}")
    chunks = chunk_documents(documents)
    if not chunks:
        return None, errors, []
    index = ChromaIndex(persist_directory=None)
    try:
        index.rebuild(chunks)
    except Exception:
        index.client.delete_collection(name=index.collection_name)
        raise
    return BankingRAG(index, uploaded_documents=True), errors, [doc["source"] for doc in documents]


def clear_documents():
    rag = st.session_state.pop("document_rag", None)
    if rag is not None:
        rag.index.client.delete_collection(name=rag.index.collection_name)
    for key in ("document_answer", "document_signature", "document_names", "document_errors"):
        st.session_state.pop(key, None)


st.set_page_config(page_title="Document Q&A Assistant", layout="wide")
st.title("Document Q&A Assistant")
st.caption("Upload documents on any topic and ask questions based on their contents.")
files = st.file_uploader("Upload documents", type=["pdf", "docx", "xlsx", "txt", "md", "csv"],
                         accept_multiple_files=True)
if not files:
    clear_documents()
    st.info("Upload one or more documents to start asking questions.")
    st.stop()

signature = tuple((file.name, hashlib.sha256(file.getvalue()).hexdigest()) for file in files)
if signature != st.session_state.get("document_signature"):
    clear_documents()
    try:
        with st.spinner("Reading and indexing your documents..."):
            rag, errors, names = build_uploaded_rag(files)
        st.session_state.update(document_rag=rag, document_signature=signature,
                                document_errors=errors, document_names=names)
    except Exception as exc:
        st.error(f"Could not index documents: {exc}")
        st.stop()

for error in st.session_state.get("document_errors", []):
    st.warning(error)
rag = st.session_state.get("document_rag")
if rag is None:
    st.error("No readable documents. Upload a supported file containing text.")
    st.stop()
st.success("Ready: " + ", ".join(st.session_state["document_names"]))
with st.form("document_question"):
    query = st.text_input("Ask about your documents", placeholder="e.g. Summarize the main points of this document")
    submitted = st.form_submit_button("Ask")
if submitted and query.strip():
    st.session_state.pop("document_answer", None)
    try:
        with st.spinner("Finding an answer..."):
            st.session_state.document_answer = rag.answer(query.strip())
    except Exception as exc:
        st.error(f"Could not answer the question: {exc}")
answer = st.session_state.get("document_answer")
if answer:
    st.subheader("Answer")
    st.markdown(answer["answer"])
    st.subheader("Retrieved sources")
    grouped = {}
    for source in answer["sources"]:
        grouped.setdefault(source["path"], []).append(source)
    for excerpts in grouped.values():
        with st.expander(excerpts[0]["source"]):
            for excerpt in excerpts:
                st.text(excerpt["text"])

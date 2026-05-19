from __future__ import annotations

import os
import re

from flask import current_app

from services.rag_embedder_service import embed_texts, get_embedding_runtime_config


def normalize_text(value):
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()


def tokenize(value):
    return [token for token in normalize_text(value).split() if token]


def get_chunking_config(*, chunk_size_key, chunk_overlap_key, default_chunk_size, default_chunk_overlap, min_chunk_size):
    raw_chunk_size = int(current_app.config.get(chunk_size_key, default_chunk_size))
    chunk_size = max(raw_chunk_size, min_chunk_size)
    raw_chunk_overlap = int(current_app.config.get(chunk_overlap_key, default_chunk_overlap))
    chunk_overlap = max(min(raw_chunk_overlap, chunk_size // 2), 0)
    return {
        'chunk_size': chunk_size,
        'chunk_overlap': chunk_overlap,
    }


def split_text_into_chunks(text, chunk_size, overlap):
    content = str(text or '').strip()
    if not content:
        return []

    chunks = []
    start = 0
    text_length = len(content)

    while start < text_length:
        end = min(start + chunk_size, text_length)
        if end < text_length:
            preferred_boundary = max(start + int(chunk_size * 0.55), start)
            boundary = content.rfind('\n\n', preferred_boundary, end)
            if boundary == -1:
                boundary = content.rfind('\n', preferred_boundary, end)
            if boundary == -1:
                boundary = content.rfind('. ', preferred_boundary, end)
                if boundary != -1:
                    boundary += 1
            if boundary > start:
                end = boundary

        chunk = content[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= text_length:
            break
        start = max(end - overlap, start + 1)

    return chunks


def _require_chromadb():
    try:
        import chromadb
    except ImportError as exc:
        raise RuntimeError(
            'chromadb is not installed in the active Python environment. '
            'Install it with ./venv/bin/pip install chromadb.'
        ) from exc
    return chromadb


def get_chroma_runtime_config(*, collection_name_key, error_label):
    persist_dir = str(current_app.config.get('CHROMA_PERSIST_DIR') or '').strip()
    collection_name = str(current_app.config.get(collection_name_key) or '').strip()
    embedding_config = get_embedding_runtime_config()

    if not persist_dir or not collection_name:
        raise RuntimeError(f'{error_label} configuration is incomplete.')

    return {
        'persist_dir': persist_dir,
        'collection_name': collection_name,
        'embedding_model': embedding_config['model_name'],
    }


def get_chroma_collection(*, collection_name_key, default_collection_metadata, error_label, rebuild=False):
    chromadb = _require_chromadb()
    config = get_chroma_runtime_config(
        collection_name_key=collection_name_key,
        error_label=error_label,
    )
    os.makedirs(config['persist_dir'], exist_ok=True)
    client = chromadb.PersistentClient(path=config['persist_dir'])
    collection_name = config['collection_name']

    if rebuild:
        try:
            client.delete_collection(name=collection_name)
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={
            **default_collection_metadata,
            'embedding_model': config['embedding_model'],
        },
    )
    return client, collection, config


def build_collection_status(*, collection_name_key, default_collection_metadata, error_label, document_model, chunk_model):
    try:
        config = get_chroma_runtime_config(
            collection_name_key=collection_name_key,
            error_label=error_label,
        )
    except RuntimeError as exc:
        return {
            'chromadb_installed': False,
            'error': str(exc),
            'document_rows': document_model.query.count(),
            'stored_chunk_rows': chunk_model.query.count(),
            'chunk_rows': 0,
        }

    status = {
        'persist_dir': config['persist_dir'],
        'collection_name': config['collection_name'],
        'embedding_model': config['embedding_model'],
        'document_rows': document_model.query.count(),
        'stored_chunk_rows': chunk_model.query.count(),
        'chromadb_installed': False,
        'chunk_rows': 0,
    }

    try:
        _, collection, _ = get_chroma_collection(
            collection_name_key=collection_name_key,
            default_collection_metadata=default_collection_metadata,
            error_label=error_label,
        )
        status['chromadb_installed'] = True
        status['chunk_rows'] = int(collection.count())
    except RuntimeError as exc:
        status['error'] = str(exc)

    return status


def delete_by_where(collection, where):
    if not where:
        return

    try:
        collection.delete(where=where)
    except Exception:
        pass


def replace_documents(collection, chunk_records, *, source_document_ids=None, rebuild=False):
    if not chunk_records:
        return 0

    if not rebuild:
        for source_document_id in sorted({str(item) for item in (source_document_ids or []) if item is not None}):
            delete_by_where(collection, {'source_document_id': source_document_id})

    embeddings = embed_texts([item['document'] for item in chunk_records])
    collection.upsert(
        ids=[item['id'] for item in chunk_records],
        documents=[item['document'] for item in chunk_records],
        metadatas=[item['metadata'] for item in chunk_records],
        embeddings=embeddings,
    )
    return len(chunk_records)


def _normalize_query_results(results):
    matches = []
    documents = (results.get('documents') or [[]])[0]
    metadatas = (results.get('metadatas') or [[]])[0]
    distances = (results.get('distances') or [[]])[0]
    for index, document in enumerate(documents):
        matches.append({
            'document': document,
            'metadata': metadatas[index] if index < len(metadatas) else {},
            'distance': distances[index] if index < len(distances) else None,
        })
    return matches


def query_collection(*, collection_name_key, default_collection_metadata, error_label, query_text, limit, where=None):
    text = str(query_text or '').strip()
    if not text:
        raise ValueError('query_text is required.')

    _, collection, _ = get_chroma_collection(
        collection_name_key=collection_name_key,
        default_collection_metadata=default_collection_metadata,
        error_label=error_label,
    )
    query_embedding = embed_texts([text])[0]
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=max(int(limit or 1), 1),
        where=where,
        include=['documents', 'metadatas', 'distances'],
    )
    return _normalize_query_results(results)

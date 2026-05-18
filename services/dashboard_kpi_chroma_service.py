from __future__ import annotations

import os
import re

import requests
from flask import current_app

from dashboard_kpi_documents_models import (
    DashboardKpiDocument,
    DashboardKpiDocumentChunk,
)
from services.dashboard_kpi_documents_service import sync_dashboard_kpi_documents


DEFAULT_COLLECTION_METADATA = {
    'domain': 'dashboard_kpi_rag',
    'source_table': 'dashboard_kpi_document_chunks',
}


def _normalize_text(value):
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()


def _tokenize(value):
    return [token for token in _normalize_text(value).split() if token]


def _extract_error_message(response):
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or f'Ollama returned HTTP {response.status_code}.'

    if isinstance(payload, dict):
        error = (payload.get('error') or '').strip()
        if error:
            return error

    return f'Ollama returned HTTP {response.status_code}.'


def _get_chroma_runtime_config():
    base_url = (current_app.config.get('OLLAMA_BASE_URL') or '').rstrip('/')
    embed_endpoint = current_app.config.get('OLLAMA_EMBED_ENDPOINT')
    embed_model = current_app.config.get('OLLAMA_EMBED_MODEL')
    timeout = current_app.config.get('OLLAMA_TIMEOUT')
    persist_dir = current_app.config.get('CHROMA_PERSIST_DIR')
    collection_name = current_app.config.get('DASHBOARD_KPI_CHROMA_COLLECTION')

    if (
        not base_url
        or not embed_endpoint
        or not embed_model
        or timeout is None
        or not persist_dir
        or not collection_name
    ):
        raise RuntimeError('Dashboard KPI Chroma/Ollama embedding configuration is incomplete.')

    return {
        'base_url': base_url,
        'embed_endpoint': str(embed_endpoint),
        'embed_model': str(embed_model),
        'timeout': int(timeout),
        'persist_dir': str(persist_dir),
        'collection_name': str(collection_name),
    }


def _require_chromadb():
    try:
        import chromadb
    except ImportError as exc:
        raise RuntimeError(
            'chromadb is not installed in the active Python environment. '
            'Install it with ./venv/bin/pip install chromadb.'
        ) from exc
    return chromadb


def _get_chroma_collection(*, rebuild=False):
    chromadb = _require_chromadb()
    config = _get_chroma_runtime_config()
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
            **DEFAULT_COLLECTION_METADATA,
            'embedding_model': config['embed_model'],
        },
    )
    return client, collection, config


def _raise_connection_error(exc, url, model):
    exc_name = type(exc).__name__
    detail = str(exc).strip() or 'No additional error details were provided.'
    raise RuntimeError(
        f'Could not reach Ollama at {url} for embedding model "{model}". '
        f'{exc_name}: {detail}'
    ) from exc


def _embed_texts(texts, config):
    items = [str(item or '').strip() for item in texts if str(item or '').strip()]
    if not items:
        return []

    embed_url = f"{config['base_url']}{config['embed_endpoint']}"

    try:
        response = requests.post(
            embed_url,
            json={
                'model': config['embed_model'],
                'input': items,
            },
            timeout=config['timeout'],
        )
    except requests.RequestException as exc:
        _raise_connection_error(exc, embed_url, config['embed_model'])

    if not response.ok:
        raise RuntimeError(f'Ollama embed request failed: {_extract_error_message(response)}')

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError('Ollama returned an invalid JSON response for embeddings.') from exc

    embeddings = payload.get('embeddings')
    if not isinstance(embeddings, list) or len(embeddings) != len(items):
        raise RuntimeError('Ollama returned an unexpected embeddings payload.')
    return embeddings


def _chunk_query(document_key=None, dashboard_page=None):
    query = (
        DashboardKpiDocumentChunk.query
        .order_by(
            DashboardKpiDocumentChunk.dashboard_page.asc(),
            DashboardKpiDocumentChunk.document_key.asc(),
            DashboardKpiDocumentChunk.chunk_index.asc(),
        )
    )

    if document_key:
        query = query.filter(DashboardKpiDocumentChunk.document_key == str(document_key).strip())
    if dashboard_page:
        query = query.filter(DashboardKpiDocumentChunk.dashboard_page == str(dashboard_page).strip())
    return query


def _chunk_metadata(row):
    summary = row.summary or {}
    return {
        'source_chunk_id': str(row.id),
        'source_document_id': str(row.document_id),
        'pipeline_name': row.pipeline_name or '',
        'pipeline_job_path': row.pipeline_job_path or '',
        'branch_name': row.branch_name or '',
        'document_key': row.document_key or '',
        'dashboard_page': row.dashboard_page or '',
        'chunk_index': int(summary.get('chunk_index') or row.chunk_index or 0),
        'chunk_count': int(summary.get('chunk_count') or row.chunk_count or 0),
        'tag_csv': str(summary.get('tag_csv') or ''),
        'alias_csv': str(summary.get('alias_csv') or ''),
        'value_mode': str(summary.get('value_mode') or 'definition_only'),
        'time_window': str(summary.get('time_window') or ''),
        'aggregation': str(summary.get('aggregation') or ''),
    }


def _chunk_document_text(row):
    summary = row.summary or {}
    lines = [
        'Dashboard KPI explanation',
        f"Value mode: {str(summary.get('value_mode') or 'definition_only')}",
        f"Title: {row.title or ''}",
        f"Dashboard page: {row.dashboard_page or ''}",
        f"Document key: {row.document_key or ''}",
    ]

    time_window = str(summary.get('time_window') or '').strip()
    if time_window:
        lines.append(f'Time window: {time_window}')

    aggregation = str(summary.get('aggregation') or '').strip()
    if aggregation:
        lines.append(f'Aggregation: {aggregation}')

    tag_csv = str(summary.get('tag_csv') or '').strip()
    if tag_csv:
        lines.append(f'Tags: {tag_csv}')

    alias_csv = str(summary.get('alias_csv') or '').strip()
    if alias_csv:
        lines.append(f'Aliases: {alias_csv}')

    lines.extend([
        '',
        'Chunk content:',
        str(row.content or '').strip(),
    ])
    return '\n'.join(line for line in lines if line is not None).strip()


def _record_id(row):
    return f'dashboard-kpi-doc-{row.document_id}-chunk-{row.chunk_index}'


def _chunk_record(row):
    return {
        'id': _record_id(row),
        'document': _chunk_document_text(row),
        'metadata': _chunk_metadata(row),
    }


def _keyword_score(query_text, match):
    normalized_query = _normalize_text(query_text)
    query_tokens = set(_tokenize(query_text))
    if not normalized_query or not query_tokens:
        return 0

    metadata = match.get('metadata') or {}
    document = str(match.get('document') or '').strip().lower()
    fields = {
        'document_key': str(metadata.get('document_key') or '').replace('.', ' ').replace('_', ' ').lower(),
        'dashboard_page': str(metadata.get('dashboard_page') or '').strip().lower(),
        'tags': str(metadata.get('tag_csv') or '').strip().lower(),
        'aliases': str(metadata.get('alias_csv') or '').strip().lower(),
        'document': document,
    }

    score = 0
    for field_name in ('document_key', 'tags', 'aliases'):
        text = fields[field_name]
        if text and text in normalized_query:
            score += 10

    if fields['dashboard_page'] and fields['dashboard_page'] in normalized_query:
        score += 3

    title_match = re.search(r'^title:\s*(.+)$', document, flags=re.MULTILINE)
    title_text = title_match.group(1).strip().lower() if title_match else ''
    if title_text and title_text in normalized_query:
        score += 12

    for token in query_tokens:
        if token in _tokenize(fields['document_key']):
            score += 3
        if token in _tokenize(fields['tags']):
            score += 2
        if token in _tokenize(fields['aliases']):
            score += 2
        if token in _tokenize(title_text):
            score += 3
        if token in _tokenize(document):
            score += 1

    return score


def get_dashboard_kpi_chroma_status():
    try:
        config = _get_chroma_runtime_config()
    except RuntimeError as exc:
        return {
            'chromadb_installed': False,
            'error': str(exc),
            'document_rows': DashboardKpiDocument.query.count(),
            'stored_chunk_rows': DashboardKpiDocumentChunk.query.count(),
            'chunk_rows': 0,
        }

    status = {
        'persist_dir': config['persist_dir'],
        'collection_name': config['collection_name'],
        'embedding_model': config['embed_model'],
        'document_rows': DashboardKpiDocument.query.count(),
        'stored_chunk_rows': DashboardKpiDocumentChunk.query.count(),
        'chromadb_installed': False,
        'chunk_rows': 0,
    }

    try:
        _, collection, _ = _get_chroma_collection()
        status['chromadb_installed'] = True
        status['chunk_rows'] = int(collection.count())
    except RuntimeError as exc:
        status['error'] = str(exc)

    return status


def sync_dashboard_kpi_documents_to_chroma(*, document_key=None, dashboard_page=None, rebuild=False, auto_generate=False):
    if auto_generate:
        sync_dashboard_kpi_documents()

    _, collection, config = _get_chroma_collection(rebuild=rebuild)
    rows = _chunk_query(document_key=document_key, dashboard_page=dashboard_page).all()

    if not rows:
        if not rebuild and document_key:
            try:
                collection.delete(where={'document_key': str(document_key).strip()})
            except Exception:
                pass
        return {
            'collection_name': config['collection_name'],
            'persist_dir': config['persist_dir'],
            'embedding_model': config['embed_model'],
            'documents_indexed': 0,
            'chunks_upserted': 0,
            'document_keys': [],
            'rebuild': bool(rebuild),
            'collection_count': int(collection.count()),
        }

    document_ids = sorted({row.document_id for row in rows if row.document_id is not None})
    document_keys = sorted({row.document_key for row in rows if row.document_key})
    chunk_records = [_chunk_record(row) for row in rows if str(row.content or '').strip()]

    if not chunk_records:
        return {
            'collection_name': config['collection_name'],
            'persist_dir': config['persist_dir'],
            'embedding_model': config['embed_model'],
            'documents_indexed': len(document_ids),
            'chunks_upserted': 0,
            'document_keys': document_keys,
            'rebuild': bool(rebuild),
            'collection_count': int(collection.count()),
        }

    if not rebuild:
        for source_document_id in document_ids:
            try:
                collection.delete(where={'source_document_id': str(source_document_id)})
            except Exception:
                pass

    embeddings = _embed_texts(
        [item['document'] for item in chunk_records],
        config,
    )
    collection.upsert(
        ids=[item['id'] for item in chunk_records],
        documents=[item['document'] for item in chunk_records],
        metadatas=[item['metadata'] for item in chunk_records],
        embeddings=embeddings,
    )

    return {
        'collection_name': config['collection_name'],
        'persist_dir': config['persist_dir'],
        'embedding_model': config['embed_model'],
        'documents_indexed': len(document_ids),
        'chunks_upserted': len(chunk_records),
        'document_keys': document_keys,
        'rebuild': bool(rebuild),
        'collection_count': int(collection.count()),
    }


def query_dashboard_kpi_chroma(query_text, *, limit=4, dashboard_page=None):
    text = str(query_text or '').strip()
    if not text:
        raise ValueError('query_text is required.')

    _, collection, config = _get_chroma_collection()
    query_embedding = _embed_texts([text], config)[0]
    requested_limit = max(int(limit or 4), 1)

    where = None
    if dashboard_page:
        where = {'dashboard_page': str(dashboard_page).strip()}

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=max(requested_limit * 4, requested_limit),
        where=where,
        include=['documents', 'metadatas', 'distances'],
    )

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

    matches.sort(
        key=lambda item: (
            -_keyword_score(text, item),
            item.get('distance') if item.get('distance') is not None else float('inf'),
        )
    )
    return matches[:requested_limit]

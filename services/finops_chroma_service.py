from __future__ import annotations

import os
from datetime import date

import requests
from flask import current_app

from finops_models import FinOpsBuildDocument, FinOpsBuildDocumentChunk


DEFAULT_COLLECTION_METADATA = {
    'domain': 'finops_rag',
    'source_table': 'finops_builds_document_chunks',
}


def _normalize_date_input(value):
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


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
    collection_name = current_app.config.get('FINOPS_CHROMA_COLLECTION')

    if (
        not base_url
        or not embed_endpoint
        or not embed_model
        or timeout is None
        or not persist_dir
        or not collection_name
    ):
        raise RuntimeError('Chroma/Ollama embedding configuration is incomplete.')

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


def _chunk_query(target_date=None, start_date=None, end_date=None):
    query = (
        FinOpsBuildDocumentChunk.query
        .order_by(
            FinOpsBuildDocumentChunk.usage_date.asc(),
            FinOpsBuildDocumentChunk.document_id.asc(),
            FinOpsBuildDocumentChunk.chunk_index.asc(),
        )
    )

    if target_date is not None:
        return query.filter(FinOpsBuildDocumentChunk.usage_date == target_date)

    if start_date is not None:
        query = query.filter(FinOpsBuildDocumentChunk.usage_date >= start_date)
    if end_date is not None:
        query = query.filter(FinOpsBuildDocumentChunk.usage_date <= end_date)
    return query


def _chunk_metadata(row):
    summary = row.summary or {}

    return {
        'source_chunk_id': str(row.id),
        'source_document_id': str(row.document_id),
        'usage_date': row.usage_date.isoformat() if row.usage_date else '',
        'pipeline_name': row.pipeline_name or '',
        'pipeline_job_path': row.pipeline_job_path or '',
        'currency_code': row.currency_code or 'USD',
        'chunk_index': int(summary.get('chunk_index') or row.chunk_index or 0),
        'chunk_count': int(summary.get('chunk_count') or row.chunk_count or 0),
        'tag_csv': str(summary.get('tag_csv') or ''),
        'likely_driver': str(summary.get('likely_driver') or ''),
        'cost_spike': bool(summary.get('cost_spike')),
        'high_build_activity': bool(summary.get('high_build_activity')),
        'long_build_activity': bool(summary.get('long_build_activity')),
        'build_pressure': bool(summary.get('build_pressure')),
        'failure_pressure': bool(summary.get('failure_pressure')),
        'build_count': int(summary.get('build_count') or 0),
        'success_count': int(summary.get('success_count') or 0),
        'failure_count': int(summary.get('failure_count') or 0),
        'aborted_count': int(summary.get('aborted_count') or 0),
        'running_count': int(summary.get('running_count') or 0),
        'total_duration_ms': int(summary.get('total_duration_ms') or 0),
        'avg_duration_ms': int(summary.get('avg_duration_ms') or 0),
        'total_cost': float(summary.get('total_cost') or 0.0),
        'month_average_total_cost': float(summary.get('month_average_total_cost') or 0.0),
    }


def _record_id(row):
    return f'finops-doc-{row.document_id}-chunk-{row.chunk_index}'


def _chunk_record(row):
    return {
        'id': _record_id(row),
        'document': str(row.content or '').strip(),
        'metadata': _chunk_metadata(row),
    }


def get_finops_chroma_status():
    try:
        config = _get_chroma_runtime_config()
    except RuntimeError as exc:
        return {
            'chromadb_installed': False,
            'error': str(exc),
            'document_rows': FinOpsBuildDocument.query.count(),
            'stored_chunk_rows': FinOpsBuildDocumentChunk.query.count(),
            'chunk_rows': 0,
        }

    status = {
        'persist_dir': config['persist_dir'],
        'collection_name': config['collection_name'],
        'embedding_model': config['embed_model'],
        'document_rows': FinOpsBuildDocument.query.count(),
        'stored_chunk_rows': FinOpsBuildDocumentChunk.query.count(),
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


def sync_finops_documents_to_chroma(*, target_date=None, start_date=None, end_date=None, rebuild=False):
    target_date = _normalize_date_input(target_date)
    start_date = _normalize_date_input(start_date)
    end_date = _normalize_date_input(end_date)

    if target_date is not None:
        start_date = target_date
        end_date = target_date
    if start_date is not None and end_date is None:
        end_date = start_date
    if end_date is not None and start_date is None:
        start_date = end_date
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError('start_date must be before or equal to end_date.')

    _, collection, config = _get_chroma_collection(rebuild=rebuild)
    rows = _chunk_query(target_date=target_date, start_date=start_date, end_date=end_date).all()

    if not rows:
        if not rebuild and target_date is not None:
            try:
                collection.delete(where={'usage_date': target_date.isoformat()})
            except Exception:
                pass
        return {
            'collection_name': config['collection_name'],
            'persist_dir': config['persist_dir'],
            'embedding_model': config['embed_model'],
            'documents_indexed': 0,
            'chunks_upserted': 0,
            'dates': [],
            'rebuild': bool(rebuild),
            'collection_count': int(collection.count()),
        }

    document_ids = sorted({row.document_id for row in rows if row.document_id is not None})
    chunk_records = [_chunk_record(row) for row in rows if str(row.content or '').strip()]

    if not chunk_records:
        return {
            'collection_name': config['collection_name'],
            'persist_dir': config['persist_dir'],
            'embedding_model': config['embed_model'],
            'documents_indexed': len(document_ids),
            'chunks_upserted': 0,
            'dates': sorted({row.usage_date.isoformat() for row in rows if row.usage_date is not None}),
            'rebuild': bool(rebuild),
            'collection_count': int(collection.count()),
        }

    if not rebuild:
        for document_id in document_ids:
            try:
                collection.delete(where={'source_document_id': str(document_id)})
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
        'dates': sorted({row.usage_date.isoformat() for row in rows if row.usage_date is not None}),
        'rebuild': bool(rebuild),
        'collection_count': int(collection.count()),
    }


def query_finops_chroma(query_text, *, limit=5, usage_date=None):
    text = str(query_text or '').strip()
    if not text:
        raise ValueError('query_text is required.')

    usage_date = _normalize_date_input(usage_date)
    _, collection, config = _get_chroma_collection()
    query_embedding = _embed_texts([text], config)[0]

    where = None
    if usage_date is not None:
        where = {'usage_date': usage_date.isoformat()}

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=max(int(limit or 5), 1),
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
    return matches

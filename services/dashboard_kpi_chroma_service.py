from __future__ import annotations

import re

from dashboard_kpi_documents_models import (
    DashboardKpiDocument,
    DashboardKpiDocumentChunk,
)
from services.dashboard_kpi_documents_service import sync_dashboard_kpi_documents
from services.rag_base_service import (
    build_collection_status,
    delete_by_where,
    get_chroma_collection,
    normalize_text as _normalize_text,
    query_collection,
    replace_documents,
    tokenize as _tokenize,
)


DEFAULT_COLLECTION_METADATA = {
    'domain': 'dashboard_kpi_rag',
    'source_table': 'dashboard_kpi_document_chunks',
}
COLLECTION_NAME_KEY = 'DASHBOARD_KPI_CHROMA_COLLECTION'
COLLECTION_ERROR_LABEL = 'Dashboard KPI Chroma'


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
    return build_collection_status(
        collection_name_key=COLLECTION_NAME_KEY,
        default_collection_metadata=DEFAULT_COLLECTION_METADATA,
        error_label=COLLECTION_ERROR_LABEL,
        document_model=DashboardKpiDocument,
        chunk_model=DashboardKpiDocumentChunk,
    )


def sync_dashboard_kpi_documents_to_chroma(*, document_key=None, dashboard_page=None, rebuild=False, auto_generate=False):
    if auto_generate:
        sync_dashboard_kpi_documents()

    _, collection, config = get_chroma_collection(
        collection_name_key=COLLECTION_NAME_KEY,
        default_collection_metadata=DEFAULT_COLLECTION_METADATA,
        error_label=COLLECTION_ERROR_LABEL,
        rebuild=rebuild,
    )
    rows = _chunk_query(document_key=document_key, dashboard_page=dashboard_page).all()

    if not rows:
        if not rebuild and document_key:
            delete_by_where(collection, {'document_key': str(document_key).strip()})
        return {
            'collection_name': config['collection_name'],
            'persist_dir': config['persist_dir'],
            'embedding_model': config['embedding_model'],
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
            'embedding_model': config['embedding_model'],
            'documents_indexed': len(document_ids),
            'chunks_upserted': 0,
            'document_keys': document_keys,
            'rebuild': bool(rebuild),
            'collection_count': int(collection.count()),
        }

    chunks_upserted = replace_documents(
        collection,
        chunk_records,
        source_document_ids=document_ids,
        rebuild=rebuild,
    )

    return {
        'collection_name': config['collection_name'],
        'persist_dir': config['persist_dir'],
        'embedding_model': config['embedding_model'],
        'documents_indexed': len(document_ids),
        'chunks_upserted': chunks_upserted,
        'document_keys': document_keys,
        'rebuild': bool(rebuild),
        'collection_count': int(collection.count()),
    }


def query_dashboard_kpi_chroma(query_text, *, limit=4, dashboard_page=None):
    text = str(query_text or '').strip()
    if not text:
        raise ValueError('query_text is required.')

    requested_limit = max(int(limit or 4), 1)
    where = None
    if dashboard_page:
        where = {'dashboard_page': str(dashboard_page).strip()}

    matches = query_collection(
        collection_name_key=COLLECTION_NAME_KEY,
        default_collection_metadata=DEFAULT_COLLECTION_METADATA,
        error_label=COLLECTION_ERROR_LABEL,
        query_text=text,
        limit=max(requested_limit * 4, requested_limit),
        where=where,
    )
    matches.sort(
        key=lambda item: (
            -_keyword_score(text, item),
            item.get('distance') if item.get('distance') is not None else float('inf'),
        )
    )
    return matches[:requested_limit]

from __future__ import annotations

from datetime import date

from finops_models import FinOpsBuildDocument, FinOpsBuildDocumentChunk
from services.rag_base_service import (
    build_collection_status,
    delete_by_where,
    get_chroma_collection,
    query_collection,
    replace_documents,
)


DEFAULT_COLLECTION_METADATA = {
    'domain': 'finops_rag',
    'source_table': 'finops_builds_document_chunks',
}
COLLECTION_NAME_KEY = 'FINOPS_CHROMA_COLLECTION'
COLLECTION_ERROR_LABEL = 'FinOps Chroma'


def _normalize_date_input(value):
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def _build_usage_date_where(*, usage_date=None, start_date=None, end_date=None):
    usage_date = _normalize_date_input(usage_date)
    start_date = _normalize_date_input(start_date)
    end_date = _normalize_date_input(end_date)

    if usage_date is not None:
        start_date = usage_date
        end_date = usage_date
    if start_date is not None and end_date is None:
        end_date = start_date
    if end_date is not None and start_date is None:
        start_date = end_date
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError('start_date must be before or equal to end_date.')

    if start_date is None and end_date is None:
        return None
    if start_date is not None and end_date is not None and start_date == end_date:
        return {'usage_date': start_date.isoformat()}

    clauses = []
    if start_date is not None:
        clauses.append({'usage_date': {'$gte': start_date.isoformat()}})
    if end_date is not None:
        clauses.append({'usage_date': {'$lte': end_date.isoformat()}})

    if len(clauses) == 1:
        return clauses[0]
    return {'$and': clauses}


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
    return build_collection_status(
        collection_name_key=COLLECTION_NAME_KEY,
        default_collection_metadata=DEFAULT_COLLECTION_METADATA,
        error_label=COLLECTION_ERROR_LABEL,
        document_model=FinOpsBuildDocument,
        chunk_model=FinOpsBuildDocumentChunk,
    )


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

    _, collection, config = get_chroma_collection(
        collection_name_key=COLLECTION_NAME_KEY,
        default_collection_metadata=DEFAULT_COLLECTION_METADATA,
        error_label=COLLECTION_ERROR_LABEL,
        rebuild=rebuild,
    )
    rows = _chunk_query(target_date=target_date, start_date=start_date, end_date=end_date).all()

    if not rows:
        if not rebuild and target_date is not None:
            delete_by_where(collection, {'usage_date': target_date.isoformat()})
        return {
            'collection_name': config['collection_name'],
            'persist_dir': config['persist_dir'],
            'embedding_model': config['embedding_model'],
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
            'embedding_model': config['embedding_model'],
            'documents_indexed': len(document_ids),
            'chunks_upserted': 0,
            'dates': sorted({row.usage_date.isoformat() for row in rows if row.usage_date is not None}),
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
        'dates': sorted({row.usage_date.isoformat() for row in rows if row.usage_date is not None}),
        'rebuild': bool(rebuild),
        'collection_count': int(collection.count()),
    }


def query_finops_chroma(query_text, *, limit=5, usage_date=None, start_date=None, end_date=None):
    where = _build_usage_date_where(
        usage_date=usage_date,
        start_date=start_date,
        end_date=end_date,
    )
    return query_collection(
        collection_name_key=COLLECTION_NAME_KEY,
        default_collection_metadata=DEFAULT_COLLECTION_METADATA,
        error_label=COLLECTION_ERROR_LABEL,
        query_text=query_text,
        limit=max(int(limit or 5), 1),
        where=where,
    )

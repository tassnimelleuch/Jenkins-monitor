from __future__ import annotations

from functools import lru_cache

from flask import current_app


def _coerce_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def get_embedding_runtime_config():
    model_name = str(current_app.config.get('EMBEDDING_MODEL') or '').strip()
    device = str(current_app.config.get('EMBEDDING_DEVICE') or 'cpu').strip() or 'cpu'
    batch_size = int(current_app.config.get('EMBEDDING_BATCH_SIZE', 32))
    normalize_embeddings = _coerce_bool(
        current_app.config.get('EMBEDDING_NORMALIZE', True),
        default=True,
    )

    if not model_name:
        raise RuntimeError('Embedding configuration is incomplete. Set EMBEDDING_MODEL.')

    return {
        'model_name': model_name,
        'device': device,
        'batch_size': max(batch_size, 1),
        'normalize_embeddings': normalize_embeddings,
    }


def _require_sentence_transformer():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            'sentence-transformers is not installed in the active Python environment. '
            'Install it with ./venv/bin/pip install sentence-transformers.'
        ) from exc
    return SentenceTransformer


@lru_cache(maxsize=4)
def _load_model(model_name, device):
    SentenceTransformer = _require_sentence_transformer()
    try:
        return SentenceTransformer(model_name, device=device)
    except Exception as exc:
        raise RuntimeError(
            f'Failed to load embedding model "{model_name}" on device "{device}". '
            f'{type(exc).__name__}: {exc}'
        ) from exc


def embed_texts(texts):
    items = [str(item or '').strip() for item in texts if str(item or '').strip()]
    if not items:
        return []

    config = get_embedding_runtime_config()
    model = _load_model(config['model_name'], config['device'])

    try:
        vectors = model.encode(
            items,
            batch_size=config['batch_size'],
            convert_to_numpy=True,
            normalize_embeddings=config['normalize_embeddings'],
            show_progress_bar=False,
        )
    except Exception as exc:
        raise RuntimeError(
            f'Embedding generation failed for model "{config["model_name"]}". '
            f'{type(exc).__name__}: {exc}'
        ) from exc

    return vectors.tolist()

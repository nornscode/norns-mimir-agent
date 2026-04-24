import logging

from fastembed import TextEmbedding

from mimir_agent import config

logger = logging.getLogger(__name__)

_model: TextEmbedding | None = None


def _resolve_model_name(name: str) -> str:
    # fastembed expects HuggingFace-style "<org>/<model>" identifiers; accept
    # the short form (e.g. "all-MiniLM-L6-v2") for back-compat with older envs.
    return name if "/" in name else f"sentence-transformers/{name}"


def _get_model() -> TextEmbedding:
    global _model
    if _model is None:
        model_name = _resolve_model_name(config.EMBEDDING_MODEL)
        logger.info("Loading embedding model: %s", model_name)
        _model = TextEmbedding(model_name=model_name)
    return _model


def get_embedding(text: str) -> list[float]:
    """Generate an embedding vector for the given text."""
    return next(_get_model().embed([text])).tolist()


def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for a batch of texts."""
    if not texts:
        return []
    return [vec.tolist() for vec in _get_model().embed(texts)]

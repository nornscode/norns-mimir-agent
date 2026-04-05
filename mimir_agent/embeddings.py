import logging

from sentence_transformers import SentenceTransformer

from mimir_agent import config

logger = logging.getLogger(__name__)

_model = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger.info("Loading embedding model: %s", config.EMBEDDING_MODEL)
        _model = SentenceTransformer(config.EMBEDDING_MODEL)
    return _model


def get_embedding(text: str) -> list[float]:
    """Generate an embedding vector for the given text."""
    return _get_model().encode(text).tolist()


def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for a batch of texts."""
    if not texts:
        return []
    return _get_model().encode(texts).tolist()

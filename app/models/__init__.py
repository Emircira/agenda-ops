"""Karargah ORM — dışarıdan `app.models` üzerinden içe aktarım."""

from app.models.base_class import Base
from app.models.core import ContentEmbedding, EMBEDDING_DIMENSION

__all__ = ["Base", "ContentEmbedding", "EMBEDDING_DIMENSION"]

from __future__ import annotations

from typing import List, Sequence, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Content, ContentEmbedding, EMBEDDING_DIMENSION
from app.repositories.base import BaseRepository


class VectorRepository(BaseRepository):
    """content_embeddings (pgvector) üzerinde okuma/yazma ve anlamsal sorgu."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def save_embedding(
        self,
        content_id: UUID,
        embedding: Sequence[float],
        *,
        commit: bool = True,
    ) -> None:
        """İçerik için vektör kaydeder; aynı content_id varsa üzerine yazar."""
        emb = list(embedding)
        if len(emb) != EMBEDDING_DIMENSION:
            raise ValueError(
                f"embedding boyutu {EMBEDDING_DIMENSION} olmalı, gelen: {len(emb)}"
            )

        stmt = insert(ContentEmbedding).values(content_id=content_id, embedding=emb)
        stmt = stmt.on_conflict_do_update(
            index_elements=[ContentEmbedding.content_id],
            set_={"embedding": stmt.excluded.embedding},
        )
        await self._session.execute(stmt)
        if commit:
            await self.commit()

    async def search_similar_contents(
        self,
        embedding: Sequence[float],
        limit: int = 50,
    ) -> List[Tuple[Content, float]]:
        """
        Kosinüs mesafesi (<=>) ile en yakın içerikleri döner.
        Dönüş: (Content, distance) — distance düşük = daha benzer.
        """
        emb = list(embedding)
        if len(emb) != EMBEDDING_DIMENSION:
            raise ValueError(
                f"embedding boyutu {EMBEDDING_DIMENSION} olmalı, gelen: {len(emb)}"
            )

        dist_expr = ContentEmbedding.embedding.cosine_distance(emb)
        q = (
            select(Content, dist_expr.label("distance"))
            .join(ContentEmbedding, ContentEmbedding.content_id == Content.id)
            .order_by(dist_expr)
            .limit(max(1, min(limit, 500)))
        )
        result = await self._session.execute(q)
        rows = result.all()
        return [(row[0], float(row[1])) for row in rows]

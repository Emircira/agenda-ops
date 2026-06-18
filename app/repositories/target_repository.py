"""Geriye dönük uyum: hedef kaynakları için `SourceRepository` takma adı."""

from app.repositories.source_repository import SourceRepository


class TargetRepository(SourceRepository):
    """İzleme hedefi kaynakları — implementasyon `SourceRepository` içindedir."""

    pass

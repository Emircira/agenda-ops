"""Radar modülü modeli — platform/kullanıcı bazlı günlük etkileşim skoru.

`compute_radar_daily` görevi her gün (00:30, Europe/Istanbul) bir önceki günün
içeriklerini (`contents` + `content_metrics`) toplulaştırıp bu tabloya yazar.
`GET /api/v1/radar` ucu, pencere (window) boyunca bu satırları toplulaştırarak
liderlik tablosu üretir.

Not: Model ayrı dosyada tanımlanır ama `app.models.base_class.Base` ile aynı
metadata'ya kayıt olur; uygulama import grafiğinde (router -> endpoint -> task ->
repository -> model) yüklendiği için `Base.metadata.create_all` ve Alembic tarafından
görülür.
"""

from datetime import datetime

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from app.models.base_class import Base


class RadarUserDaily(Base):
    """Bir platformdaki bir kullanıcının tek bir güne ait etkileşim özeti + skoru."""

    __tablename__ = "radar_user_daily"
    __table_args__ = (
        UniqueConstraint("platform", "username", "day", name="uq_radar_user_daily"),
        Index("ix_radar_user_daily_day_score", "day", "radar_score"),
        Index("ix_radar_user_daily_platform_day", "platform", "day"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String, nullable=False, index=True)
    username = Column(String, nullable=False, index=True)
    day = Column(Date, nullable=False, index=True)

    posts_count = Column(Integer, nullable=False, default=0)
    comments_received = Column(Integer, nullable=False, default=0)
    total_engagement = Column(Float, nullable=False, default=0.0)
    reach_estimate = Column(Float, nullable=False, default=0.0)

    # Üst üste aktif gün sayısı (streak). Skor bonusunda kullanılır.
    consecutive_days = Column(Integer, nullable=False, default=1)
    radar_score = Column(Float, nullable=False, default=0.0, index=True)

    computed_at = Column(DateTime, nullable=False, default=datetime.utcnow)

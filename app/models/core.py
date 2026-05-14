from typing import Optional, List
import uuid
import enum
from datetime import datetime
from sqlalchemy import (
    UniqueConstraint,
    Column,
    String,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Enum as SQLEnum,
    Index,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.base_class import Base

class SourceType(str, enum.Enum):
    rss = "rss"
    youtube = "youtube"
    x = "x"

class ContentType(str, enum.Enum):
    post = "post"
    comment = "comment"
    video = "video"
    article = "article"
    reply = "reply"

class Source(Base):
    __tablename__ = "sources"
    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String, nullable=False)  # twitter_self, twitter_competitor, twitter_trend, youtube, rss
    name = Column(String, nullable=False)
    url = Column(String, nullable=False)
    domain = Column(String, default="general")  # politics, sports, economy, general
    # Karargah: Rakip | Haber Ajansı | Şahıs-Hedef | Genel Gündem (UI / AI bağlamı)
    source_category = Column(String, nullable=False, default="general_agenda")
    active = Column(Boolean, default=True)

class ElectionCategory(str, enum.Enum):
    presidential = "presidential"
    parliamentary = "parliamentary"
    local = "local"
    referendum = "referendum"

class RiskLevel(str, enum.Enum):
    low = "low"
    med = "med"
    high = "high"

class Content(Base):
    __tablename__ = "contents"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=True) # ForeignKey eklendi
    platform = Column(String, nullable=False)
    external_id = Column(String, nullable=False, unique=True)
    author_name = Column(String)
    published_at = Column(DateTime, nullable=False)
    fetched_at = Column(DateTime, default=datetime.utcnow)
    text = Column(String, nullable=False)
    content_type = Column(SQLEnum(ContentType), nullable=False)
    url = Column(String)
    lang = Column(String, default="tr")
    domain = Column(String, default="general")  # politics, sports, economy, general
    raw_json = Column(JSONB) # Ham JSON verisi için
    is_analyzed = Column(Boolean, default=False, index=True)

    labels = relationship(
        "ContentLabel",
        back_populates="content",
        uselist=False,
    )
    
    # OSINT: Devasa veri sorguları için optimizasyon
    __table_args__ = (
        Index('ix_contents_platform_published', 'platform', 'published_at'),
        Index('ix_contents_domain', 'domain'),
    )

class ContentMetric(Base):
    __tablename__ = "content_metrics"
    id = Column(Integer, primary_key=True, autoincrement=True)
    content_id = Column(UUID(as_uuid=True), ForeignKey("contents.id", ondelete="CASCADE"), nullable=False)
    captured_at = Column(DateTime, default=datetime.utcnow)
    likes = Column(Integer, default=0)
    replies = Column(Integer, default=0)
    reposts = Column(Integer, default=0)
    views = Column(Integer, default=0)
    
    # Trend analizi hızı için
    __table_args__ = (
        Index('ix_content_metrics_captured_at', 'captured_at'),
    )

class ContentLabel(Base):
    __tablename__ = "content_labels"
    content_id = Column(UUID(as_uuid=True), ForeignKey("contents.id", ondelete="CASCADE"), primary_key=True)
    topic = Column(String)
    frame = Column(String)
    stance = Column(String) # support/oppose/neutral
    target = Column(String)
    risk_level = Column(String) # low/med/high
    confidence = Column(Float)
    summary = Column(String)
    
    content = relationship("Content", back_populates="labels")
    
    # OSINT: Yapay Zeka Destekli Anlam Analizi Eklemeleri
    sentiment_score = Column(Float, default=0.0) # -1.0 ile 1.0 arası
    manipulation_prob = Column(Float, default=0.0) # 0.0 - 1.0
    bot_likelihood = Column(Float, default=0.0) # 0.0 - 1.0
    sarcasm_detected = Column(Boolean, default=False)
    crisis_score = Column(Integer, default=0)
    sentiment = Column(String) # Pozitif/Negatif

class Opportunity(Base):
    __tablename__ = "opportunities"
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    topic = Column(String)
    frame = Column(String)
    score = Column(Float)
    parts = Column(JSONB) # json olarak saklanacak
    window_hours = Column(Integer)
    rationale = Column(JSONB) # Gerekçeler json olarak

# --- OSINT & MEDYA TAKİP MİMARİSİ TABLOLARI ---

class Keyword(Base):
    __tablename__ = "keywords"
    id = Column(Integer, primary_key=True, autoincrement=True)
    term = Column(String, nullable=False, unique=True)
    category = Column(String, default="general") # brand, competitor, person, event
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Alias(Base):
    __tablename__ = "aliases"
    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword_id = Column(Integer, ForeignKey("keywords.id", ondelete="CASCADE"), nullable=False)
    alias_term = Column(String, nullable=False)
    
    # Eş anlamlı kelimelerin hızlı aranması için (Fuzzy/Alias aramaları)
    __table_args__ = (
        Index('ix_aliases_alias_term', 'alias_term'),
    )

class EntityType(str, enum.Enum):
    person = "person"
    organization = "organization"
    location = "location"
    event = "event"

class Entity(Base):
    __tablename__ = "entities"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    type = Column(SQLEnum(EntityType), nullable=False)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    mention_count = Column(Integer, default=1)
    
    # NER (Kişi/Kurum tespiti) için hızlı arama
    __table_args__ = (
        Index('ix_entities_name', 'name'),
    )

class EntityRelation(Base):
    __tablename__ = "entity_relations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_entity_id = Column(Integer, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    target_entity_id = Column(Integer, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    content_id = Column(UUID(as_uuid=True), ForeignKey("contents.id", ondelete="CASCADE"), nullable=False)
    relation_type = Column(String) # örn: "mentioned_together", "works_for"
    created_at = Column(DateTime, default=datetime.utcnow)

# --- ELECTION RADAR MODELS (From tables.py) ---
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
# --- 3. YSK SEÇİM ARŞİVİ SİSTEMİ ---

class ElectionResult(Base):
    __tablename__ = "election_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    election_year: Mapped[int] = mapped_column(Integer, index=True)
    election_type: Mapped[ElectionCategory] = mapped_column(SQLEnum(ElectionCategory), index=True)
    election_detail: Mapped[Optional[str]] = mapped_column(String, index=True)
    province: Mapped[str] = mapped_column(String, index=True)
    district: Mapped[Optional[str]] = mapped_column(String, index=True)
    party: Mapped[str] = mapped_column(String, index=True)
    vote_count: Mapped[int] = mapped_column(Integer, default=0)
    raw_data: Mapped[dict] = mapped_column(JSONB, default={})
    # app/data/ysk_raw/... göreli yolu veya wikipedia:SayfaAdı
    source_json_file: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)


class ElectionRegionArchive(Base):
    """
    İl/ilçe bazlı tek kayıt: sonuç özeti + demografi JSONB (simülatör / AI).
    election_detail: 2015_1, 2023_cb_1_tur, 2019_il_yerel_yenilenme vb.
    """
    __tablename__ = "election_region_archives"
    __table_args__ = (
        UniqueConstraint(
            "election_year",
            "election_type",
            "election_detail",
            "province",
            "district_key",
            name="uq_election_region_archive",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    election_year: Mapped[int] = mapped_column(Integer, index=True)
    election_type: Mapped[ElectionCategory] = mapped_column(SQLEnum(ElectionCategory), index=True)
    election_detail: Mapped[str] = mapped_column(String(180), index=True, default="")
    province: Mapped[str] = mapped_column(String(120), index=True)
    district_key: Mapped[str] = mapped_column(String(120), index=True, default="")
    total_valid_votes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    winner_party: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    winner_candidate: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    results_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    demographics_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_files_json: Mapped[list] = mapped_column(JSONB, default=list)


class ElectionDemographicStat(Base):
    """YSK YasDagilim / CinsiyetDagilim JSON satırlarının yıl ve bölge bazlı özeti."""
    __tablename__ = "election_demographic_stats"
    id: Mapped[int] = mapped_column(primary_key=True)
    election_year: Mapped[int] = mapped_column(Integer, index=True)
    election_type: Mapped[ElectionCategory] = mapped_column(SQLEnum(ElectionCategory), index=True)
    election_detail: Mapped[Optional[str]] = mapped_column(String, index=True)
    province: Mapped[Optional[str]] = mapped_column(String, index=True)
    district: Mapped[Optional[str]] = mapped_column(String, index=True)
    party: Mapped[str] = mapped_column(String, index=True)
    dimension: Mapped[str] = mapped_column(String, index=True)  # gender | age
    bucket: Mapped[str] = mapped_column(String, index=True)
    count_value: Mapped[int] = mapped_column(Integer, default=0)
    source_json_file: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)


class ElectionRegionTrend(Base):
    """seed sonrası election_results üzerinden hesaplanan yıllık oy payı ve yıllık değişim."""
    __tablename__ = "election_region_trends"
    id: Mapped[int] = mapped_column(primary_key=True)
    province: Mapped[str] = mapped_column(String, index=True)
    district: Mapped[Optional[str]] = mapped_column(String, index=True)
    election_type: Mapped[ElectionCategory] = mapped_column(SQLEnum(ElectionCategory), index=True)
    election_detail: Mapped[Optional[str]] = mapped_column(String(180), index=True, nullable=True)
    party: Mapped[str] = mapped_column(String, index=True)
    ref_year: Mapped[int] = mapped_column(Integer, index=True)
    vote_share_pct: Mapped[float] = mapped_column(Float)
    vote_count: Mapped[int] = mapped_column(Integer, default=0)
    yoy_delta_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_json_file: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)

class CandidateDemographic(Base):
    __tablename__ = "candidate_demographics"
    id: Mapped[int] = mapped_column(primary_key=True)
    election_year: Mapped[int] = mapped_column(Integer, index=True)
    election_type: Mapped[ElectionCategory] = mapped_column(SQLEnum(ElectionCategory), index=True)
    province: Mapped[str] = mapped_column(String, index=True)
    party: Mapped[str] = mapped_column(String, index=True)
    gender: Mapped[Optional[str]] = mapped_column(String)
    education: Mapped[Optional[str]] = mapped_column(String)
    source_json_file: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)

# --- 4. YAPAY ZEKA KALICI HAFIZA (CACHING) ---


class ComplaintsRadarCache(Base):
    """Şikayet Radarı: il bazlı X örnekleme + Gemini süzgeci sonucu (TTL ile yenilenir)."""
    __tablename__ = "complaints_radar_cache"
    __table_args__ = (
        Index("ix_complaints_radar_cache_cached_at", "cached_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    province_key = Column(String(120), nullable=False, unique=True)
    province_label = Column(String(120), nullable=False)
    cached_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    payload_json = Column(JSONB, default=dict)


class RegionAnalysis(Base):
    __tablename__ = "region_analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    province: Mapped[str] = mapped_column(String, index=True)
    district: Mapped[Optional[str]] = mapped_column(String, index=True)
    # Seçim radarı önbellek anahtarı: UI election_type (local / presidential / parliamentary)
    neighborhood: Mapped[Optional[str]] = mapped_column(String, index=True)
    election_year: Mapped[int] = mapped_column(Integer)
    ai_summary: Mapped[str] = mapped_column(Text)
    last_analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class CityDemographics(Base):
    __tablename__ = "city_demographics"
    id = Column(Integer, primary_key=True, index=True)
    province = Column(String, index=True)
    year = Column(Integer)
    total_population = Column(Integer, default=0)
    growth_rate = Column(Float, default=0.0)      # Nüfus artış hızı
    university_grad_pct = Column(Float, default=0.0) # Üniversite mezun oranı
    unemployment_rate = Column(Float, default=0.0)   # İşsizlik oranı
    foreign_pop_pct = Column(Float, default=0.0)     # Yabancı nüfus oranı
    literacy_rate = Column(Float, default=0.0)       # Okuryazarlık oranı
    source_json_file = Column(String, default="city_stats.json", nullable=False)
    source_category = Column(String, default="tuik_city_aggregate", nullable=False)


class DistrictDemographics(Base):
    __tablename__ = "district_demographics"
    id = Column(Integer, primary_key=True, index=True)
    province = Column(String, index=True)      # Hangi ile bağlı? (Örn: İSTANBUL)
    district = Column(String, index=True)      # İlçe Adı (Örn: ESENYURT)
    year = Column(Integer)
    total_population = Column(Integer, default=0)
    growth_rate = Column(Float, default=0.0)
    university_grad_pct = Column(Float, default=0.0)
    unemployment_rate = Column(Float, default=0.0)
    foreign_pop_pct = Column(Float, default=0.0)
    source_json_file = Column(String, default="district_stats.json", nullable=False)
    source_category = Column(String, default="tuik_district_aggregate", nullable=False)


from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID

class SourceBase(BaseModel):
    type: str
    name: str
    url: str
    active: bool = True

class SourceCreate(SourceBase):
    pass

class SourceResponse(SourceBase):
    id: int
    last_fetched_at: Optional[datetime] = None
    domain: str = "general"
    source_category: str = "general_agenda"
    model_config = ConfigDict(from_attributes=True)


# --- API v1: IntelligenceSource (REST alan adları) ---
ALLOWED_INTEL_SOURCE_CATEGORIES = frozenset(
    {"competitor", "news_agency", "person_or_target", "general_agenda"}
)


class IntelligenceSourceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)
    source_type: str = Field(..., min_length=1, max_length=64)
    url_or_handle: str = Field(..., min_length=1, max_length=2048)
    is_active: bool = True
    domain: str = "general"
    source_category: str = "general_agenda"


class IntelligenceSourcePatch(BaseModel):
    is_active: Optional[bool] = None
    name: Optional[str] = Field(None, min_length=1, max_length=500)
    url_or_handle: Optional[str] = Field(None, min_length=1, max_length=2048)
    source_type: Optional[str] = Field(None, min_length=1, max_length=64)
    domain: Optional[str] = None
    source_category: Optional[str] = None


class IntelligenceSourceResponse(BaseModel):
    id: int
    name: str
    source_type: str
    url_or_handle: str
    is_active: bool
    last_fetched_at: Optional[datetime] = None
    domain: str = "general"
    source_category: str = "general_agenda"
    model_config = ConfigDict(from_attributes=True)

class OpportunityResponse(BaseModel):
    id: int
    created_at: datetime
    topic: str
    frame: str
    score: float
    parts: Dict[str, Any]
    window_hours: int
    rationale: Dict[str, Any]
    model_config = ConfigDict(from_attributes=True)

class ContentResponse(BaseModel):
    id: UUID
    platform: str
    author_name: Optional[str]
    text: str
    published_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SystemAlertResponse(BaseModel):
    """Sistem içi bildirim (SystemAlert tablosu)."""

    id: int
    alert_type: str
    severity: str
    message: str
    is_read: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class UnreadAlertsResponse(BaseModel):
    count: int
    alerts: List[SystemAlertResponse]
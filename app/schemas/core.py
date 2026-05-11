from pydantic import BaseModel, ConfigDict
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
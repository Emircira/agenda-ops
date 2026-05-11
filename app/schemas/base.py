from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any
from datetime import datetime
from app.models.core import SourceType

# --- Source Schemas ---
class SourceCreate(BaseModel):
    type: SourceType
    name: str
    url: str

class SourceOut(SourceCreate):
    id: int
    active: bool
    model_config = ConfigDict(from_attributes=True)

# --- Opportunity Schemas ---
class OpportunityOut(BaseModel):
    id: int
    topic: str
    score: float
    frame: str
    parts: Dict[str, Any]      # {velocity: 80, reach: 20...}
    rationale: Dict[str, Any]  # {item_count: 5...}
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
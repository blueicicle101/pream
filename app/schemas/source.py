"""
Pydantic schemas for Source configuration and API requests/responses.
"""
import json
from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, field_validator


class SourceBase(BaseModel):
    name: str
    type: str  # "xtream" or "m3u"
    url: str
    username: Optional[str] = None
    password: Optional[str] = None
    content_types: List[str] = ["live", "vod", "series"]
    priority: int = 10
    user_agent: Optional[str] = None
    enabled: bool = True

    @field_validator("content_types", mode="before")
    @classmethod
    def parse_content_types(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return v
        return ["live", "vod", "series"]


class SourceCreate(SourceBase):
    pass


class SourceUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    content_types: Optional[List[str]] = None
    priority: Optional[int] = None
    user_agent: Optional[str] = None
    enabled: Optional[bool] = None

    @field_validator("content_types", mode="before")
    @classmethod
    def parse_content_types(cls, v: Any) -> Optional[List[str]]:
        if v is None:
            return None
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return v
        return ["live", "vod", "series"]


class SourceRead(SourceBase):
    id: int
    expiry_date: Optional[str] = None
    max_connections: Optional[str] = None
    last_refresh: Optional[datetime] = None
    last_status: Optional[str] = "pending"
    last_error: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SourceTestRequest(BaseModel):
    type: str = "xtream"  # "xtream" or "m3u"
    url: str
    username: Optional[str] = None
    password: Optional[str] = None
    user_agent: Optional[str] = None


class SourceTestResponse(BaseModel):
    status: str  # "ok" or "error"
    message: str
    expiry_date: Optional[str] = None
    days_remaining: Optional[int] = None
    formatted_expiry: Optional[str] = None
    max_connections: Optional[str] = None
    active_connections: Optional[str] = None
    account_status: Optional[str] = None


"""
Pydantic schemas for the Rules Engine.
"""
from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict

RuleType = Literal[
    "rename_group",
    "rename_channel",
    "hide_group",
    "hide_channel",
    "set_group",
    "uppercase_channel",
    "lowercase_channel",
    "capitalize_channel",
    "titlecase_channel",
    "uppercase_group",
    "lowercase_group",
    "capitalize_group",
    "titlecase_group",
]
ContentType = Literal["live", "vod", "series", "all"]
MatchField = Literal["group", "name"]


class RuleBase(BaseModel):
    order: int = 0
    rule_type: RuleType
    content_type: ContentType = "all"
    match_field: MatchField = "group"
    match_pattern: str
    replacement: Optional[str] = None  # For rename rules (regex substitution)
    new_group: Optional[str] = None  # For set_group rules
    enabled: bool = True
    description: Optional[str] = None


class RuleCreate(RuleBase):
    pass


class RuleUpdate(BaseModel):
    order: Optional[int] = None
    rule_type: Optional[RuleType] = None
    content_type: Optional[ContentType] = None
    match_field: Optional[MatchField] = None
    match_pattern: Optional[str] = None
    replacement: Optional[str] = None
    new_group: Optional[str] = None
    enabled: Optional[bool] = None
    description: Optional[str] = None
    affected_count: Optional[int] = None


class RuleRead(RuleBase):
    id: int
    affected_count: int = 0
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RuleReorderRequest(BaseModel):
    rule_ids: List[int]


class RuleTestRequest(BaseModel):
    rule_type: RuleType
    content_type: ContentType = "all"
    match_field: MatchField = "group"
    match_pattern: str
    replacement: Optional[str] = None
    new_group: Optional[str] = None
    sample_name: str
    sample_group: str


class RuleTestResponse(BaseModel):
    matched: bool
    output_name: str
    output_group: str
    is_hidden: bool
    error: Optional[str] = None

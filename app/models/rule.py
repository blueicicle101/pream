"""Rule model — represents a transformation rule applied to library entries."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.database import Base


class Rule(Base):
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, index=True)
    order = Column(Integer, nullable=False, default=0)  # Execution order (ascending)
    rule_type = Column(
        String(50), nullable=False
    )  # rename_group, rename_channel, hide_group, hide_channel, set_group
    content_type = Column(
        String(20), nullable=False, default="all"
    )  # live, vod, series, all
    match_field = Column(String(20), nullable=False, default="group")  # group or name
    match_pattern = Column(Text, nullable=False)  # Regex pattern
    replacement = Column(Text, nullable=True)  # Replacement string (for rename rules)
    new_group = Column(Text, nullable=True)  # Target group (for set_group rules)
    enabled = Column(Boolean, nullable=False, default=True)
    description = Column(Text, nullable=True)  # Human-readable label
    affected_count = Column(Integer, nullable=False, default=0)  # Total library items modified
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<Rule(id={self.id}, type='{self.rule_type}', pattern='{self.match_pattern}')>"

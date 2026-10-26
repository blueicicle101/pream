"""Setting model — key/value store for runtime settings."""
from sqlalchemy import Column, String, Text

from app.database import Base


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(255), primary_key=True)
    value = Column(Text, nullable=True)  # JSON-encoded

    def __repr__(self):
        return f"<Setting(key='{self.key}')>"

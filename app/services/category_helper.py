"""
Deterministic category ID generator for Xtream Codes API.
Uses CRC32 to ensure group_name <-> category_id is completely stable across
server restarts and updates without requiring an extra database table.
"""
import zlib
from typing import Optional


def group_to_category_id(group_name: Optional[str]) -> int:
    """Return a deterministic positive integer category ID for a group name."""
    name = (group_name or "Uncategorized").strip()
    return (zlib.crc32(name.encode("utf-8")) & 0x7FFFFFFF) % 900000 + 1000

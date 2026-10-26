"""
Encrypted backup and restore service for Pream settings, sources, and rules.
Uses PBKDF2-HMAC-SHA256 (100,000 rounds) + RFC 8439 ChaCha20 cipher + HMAC-SHA256 authentication.
Zero external system dependencies required.
"""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import secrets
import struct
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.config import settings
from app.models.rule import Rule
from app.models.setting import Setting
from app.models.source import Source
from app.services.rules_engine import apply_all_rules_to_library

logger = logging.getLogger(__name__)

# ── RFC 8439 ChaCha20 Pure Python Implementation ──

def _rotl32(v: int, c: int) -> int:
    return ((v << c) & 0xFFFFFFFF) | (v >> (32 - c))


def _quarter_round(x: List[int], a: int, b: int, c: int, d: int):
    x[a] = (x[a] + x[b]) & 0xFFFFFFFF
    x[d] = _rotl32(x[d] ^ x[a], 16)
    x[c] = (x[c] + x[d]) & 0xFFFFFFFF
    x[b] = _rotl32(x[b] ^ x[c], 12)
    x[a] = (x[a] + x[b]) & 0xFFFFFFFF
    x[d] = _rotl32(x[d] ^ x[a], 8)
    x[c] = (x[c] + x[d]) & 0xFFFFFFFF
    x[b] = _rotl32(x[b] ^ x[c], 7)


def _chacha20_block(key_words: List[int], counter: int, nonce_words: List[int]) -> bytes:
    constants = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574]
    state = constants + list(key_words) + [counter] + list(nonce_words)
    orig = list(state)

    for _ in range(10):
        # Column round
        _quarter_round(state, 0, 4, 8, 12)
        _quarter_round(state, 1, 5, 9, 13)
        _quarter_round(state, 2, 6, 10, 14)
        _quarter_round(state, 3, 7, 11, 15)
        # Diagonal round
        _quarter_round(state, 0, 5, 10, 15)
        _quarter_round(state, 1, 6, 11, 12)
        _quarter_round(state, 2, 7, 8, 13)
        _quarter_round(state, 3, 4, 9, 14)

    out = [(state[i] + orig[i]) & 0xFFFFFFFF for i in range(16)]
    return struct.pack("<16I", *out)


def _chacha20_crypt(key: bytes, nonce: bytes, data: bytes, initial_counter: int = 1) -> bytes:
    """Encrypt or decrypt bytes using RFC 8439 ChaCha20 stream cipher."""
    if len(key) != 32:
        raise ValueError("ChaCha20 key must be exactly 32 bytes")
    if len(nonce) != 12:
        raise ValueError("ChaCha20 nonce must be exactly 12 bytes")

    key_words = list(struct.unpack("<8I", key))
    nonce_words = list(struct.unpack("<3I", nonce))

    out = bytearray(len(data))
    counter = initial_counter

    for block_start in range(0, len(data), 64):
        keystream = _chacha20_block(key_words, counter, nonce_words)
        counter = (counter + 1) & 0xFFFFFFFF
        block_end = min(block_start + 64, len(data))
        for i in range(block_start, block_end):
            out[i] = data[i] ^ keystream[i - block_start]

    return bytes(out)


def _derive_keys(password: str, salt: bytes, iterations: int = 100_000) -> Tuple[bytes, bytes]:
    """Derive 32-byte cipher key and 32-byte auth key via PBKDF2-HMAC-SHA256."""
    key_material = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, dklen=64
    )
    return key_material[:32], key_material[32:]


# ── Backup & Restore Logic ──

def export_encrypted_backup(db: Session, password: str) -> Dict[str, Any]:
    """
    Collect system settings, sources, and rules, serialize to JSON,
    and return an encrypted envelope with PBKDF2-HMAC-SHA256 + ChaCha20.
    """
    if not password:
        password = settings.ADMIN_PASSWORD

    # 1. Gather Settings
    db_settings = db.query(Setting).all()
    settings_data = [{"key": s.key, "value": s.value} for s in db_settings]

    # 2. Gather Sources
    db_sources = db.query(Source).order_by(Source.priority.asc(), Source.id.asc()).all()
    sources_data = []
    for s in db_sources:
        sources_data.append({
            "name": s.name,
            "type": s.type,
            "url": s.url,
            "username": s.username,
            "password": s.password,
            "content_types": s.content_types,
            "priority": s.priority,
            "user_agent": s.user_agent,
            "enabled": s.enabled,
        })

    # 3. Gather Rules
    db_rules = db.query(Rule).order_by(Rule.order.asc(), Rule.id.asc()).all()
    rules_data = []
    for r in db_rules:
        rules_data.append({
            "order": r.order,
            "rule_type": r.rule_type,
            "content_type": r.content_type,
            "match_field": r.match_field,
            "match_pattern": r.match_pattern,
            "replacement": r.replacement,
            "new_group": r.new_group,
            "enabled": r.enabled,
            "description": r.description,
        })

    payload = {
        "pream_backup": True,
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "server_name": settings.SERVER_NAME,
        "data": {
            "settings": settings_data,
            "sources": sources_data,
            "rules": rules_data,
        },
    }

    raw_bytes = json.dumps(payload, indent=2).encode("utf-8")

    # Cryptographic preparation
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    cipher_key, auth_key = _derive_keys(password, salt)

    ciphertext = _chacha20_crypt(cipher_key, nonce, raw_bytes)
    auth_tag = hmac.new(auth_key, salt + nonce + ciphertext, hashlib.sha256).hexdigest()

    return {
        "pream_backup": True,
        "version": 1,
        "kdf": "pbkdf2_sha256",
        "iterations": 100_000,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        "auth_tag": auth_tag,
    }


def import_encrypted_backup(
    db: Session, backup_dict: Dict[str, Any], password: str, mode: str = "merge"
) -> Dict[str, Any]:
    """
    Validate, decrypt, and import an encrypted backup envelope into the database.
    Mode: "merge" (update/add) or "replace" (wipes existing sources/rules first).
    """
    if not isinstance(backup_dict, dict) or not backup_dict.get("pream_backup"):
        raise ValueError("Invalid backup format: missing 'pream_backup' header.")

    if not password:
        password = settings.ADMIN_PASSWORD

    try:
        salt = base64.b64decode(backup_dict["salt"])
        nonce = base64.b64decode(backup_dict["nonce"])
        ciphertext = base64.b64decode(backup_dict["ciphertext"])
        expected_tag = backup_dict.get("auth_tag", "")
        iterations = backup_dict.get("iterations", 100_000)
    except Exception as e:
        raise ValueError(f"Malformed backup metadata: {e}")

    cipher_key, auth_key = _derive_keys(password, salt, iterations)
    actual_tag = hmac.new(auth_key, salt + nonce + ciphertext, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_tag, actual_tag):
        raise ValueError("Decryption failed: incorrect password or corrupted backup file.")

    raw_bytes = _chacha20_crypt(cipher_key, nonce, ciphertext)
    try:
        data_dict = json.loads(raw_bytes.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"Failed to parse decrypted backup data: {e}")

    content = data_dict.get("data", {})
    settings_list = content.get("settings", [])
    sources_list = content.get("sources", [])
    rules_list = content.get("rules", [])

    imported_settings = 0
    imported_sources = 0
    imported_rules = 0

    if mode == "replace":
        db.query(Rule).delete()
        db.query(Source).delete()
        db.commit()

    # 1. Restore Settings
    for s in settings_list:
        k, v = s.get("key"), s.get("value")
        if not k:
            continue
        existing = db.query(Setting).filter(Setting.key == k).first()
        if existing:
            existing.value = v
        else:
            db.add(Setting(key=k, value=v))
        imported_settings += 1

    # 2. Restore Sources
    for s in sources_list:
        name = s.get("name")
        if not name:
            continue
        existing_src = db.query(Source).filter(Source.name == name).first()
        if existing_src and mode == "merge":
            existing_src.type = s.get("type", "m3u")
            existing_src.url = s.get("url", "")
            existing_src.username = s.get("username")
            existing_src.password = s.get("password")
            existing_src.content_types = s.get("content_types", '["live","vod","series"]')
            existing_src.priority = s.get("priority", 10)
            existing_src.user_agent = s.get("user_agent")
            existing_src.enabled = s.get("enabled", True)
        else:
            new_src = Source(
                name=name,
                type=s.get("type", "m3u"),
                url=s.get("url", ""),
                username=s.get("username"),
                password=s.get("password"),
                content_types=s.get("content_types", '["live","vod","series"]'),
                priority=s.get("priority", 10),
                user_agent=s.get("user_agent"),
                enabled=s.get("enabled", True),
            )
            db.add(new_src)
        imported_sources += 1

    # 3. Restore Rules
    for r in rules_list:
        pattern = r.get("match_pattern")
        rtype = r.get("rule_type")
        if not pattern or not rtype:
            continue
        existing_rule = (
            db.query(Rule)
            .filter(Rule.match_pattern == pattern, Rule.rule_type == rtype)
            .first()
        )
        if existing_rule and mode == "merge":
            existing_rule.order = r.get("order", 0)
            existing_rule.content_type = r.get("content_type", "all")
            existing_rule.match_field = r.get("match_field", "group")
            existing_rule.replacement = r.get("replacement")
            existing_rule.new_group = r.get("new_group")
            existing_rule.enabled = r.get("enabled", True)
            existing_rule.description = r.get("description")
        else:
            new_rule = Rule(
                order=r.get("order", 0),
                rule_type=rtype,
                content_type=r.get("content_type", "all"),
                match_field=r.get("match_field", "group"),
                match_pattern=pattern,
                replacement=r.get("replacement"),
                new_group=r.get("new_group"),
                enabled=r.get("enabled", True),
                description=r.get("description"),
            )
            db.add(new_rule)
        imported_rules += 1

    db.commit()

    # Re-apply rules pipeline to synchronize library with imported rules
    try:
        apply_all_rules_to_library(db)
    except Exception as e:
        logger.warning(f"Error applying rules after backup import: {e}")

    return {
        "status": "ok",
        "settings_restored": imported_settings,
        "sources_restored": imported_sources,
        "rules_restored": imported_rules,
    }

"""
Rules API router for managing transformation and filtering rules.
"""
import re
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.rule import Rule
from app.schemas.rule import (
    RuleCreate,
    RuleRead,
    RuleReorderRequest,
    RuleTestRequest,
    RuleTestResponse,
    RuleUpdate,
)
from app.services.auth import require_admin
from app.services.rules_engine import (
    apply_all_rules_to_library,
    get_rules_diff_preview,
    test_rule_preview,
)

router = APIRouter(prefix="/api/rules", tags=["Rules"], dependencies=[Depends(require_admin)])


@router.get("/diff")
def get_rules_diff(scope: str = "channels", limit: int = 150, db: Session = Depends(get_db)):
    """Return Original vs Filtered 2-column comparison preview for channels, movies, series, or groups."""
    return get_rules_diff_preview(db, scope=scope, limit=limit)



@router.get("", response_model=List[RuleRead])
def list_rules(db: Session = Depends(get_db)):
    """List all rules ordered by execution order."""
    return db.query(Rule).order_by(Rule.order.asc(), Rule.id.asc()).all()


@router.post("", response_model=RuleRead, status_code=status.HTTP_201_CREATED)
def create_rule(rule_in: RuleCreate, db: Session = Depends(get_db)):
    """Create a new transformation rule."""
    # If order is 0, append to the end
    if rule_in.order == 0:
        max_order = db.query(Rule).count()
        order_val = max_order
    else:
        order_val = rule_in.order

    db_rule = Rule(
        order=order_val,
        rule_type=rule_in.rule_type,
        content_type=rule_in.content_type,
        match_field=rule_in.match_field,
        match_pattern=rule_in.match_pattern,
        replacement=rule_in.replacement,
        new_group=rule_in.new_group,
        enabled=rule_in.enabled,
        description=rule_in.description,
    )
    db.add(db_rule)
    db.commit()
    db.refresh(db_rule)
    return db_rule


@router.get("/{rule_id}", response_model=RuleRead)
def get_rule(rule_id: int, db: Session = Depends(get_db)):
    """Get single rule by ID."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.put("/{rule_id}", response_model=RuleRead)
def update_rule(rule_id: int, rule_in: RuleUpdate, db: Session = Depends(get_db)):
    """Update rule attributes."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    update_data = rule_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(rule, key, value)

    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    """Delete a rule."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    db.delete(rule)
    db.commit()
    return None


@router.post("/reorder")
def reorder_rules(req: RuleReorderRequest, db: Session = Depends(get_db)):
    """Update execution order of rules given a list of IDs in desired order."""
    for idx, rule_id in enumerate(req.rule_ids):
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if rule:
            rule.order = idx
    db.commit()
    return {"status": "ok", "message": "Rules reordered successfully"}


@router.post("/apply")
def trigger_apply_rules(db: Session = Depends(get_db)):
    """Immediately re-evaluate all rules across the entire library in SQLite."""
    stats = apply_all_rules_to_library(db)
    return stats


@router.post("/cancel")
def trigger_cancel_rules():
    """Cancel currently running library rule application."""
    from app.services.rules_engine import request_rules_cancellation
    request_rules_cancellation()
    return {"status": "ok", "message": "Rule application cancellation requested"}


@router.post("/purge-hidden")
def purge_hidden_items(db: Session = Depends(get_db)):
    """
    On-demand database upkeep: Permanently drops all rows currently marked as hidden
    from channels, movies, and series tables, and vacuums SQLite to reclaim disk space.
    """
    from app.models.channel import Channel
    from app.models.movie import Movie
    from app.models.series import Episode, Series
    from app.models.setting import Setting
    from sqlalchemy import text

    c_deleted = db.query(Channel).filter(Channel.is_hidden == True).delete(synchronize_session=False)
    m_deleted = db.query(Movie).filter(Movie.is_hidden == True).delete(synchronize_session=False)
    s_deleted = db.query(Series).filter(Series.is_hidden == True).delete(synchronize_session=False)
    
    # Also clean orphaned episodes if any series was deleted
    e_deleted = db.execute(
        text("DELETE FROM episodes WHERE series_id NOT IN (SELECT id FROM series)")
    ).rowcount

    # Set banner flag in database settings
    s_flag = db.query(Setting).filter(Setting.key == "hidden_items_purged").first()
    if not s_flag:
        s_flag = Setting(key="hidden_items_purged", value="true")
        db.add(s_flag)
    else:
        s_flag.value = "true"

    db.commit()

    # Reclaim SQLite storage space
    try:
        db.execute(text("VACUUM"))
        db.commit()
    except Exception as e:
        pass

    total_purged = c_deleted + m_deleted + s_deleted + e_deleted
    return {
        "status": "ok",
        "message": f"Purged {total_purged:,} hidden items from database ({c_deleted:,} Live, {m_deleted:,} Movies, {s_deleted:,} Series).",
        "channels_purged": c_deleted,
        "movies_purged": m_deleted,
        "series_purged": s_deleted,
        "episodes_purged": e_deleted,
    }


class QuickGroupActionRequest(BaseModel):
    group_name: str
    content_type: str = "all"  # "all", "live", "vod", "series"
    new_name: str = ""


from app.models.channel import Channel
from app.models.movie import Movie
from app.models.series import Series
from app.services.gemini import suggest_group_rules_with_gemini
from app.services.ingestion.manager import is_group_by_source_enabled


class AISuggestRulesRequest(BaseModel):
    user_instruction: str


@router.post("/ai-suggest")
async def ai_suggest_rules(req: AISuggestRulesRequest, db: Session = Depends(get_db)):
    """
    Generate candidate category transformation rules using Gemini AI based on user instructions.
    Groups are extracted from Live channels, VOD movies, and TV series in the database.
    """
    user_instr = req.user_instruction.strip()
    if not user_instr:
        raise HTTPException(status_code=400, detail="User instruction cannot be empty")

    gbs = is_group_by_source_enabled(db)

    # Extract clean raw/display groups from database
    live_groups_raw = [g[0] for g in db.query(Channel.display_group).distinct().all() if g[0]]
    vod_groups_raw = [g[0] for g in db.query(Movie.display_group).distinct().all() if g[0]]
    series_groups_raw = [g[0] for g in db.query(Series.display_group).distinct().all() if g[0]]

    # If GROUP_BY_SOURCE is active, strip the "[Source] " prefix so AI sees pure category names
    def clean_prefix(groups: List[str]) -> List[str]:
        cleaned = set()
        prefix_pattern = re.compile(r"^\[.*?\]\s*")
        for g in groups:
            if gbs:
                cleaned.add(prefix_pattern.sub("", g).strip())
            else:
                cleaned.add(g.strip())
        return sorted([c for c in cleaned if c])

    categories = {
        "live": clean_prefix(live_groups_raw),
        "vod": clean_prefix(vod_groups_raw),
        "series": clean_prefix(series_groups_raw),
    }

    try:
        rules = await suggest_group_rules_with_gemini(
            user_instruction=user_instr,
            categories=categories,
            group_by_source=gbs,
            db=db,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "status": "ok",
        "rules": rules,
        "sample_categories": {
            "live_count": len(categories["live"]),
            "vod_count": len(categories["vod"]),
            "series_count": len(categories["series"]),
        },
    }


@router.post("/quick-rename-group")
def quick_rename_group(req: QuickGroupActionRequest, db: Session = Depends(get_db)):
    """Create a permanent rename_group rule and perform a fast targeted update on matching items."""
    clean_target = req.new_name.strip()
    if not clean_target:
        raise HTTPException(status_code=400, detail="New group name cannot be empty")

    escaped_group = re.escape(req.group_name.strip())
    max_order = db.query(Rule).count()
    rule = Rule(
        order=max_order,
        rule_type="rename_group",
        content_type=req.content_type if req.content_type in ("live", "vod", "series") else "all",
        match_field="group",
        match_pattern=f"^{escaped_group}$",
        replacement=clean_target,
        enabled=True,
        description=f"Quick Rename: '{req.group_name}' -> '{clean_target}'",
    )
    db.add(rule)

    # Fast targeted direct update on SQLite
    affected = 0
    target_ct = req.content_type.lower() if req.content_type else "all"
    if target_ct in ("all", "live"):
        c_count = db.query(Channel).filter(Channel.display_group == req.group_name).update(
            {Channel.display_group: clean_target}, synchronize_session=False
        )
        affected += c_count
    if target_ct in ("all", "vod", "movie"):
        m_count = db.query(Movie).filter(Movie.display_group == req.group_name).update(
            {Movie.display_group: clean_target}, synchronize_session=False
        )
        affected += m_count
    if target_ct in ("all", "series"):
        s_count = db.query(Series).filter(Series.display_group == req.group_name).update(
            {Series.display_group: clean_target}, synchronize_session=False
        )
        affected += s_count

    rule.affected_count = affected
    db.commit()

    return {
        "status": "ok",
        "message": f"Group '{req.group_name}' renamed to '{clean_target}' ({affected} items updated).",
        "rule_id": rule.id,
        "affected": {"total_modified": affected},
    }


@router.post("/quick-hide-group")
def quick_hide_group(req: QuickGroupActionRequest, db: Session = Depends(get_db)):
    """Create a permanent hide_group rule and perform a fast targeted update on matching items."""
    escaped_group = re.escape(req.group_name.strip())
    max_order = db.query(Rule).count()
    rule = Rule(
        order=max_order,
        rule_type="hide_group",
        content_type=req.content_type if req.content_type in ("live", "vod", "series") else "all",
        match_field="group",
        match_pattern=f"^{escaped_group}$",
        enabled=True,
        description=f"Quick Hide Group: '{req.group_name}'",
    )
    db.add(rule)

    affected = 0
    target_ct = req.content_type.lower() if req.content_type else "all"
    if target_ct in ("all", "live"):
        c_count = db.query(Channel).filter(Channel.display_group == req.group_name).update(
            {Channel.is_hidden: True}, synchronize_session=False
        )
        affected += c_count
    if target_ct in ("all", "vod", "movie"):
        m_count = db.query(Movie).filter(Movie.display_group == req.group_name).update(
            {Movie.is_hidden: True}, synchronize_session=False
        )
        affected += m_count
    if target_ct in ("all", "series"):
        s_count = db.query(Series).filter(Series.display_group == req.group_name).update(
            {Series.is_hidden: True}, synchronize_session=False
        )
        affected += s_count

    rule.affected_count = affected
    db.commit()

    return {
        "status": "ok",
        "message": f"Group '{req.group_name}' hidden ({affected} items hidden).",
        "rule_id": rule.id,
        "affected": {"total_modified": affected},
    }


@router.post("/test", response_model=RuleTestResponse)
def test_rule(req: RuleTestRequest):
    """Test a rule against sample inputs without altering any saved data."""
    res = test_rule_preview(
        rule_type=req.rule_type,
        content_type=req.content_type,
        match_field=req.match_field,
        match_pattern=req.match_pattern,
        replacement=req.replacement,
        new_group=req.new_group,
        sample_name=req.sample_name,
        sample_group=req.sample_group,
    )
    return res

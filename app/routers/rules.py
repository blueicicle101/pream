"""
Rules API router for managing transformation and filtering rules.
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
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

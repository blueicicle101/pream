"""
TMDB router for triggering and monitoring metadata enrichment.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.auth import require_admin
from app.services.tmdb import enrich_pending_media

router = APIRouter(prefix="/api/tmdb", tags=["TMDB"], dependencies=[Depends(require_admin)])


@router.post("/enrich")
async def trigger_tmdb_enrich(
    batch_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Trigger metadata enrichment for pending movies and TV series."""
    result = await enrich_pending_media(db, batch_size=batch_size)
    return result

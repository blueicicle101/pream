"""
TMDB router for triggering and monitoring metadata enrichment.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.auth import require_admin
from app.services.tmdb import (
    apply_tmdb_id_match,
    enrich_pending_media,
    enrich_single_movie,
    enrich_single_series,
    get_tmdb_api_key,
    register_active_tmdb_task,
    request_tmdb_cancellation,
    search_tmdb_candidates,
)

router = APIRouter(prefix="/api/tmdb", tags=["TMDB"], dependencies=[Depends(require_admin)])


@router.post("/enrich")
async def trigger_tmdb_enrich(
    batch_size: int = Query(100, ge=1, le=1000),
    continuous: bool = Query(True, description="Keep enriching until entire catalog is completed"),
    max_items: Optional[int] = Query(None, ge=1, description="Optional cap on items to process in this run"),
    group: Optional[str] = Query(None, description="Optional specific category group to enrich"),
    content_type: Optional[str] = Query(None, description="Optional content type ('vod' or 'series')"),
    db: Session = Depends(get_db),
):
    """Trigger metadata enrichment for pending movies and TV series (optionally scoped to group or content_type)."""
    import asyncio
    current_task = asyncio.current_task()
    if current_task:
        register_active_tmdb_task(current_task)

    result = await enrich_pending_media(
        db,
        batch_size=batch_size,
        continuous=continuous,
        max_items=max_items,
        group=group,
        content_type=content_type,
    )
    return result


@router.post("/cancel")
def trigger_cancel_tmdb():
    """Cancel running TMDB enrichment operations immediately."""
    request_tmdb_cancellation()
    return {"status": "ok", "message": "TMDB enrichment cancellation requested"}


@router.post("/enrich/movie/{movie_id}")
async def trigger_enrich_single_movie(movie_id: int, db: Session = Depends(get_db)):
    """Instantly enrich a single specific movie by its ID."""
    result = await enrich_single_movie(movie_id, db)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("reason"))
    return result


@router.post("/enrich/series/{series_id}")
async def trigger_enrich_single_series(series_id: int, db: Session = Depends(get_db)):
    """Instantly enrich a single specific TV series by its ID."""
    result = await enrich_single_series(series_id, db)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("reason"))
    return result


@router.get("/search")
async def search_tmdb(
    query: str = Query(..., min_length=1),
    media_type: str = Query("movie", pattern="^(movie|series|vod|tv)$"),
    year: Optional[int] = Query(None, ge=1880, le=2100),
    language: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Search TMDB for candidate matches given a title query and optional year / language.
    Used by the manual match search modal.
    """
    api_key = get_tmdb_api_key(db)
    if not api_key:
        raise HTTPException(status_code=400, detail="No TMDB API key configured")

    norm_type = "series" if media_type in ("series", "tv") else "movie"
    candidates = await search_tmdb_candidates(
        query=query.strip(),
        media_type=norm_type,
        year=year,
        language=language.strip() if language else None,
        api_key=api_key,
    )
    return {"status": "ok", "query": query, "media_type": norm_type, "results": candidates}


@router.post("/apply-match/{media_type}/{item_id}")
async def apply_custom_tmdb_match(
    media_type: str,
    item_id: int,
    tmdb_id: int = Query(..., ge=1),
    language: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Apply a selected TMDB match candidate directly to a movie or series.
    """
    if media_type not in ("movie", "series", "vod", "tv"):
        raise HTTPException(status_code=400, detail="Invalid media_type. Must be movie or series.")

    norm_type = "series" if media_type in ("series", "tv") else "movie"
    result = await apply_tmdb_id_match(
        media_type=norm_type,
        item_id=item_id,
        tmdb_id=tmdb_id,
        db=db,
        language=language.strip() if language else None,
    )

    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("reason"))
    return result

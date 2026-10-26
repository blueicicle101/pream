"""
Rules Engine for Pream.
Transforms raw playlist metadata into clean, organized display fields without ever
modifying the immutable raw data (raw_name, raw_group).
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.config import settings
from app.models.channel import Channel
from app.models.movie import Movie
from app.models.rule import Rule
from app.models.series import Series
from app.models.setting import Setting
from app.models.source import Source

from app.services.task_status import set_task_status

logger = logging.getLogger(__name__)


def is_group_by_source_enabled(db: Session) -> bool:
    """Check if group_by_source is enabled in DB settings or .env config."""
    db_setting = db.query(Setting).filter(Setting.key == "group_by_source").first()
    if db_setting and db_setting.value:
        return db_setting.value.lower() in ("true", "1", "yes")
    return settings.GROUP_BY_SOURCE


def is_content_type_compatible(rule_content_type: Optional[str], item_type: str) -> bool:
    """Check if rule content_type targets the given item_type (live, vod, series)."""
    if not rule_content_type or rule_content_type.lower() in ("all", "*"):
        return True
    rc = rule_content_type.lower().strip()
    it = item_type.lower().strip()
    if rc == it:
        return True
    if rc in ("vod", "movie", "movies") and it in ("vod", "movie", "movies"):
        return True
    if rc in ("live", "channel", "channels", "tv") and it in ("live", "channel", "channels", "tv"):
        return True
    if rc in ("series", "show", "shows") and it in ("series", "show", "shows"):
        return True
    return False


def apply_single_rule_to_item(
    item_type: str,  # "live", "vod", "series"
    name: str,
    group: str,
    is_hidden: bool,
    rule: Rule,
    compiled_regex: Optional[re.Pattern] = None,
) -> Tuple[str, str, bool, bool]:
    """
    Apply a single rule to (name, group, is_hidden).
    Returns (new_name, new_group, new_is_hidden, was_matched).
    """
    if not rule.enabled:
        return name, group, is_hidden, False

    # Check content type compatibility (supports live, vod/movies, series)
    if not is_content_type_compatible(rule.content_type, item_type):
        return name, group, is_hidden, False

    pattern = compiled_regex
    if pattern is None:
        try:
            pattern = re.compile(rule.match_pattern, re.IGNORECASE)
        except re.error as e:
            logger.warning(f"Invalid regex in rule {rule.id}: {rule.match_pattern} ({e})")
            return name, group, is_hidden, False

    rtype = rule.rule_type
    target_field_val = group if rule.match_field == "group" else name

    # Prefix stripping helper for group matching when source prefix is enabled
    clean_group = re.sub(r"^\[[^\]]+\]\s*", "", group)

    matched = False

    if rtype in ("rename_group", "rename_category"):
        if pattern.search(group):
            matched = True
            try:
                group = pattern.sub(rule.replacement or "", group).strip()
            except re.error as e:
                logger.warning(f"Regex substitution error in rule {rule.id}: {e}")
        elif clean_group != group and pattern.search(clean_group):
            matched = True
            try:
                prefix = group[: len(group) - len(clean_group)]
                renamed_inner = pattern.sub(rule.replacement or "", clean_group).strip()
                group = f"{prefix}{renamed_inner}".strip()
            except re.error as e:
                logger.warning(f"Regex substitution error in rule {rule.id}: {e}")

    elif rtype in ("rename_channel", "rename_item", "rename_title", "rename_stream"):
        if pattern.search(name):
            matched = True
            try:
                name = pattern.sub(rule.replacement or "", name).strip()
            except re.error as e:
                logger.warning(f"Regex substitution error in rule {rule.id}: {e}")

    elif rtype in ("hide_group", "hide_category"):
        if pattern.search(group) or pattern.search(clean_group):
            matched = True
            is_hidden = True

    elif rtype in ("hide_channel", "hide_item", "hide_title", "hide_stream"):
        if pattern.search(name):
            matched = True
            is_hidden = True

    elif rtype in ("set_group", "set_category"):
        match_target = target_field_val
        clean_target = clean_group if rule.match_field == "group" else name
        if pattern.search(match_target) or (clean_target != match_target and pattern.search(clean_target)):
            matched = True
            if rule.new_group is not None:
                group = rule.new_group.strip()

    elif rtype in ("uppercase_channel", "lowercase_channel", "capitalize_channel", "titlecase_channel"):
        if pattern.search(name):
            matched = True
            try:
                if rtype == "uppercase_channel":
                    name = pattern.sub(lambda m: m.group(0).upper(), name).strip()
                elif rtype == "lowercase_channel":
                    name = pattern.sub(lambda m: m.group(0).lower(), name).strip()
                elif rtype == "capitalize_channel":
                    name = pattern.sub(lambda m: m.group(0).capitalize(), name).strip()
                elif rtype == "titlecase_channel":
                    name = pattern.sub(lambda m: m.group(0).title(), name).strip()
            except Exception as e:
                logger.warning(f"Case transform error in rule {rule.id}: {e}")

    elif rtype in ("uppercase_group", "lowercase_group", "capitalize_group", "titlecase_group"):
        def _apply_case(text: str) -> str:
            if rtype == "uppercase_group":
                return pattern.sub(lambda m: m.group(0).upper(), text).strip()
            elif rtype == "lowercase_group":
                return pattern.sub(lambda m: m.group(0).lower(), text).strip()
            elif rtype == "capitalize_group":
                return pattern.sub(lambda m: m.group(0).capitalize(), text).strip()
            elif rtype == "titlecase_group":
                return pattern.sub(lambda m: m.group(0).title(), text).strip()
            return text

        if pattern.search(group):
            matched = True
            try:
                group = _apply_case(group)
            except Exception as e:
                logger.warning(f"Case transform error in rule {rule.id}: {e}")
        elif clean_group != group and pattern.search(clean_group):
            matched = True
            try:
                prefix = group[: len(group) - len(clean_group)]
                renamed_inner = _apply_case(clean_group)
                group = f"{prefix}{renamed_inner}".strip()
            except Exception as e:
                logger.warning(f"Case transform error in rule {rule.id}: {e}")

    return name, group, is_hidden, matched



def apply_rules_to_item_chain(
    item_type: str,
    raw_name: str,
    raw_group: str,
    source_name: Optional[str],
    group_prefix_enabled: bool,
    rules_with_regex: List[Tuple[Rule, re.Pattern]],
    rule_counters: Optional[Dict[int, int]] = None,
) -> Tuple[str, str, bool]:
    """
    Reset item to raw baseline, then sequentially execute all matching rules.
    Increments rule_counters for each rule that modifies the item.
    """
    disp_name = raw_name or ""
    base_group = raw_group or "Uncategorized"
    if group_prefix_enabled and source_name:
        disp_group = f"[{source_name}] {base_group}"
    else:
        disp_group = base_group

    is_hidden = False

    for rule, compiled in rules_with_regex:
        prev_name, prev_group, prev_hid = disp_name, disp_group, is_hidden
        disp_name, disp_group, is_hidden, matched = apply_single_rule_to_item(
            item_type, disp_name, disp_group, is_hidden, rule, compiled
        )
        if matched and (disp_name != prev_name or disp_group != prev_group or is_hidden != prev_hid):
            if rule_counters is not None and rule.id in rule_counters:
                rule_counters[rule.id] += 1

    return disp_name, disp_group, is_hidden


def compile_rules(rules: List[Rule]) -> List[Tuple[Rule, re.Pattern]]:
    """Compile regexes for a list of rules, filtering out invalid regexes."""
    compiled_list = []
    for r in sorted(rules, key=lambda x: x.order):
        if not r.enabled:
            continue
        try:
            c = re.compile(r.match_pattern, re.IGNORECASE)
            compiled_list.append((r, c))
        except re.error as e:
            logger.warning(f"Skipping rule {r.id} with invalid regex: {r.match_pattern} ({e})")
    return compiled_list


def apply_all_rules_to_library(db: Session) -> Dict[str, Any]:
    """
    Reapply all active rules across the entire library in SQLite.
    Tracks affected counts per rule and updates task status.
    """
    set_task_status("running", "Applying Rules", "Applying transformation rules across library...", 15)

    rules = db.query(Rule).filter(Rule.enabled == True).order_by(Rule.order.asc()).all()
    compiled_rules = compile_rules(rules)
    rule_counters: Dict[int, int] = {r.id: 0 for r in rules}

    group_prefix = is_group_by_source_enabled(db)

    # Build source map for source names
    sources = db.query(Source).all()
    source_map = {s.id: s.name for s in sources}

    # 1. Process Channels (Live)
    channels = db.query(Channel).all()
    hidden_channels = 0
    for ch in channels:
        s_name = source_map.get(ch.source_id, "")
        d_name, d_grp, hid = apply_rules_to_item_chain(
            "live", ch.raw_name, ch.raw_group, s_name, group_prefix, compiled_rules, rule_counters
        )
        ch.display_name = d_name
        ch.display_group = d_grp
        ch.is_hidden = hid
        if hid:
            hidden_channels += 1

    set_task_status("running", "Applying Rules", f"Processed {len(channels)} Live channels. Processing VOD...", 50)

    # 2. Process Movies (VOD)
    from app.services.tmdb import is_tmdb_auto_group_enabled, GENRE_MAP, detect_language_code
    auto_group_enabled = is_tmdb_auto_group_enabled(db)

    movies = db.query(Movie).all()
    hidden_movies = 0
    for mv in movies:
        s_name = source_map.get(mv.source_id, "")
        d_name, d_grp, hid = apply_rules_to_item_chain(
            "vod", mv.raw_name, mv.raw_group, s_name, group_prefix, compiled_rules, rule_counters
        )
        mv.display_name = d_name
        # If movie was enriched and auto-group is enabled and no explicit group rule altered it, preserve TMDB genre group
        if auto_group_enabled and mv.genre and mv.tmdb_id:
            first_genre = mv.genre.split(",")[0].strip()
            lang = detect_language_code(mv.raw_group or d_grp)
            mv.display_group = f"{lang} | {first_genre}"
        else:
            mv.display_group = d_grp
        mv.is_hidden = hid
        if hid:
            hidden_movies += 1

    # 3. Process Series
    series_list = db.query(Series).all()
    hidden_series = 0
    for sr in series_list:
        s_name = source_map.get(sr.source_id, "")
        d_name, d_grp, hid = apply_rules_to_item_chain(
            "series", sr.raw_name, sr.raw_group, s_name, group_prefix, compiled_rules, rule_counters
        )
        sr.display_name = d_name
        if auto_group_enabled and sr.genre and sr.tmdb_id:
            first_genre = sr.genre.split(",")[0].strip()
            lang = detect_language_code(sr.raw_group or d_grp)
            sr.display_group = f"{lang} | {first_genre}"
        else:
            sr.display_group = d_grp
        sr.is_hidden = hid
        if hid:
            hidden_series += 1

    # Update affected count on each rule in DB
    for r in rules:
        r.affected_count = rule_counters.get(r.id, 0)

    db.commit()

    total_hidden = hidden_channels + hidden_movies + hidden_series
    set_task_status(
        "completed",
        "Complete",
        f"Rules applied: {total_hidden} items hidden, {len(compiled_rules)} rules executed",
        100,
    )

    return {
        "status": "ok",
        "rules_count": len(compiled_rules),
        "channels_processed": len(channels),
        "channels_hidden": hidden_channels,
        "movies_processed": len(movies),
        "movies_hidden": hidden_movies,
        "series_processed": len(series_list),
        "series_hidden": hidden_series,
        "rule_affected": rule_counters,
    }


def _get_model_group_diffs(db: Session, model, group_type_name: str, item_unit: str, limit: int = 150) -> List[Dict[str, Any]]:
    """Aggregate group transformation comparison (original vs filtered) with item counts and hidden state."""
    from sqlalchemy import case, func
    group_stats = (
        db.query(
            model.raw_group,
            model.display_group,
            func.count(model.id).label("total_items"),
            func.sum(case((model.is_hidden.is_(True), 1), else_=0)).label("hidden_items"),
        )
        .group_by(model.raw_group, model.display_group)
        .order_by(model.raw_group.asc())
        .limit(limit)
        .all()
    )
    diff_items = []
    for r_grp, d_grp, total, hidden in group_stats:
        rg = r_grp or "Uncategorized"
        dg = d_grp or "Uncategorized"
        tot = total or 0
        hid_count = hidden or 0
        is_all_hidden = (tot > 0 and hid_count == tot)
        is_renamed = (rg != dg)
        changed = is_renamed or is_all_hidden or (hid_count > 0)
        display_sub = (
            f"{group_type_name} ({tot - hid_count} active / {hid_count} hidden)"
            if hid_count > 0
            else f"{group_type_name} ({tot} {item_unit})"
        )
        diff_items.append({
            "id": None,
            "raw_name": rg,
            "raw_group": f"{group_type_name} ({tot} {item_unit})",
            "display_name": dg,
            "display_group": display_sub,
            "is_hidden": is_all_hidden,
            "changed": changed,
            "is_renamed": is_renamed,
            "is_group_changed": False,
        })
    return diff_items


def get_rules_diff_preview(
    db: Session,
    scope: str = "channels",  # "channels", "movies", "series", "channel_groups", "movie_groups", "series_groups", "all_groups"
    limit: int = 150,
) -> List[Dict[str, Any]]:
    """
    Generate Original vs Filtered 2-column preview of library items and groups.
    Supports Live Channels, Movies, Series, Channel Groups, Movie Groups, and Series Groups.
    """
    diff_items = []

    if scope == "channels":
        items = db.query(Channel).order_by(Channel.display_name.asc()).limit(limit).all()
        for it in items:
            is_renamed = (it.raw_name != it.display_name)
            is_group_changed = (it.raw_group != it.display_group)
            is_hidden = it.is_hidden
            changed = is_renamed or is_group_changed or is_hidden
            diff_items.append({
                "id": it.id,
                "raw_name": it.raw_name,
                "raw_group": it.raw_group or "Uncategorized",
                "display_name": it.display_name,
                "display_group": it.display_group or "Uncategorized",
                "is_hidden": is_hidden,
                "changed": changed,
                "is_renamed": is_renamed,
                "is_group_changed": is_group_changed,
            })

    elif scope == "movies":
        items = db.query(Movie).order_by(Movie.display_name.asc()).limit(limit).all()
        for it in items:
            is_renamed = (it.raw_name != it.display_name)
            is_group_changed = (it.raw_group != it.display_group)
            is_hidden = it.is_hidden
            changed = is_renamed or is_group_changed or is_hidden
            diff_items.append({
                "id": it.id,
                "raw_name": it.raw_name,
                "raw_group": it.raw_group or "Uncategorized",
                "display_name": it.display_name,
                "display_group": it.display_group or "Uncategorized",
                "is_hidden": is_hidden,
                "changed": changed,
                "is_renamed": is_renamed,
                "is_group_changed": is_group_changed,
            })

    elif scope == "series":
        items = db.query(Series).order_by(Series.display_name.asc()).limit(limit).all()
        for it in items:
            is_renamed = (it.raw_name != it.display_name)
            is_group_changed = (it.raw_group != it.display_group)
            is_hidden = it.is_hidden
            changed = is_renamed or is_group_changed or is_hidden
            diff_items.append({
                "id": it.id,
                "raw_name": it.raw_name,
                "raw_group": it.raw_group or "Uncategorized",
                "display_name": it.display_name,
                "display_group": it.display_group or "Uncategorized",
                "is_hidden": is_hidden,
                "changed": changed,
                "is_renamed": is_renamed,
                "is_group_changed": is_group_changed,
            })

    elif scope in ("channel_groups", "groups"):
        diff_items = _get_model_group_diffs(db, Channel, "Channel Group", "channels", limit)

    elif scope == "movie_groups":
        diff_items = _get_model_group_diffs(db, Movie, "Movie Group", "movies", limit)

    elif scope == "series_groups":
        diff_items = _get_model_group_diffs(db, Series, "Series Group", "series", limit)

    elif scope == "all_groups":
        sub_limit = max(10, limit // 3)
        diff_items = (
            _get_model_group_diffs(db, Channel, "Channel Group", "channels", sub_limit)
            + _get_model_group_diffs(db, Movie, "Movie Group", "movies", sub_limit)
            + _get_model_group_diffs(db, Series, "Series Group", "series", sub_limit)
        )

    return diff_items



def apply_all_rules_for_source(source_id: int, db: Session) -> Dict[str, Any]:
    """Reapply rules specifically to items of a single source."""
    rules = db.query(Rule).filter(Rule.enabled == True).order_by(Rule.order.asc()).all()
    compiled_rules = compile_rules(rules)
    group_prefix = is_group_by_source_enabled(db)

    source = db.query(Source).filter(Source.id == source_id).first()
    s_name = source.name if source else ""

    channels = db.query(Channel).filter(Channel.source_id == source_id).all()
    for ch in channels:
        d_name, d_grp, hid = apply_rules_to_item_chain(
            "live", ch.raw_name, ch.raw_group, s_name, group_prefix, compiled_rules
        )
        ch.display_name = d_name
        ch.display_group = d_grp
        ch.is_hidden = hid

    from app.services.tmdb import is_tmdb_auto_group_enabled, detect_language_code
    auto_group_enabled = is_tmdb_auto_group_enabled(db)

    movies = db.query(Movie).filter(Movie.source_id == source_id).all()
    for mv in movies:
        d_name, d_grp, hid = apply_rules_to_item_chain(
            "vod", mv.raw_name, mv.raw_group, s_name, group_prefix, compiled_rules
        )
        mv.display_name = d_name
        if auto_group_enabled and mv.genre and mv.tmdb_id:
            first_genre = mv.genre.split(",")[0].strip()
            lang = detect_language_code(mv.raw_group or d_grp)
            mv.display_group = f"{lang} | {first_genre}"
        else:
            mv.display_group = d_grp
        mv.is_hidden = hid

    series_list = db.query(Series).filter(Series.source_id == source_id).all()
    for sr in series_list:
        d_name, d_grp, hid = apply_rules_to_item_chain(
            "series", sr.raw_name, sr.raw_group, s_name, group_prefix, compiled_rules
        )
        sr.display_name = d_name
        if auto_group_enabled and sr.genre and sr.tmdb_id:
            first_genre = sr.genre.split(",")[0].strip()
            lang = detect_language_code(sr.raw_group or d_grp)
            sr.display_group = f"{lang} | {first_genre}"
        else:
            sr.display_group = d_grp
        sr.is_hidden = hid

    db.commit()
    return {"status": "ok", "source_id": source_id}


def test_rule_preview(
    rule_type: str,
    content_type: str,
    match_field: str,
    match_pattern: str,
    replacement: Optional[str],
    new_group: Optional[str],
    sample_name: str,
    sample_group: str,
) -> Dict[str, Any]:
    """Test a rule pattern against sample name & group without touching the database."""
    try:
        compiled = re.compile(match_pattern, re.IGNORECASE)
    except re.error as e:
        return {
            "matched": False,
            "output_name": sample_name,
            "output_group": sample_group,
            "is_hidden": False,
            "error": f"Invalid regex pattern: {str(e)}",
        }

    mock_rule = Rule(
        order=0,
        rule_type=rule_type,
        content_type=content_type,
        match_field=match_field,
        match_pattern=match_pattern,
        replacement=replacement,
        new_group=new_group,
        enabled=True,
    )

    out_name, out_group, is_hidden, matched = apply_single_rule_to_item(
        content_type if content_type != "all" else "live",
        sample_name,
        sample_group,
        False,
        mock_rule,
        compiled,
    )

    return {
        "matched": matched,
        "output_name": out_name,
        "output_group": out_group,
        "is_hidden": is_hidden,
        "error": None,
    }

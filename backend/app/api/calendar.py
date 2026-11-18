from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date as date_type, datetime, time, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request

from app.db.database import get_connection

router = APIRouter(prefix="/calendar", tags=["calendar"])
MAX_LIMIT = 2000


@router.get("")
def calendar_view(
    request: Request,
    view: str = "month",
    date: str | None = None,
    timezone: str = "UTC",
    limit: int = 500,
    offset: int = 0,
) -> dict:
    zone = _timezone(timezone)
    reference = _reference_date(date, zone, view)
    start, end = _view_bounds(reference, view, zone)
    events, total = _range_events(request, start, end, limit, offset)
    return _calendar_response(view, reference, timezone, events, total, limit, offset)


@router.get("/on-this-day")
def on_this_day(
    request: Request,
    date: str | None = None,
    timezone: str = "UTC",
    limit: int = 500,
    offset: int = 0,
) -> dict:
    zone = _timezone(timezone)
    reference = _reference_date(date, zone, "day")
    with get_connection(request.app.state.settings.database_path) as connection:
        rows = connection.execute(_EVENT_QUERY + " ORDER BY e.timestamp, e.event_id").fetchall()
    historical = [
        _serialise_event(row, zone)
        for row in rows
        if _event_local_date(row["timestamp"], zone).month == reference.month
        and _event_local_date(row["timestamp"], zone).day == reference.day
        and _event_local_date(row["timestamp"], zone) != reference
    ]
    total = len(historical)
    page = historical[offset:offset + _safe_limit(limit)]
    return {
        "reference_date": reference.isoformat(),
        "timezone": timezone,
        "events": page,
        "days": _group_by_day(page),
        "total": total,
        "limit": _safe_limit(limit),
        "offset": max(offset, 0),
        "has_more": offset + len(page) < total,
        "statistics": _statistics(page),
    }


@router.get("/recap")
def recap(
    request: Request,
    period: str = "week",
    date: str | None = None,
    timezone: str = "UTC",
) -> dict:
    zone = _timezone(timezone)
    reference = _reference_date(date, zone, "week" if period == "week" else "day")
    if period != "week":
        raise HTTPException(status_code=400, detail="only period=week is currently supported")
    start, end = _view_bounds(reference, "week", zone)
    events, total = _range_events(request, start, end, MAX_LIMIT, 0)
    return {
        "period": period,
        "reference_date": reference.isoformat(),
        "timezone": timezone,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total_events": total,
        "days": _group_by_day(events),
        "statistics": _statistics(events),
    }


_EVENT_QUERY = (
    "SELECT e.event_id, e.event_type, e.source_id, e.timestamp, e.date_basis, "
    "e.metadata_json, e.created_at, s.title, s.source_type, s.original_path, "
    "du.unit_id AS thumbnail_unit_id, du.thumbnail_path, du.page_or_slide "
    "FROM memory_events e LEFT JOIN sources s ON s.source_id = e.source_id "
    "LEFT JOIN document_units du ON du.unit_id = ("
    "SELECT unit_id FROM document_units WHERE source_id = e.source_id "
    "AND thumbnail_path IS NOT NULL ORDER BY unit_id LIMIT 1)"
)


def _range_events(request: Request, start: datetime, end: datetime, limit: int, offset: int):
    safe_limit = _safe_limit(limit)
    safe_offset = max(offset, 0)
    start_utc = start.astimezone(UTC).isoformat()
    end_utc = end.astimezone(UTC).isoformat()
    with get_connection(request.app.state.settings.database_path) as connection:
        total = connection.execute(
            "SELECT COUNT(*) FROM memory_events WHERE datetime(timestamp) >= datetime(?) "
            "AND datetime(timestamp) < datetime(?)",
            (start_utc, end_utc),
        ).fetchone()[0]
        rows = connection.execute(
            _EVENT_QUERY + " WHERE datetime(e.timestamp) >= datetime(?) "
            "AND datetime(e.timestamp) < datetime(?) "
            "ORDER BY e.timestamp, e.event_id LIMIT ? OFFSET ?",
            (start_utc, end_utc, safe_limit, safe_offset),
        ).fetchall()
    zone = start.tzinfo
    events = [_serialise_event(row, zone) for row in rows]
    return events, int(total)


def _calendar_response(view, reference, timezone, events, total, limit, offset):
    safe_limit = _safe_limit(limit)
    safe_offset = max(offset, 0)
    return {
        "view": view,
        "date": reference.isoformat() if view != "month" else reference.strftime("%Y-%m"),
        "timezone": timezone,
        "events": events,
        "days": _group_by_day(events),
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
        "has_more": safe_offset + len(events) < total,
        "statistics": _statistics(events),
    }


def _serialise_event(row, zone) -> dict:
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        metadata = {}
    title = row["title"] or metadata.get("source_title") or row["event_type"].replace("_", " ").title()
    return {
        "event_id": row["event_id"],
        "event_type": row["event_type"],
        "timestamp": row["timestamp"],
        "date": _event_local_date(row["timestamp"], zone).isoformat(),
        "title": title,
        "source_id": row["source_id"],
        "file_type": row["source_type"] or metadata.get("source_type"),
        "page": row["page_or_slide"],
        "thumbnail_url": (
            f"/units/{row['thumbnail_unit_id']}/thumbnail" if row["thumbnail_unit_id"] else None
        ),
        "date_basis": row["date_basis"],
        "metadata": metadata,
        "created_at": row["created_at"],
    }


def _group_by_day(events: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for event in events:
        grouped[event["date"]].append(event)
    return [{"date": day, "events": values, "count": len(values)} for day, values in sorted(grouped.items())]


def _statistics(events: list[dict]) -> dict:
    event_types = Counter(event["event_type"] for event in events)
    file_types = Counter(event["file_type"] for event in events if event["file_type"])
    return {
        "memory_count": len(events),
        "memories_per_day": dict(Counter(event["date"] for event in events)),
        "captured_files": event_types["file_imported"] + event_types["screenshot_captured"],
        "created_notes": event_types["note_created"],
        "saved_webpages": event_types["webpage_saved"],
        "study_sessions": event_types["study_session_completed"],
        "event_types": dict(event_types),
        "file_types": dict(file_types),
    }


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f"unknown timezone: {value}") from exc


def _reference_date(value: str | None, zone: ZoneInfo, view: str) -> date_type:
    if view not in {"month", "week", "day"}:
        raise HTTPException(status_code=400, detail="view must be month, week, or day")
    if value is None:
        return datetime.now(zone).date()
    try:
        if view == "month":
            return date_type.fromisoformat(f"{value}-01")
        return date_type.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM for month or YYYY-MM-DD") from exc


def _view_bounds(reference: date_type, view: str, zone: ZoneInfo) -> tuple[datetime, datetime]:
    if view == "month":
        next_month = date_type(reference.year + (reference.month == 12), 1 if reference.month == 12 else reference.month + 1, 1)
        return _local_start(reference, zone), _local_start(next_month, zone)
    if view == "week":
        start_day = reference - timedelta(days=reference.weekday())
        return _local_start(start_day, zone), _local_start(start_day + timedelta(days=7), zone)
    return _local_start(reference, zone), _local_start(reference + timedelta(days=1), zone)


def _local_start(day: date_type, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=zone)


def _event_local_date(value: str, zone) -> date_type:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(zone).date()


def _safe_limit(value: int) -> int:
    return min(max(int(value), 1), MAX_LIMIT)

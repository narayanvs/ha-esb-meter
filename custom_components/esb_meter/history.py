"""Bounded interval ledger, dated summaries and correction-aware replay plans."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from .parser import Reading, hourly, merge, summary

RETENTION_DAYS = 730
LOCAL = ZoneInfo("Europe/Dublin")


def decode(rows):
    return tuple(Reading(d, datetime.fromisoformat(t), Decimal(v)) for d, t, v in rows)


def encode(rows):
    return [[r.direction, r.start.isoformat(), str(r.kwh)] for r in rows]


def plan(previous, incoming, base, anchor, now):
    """Replay from first changed hour, with sums anchored before retained history."""
    incoming = tuple(r for r in incoming if anchor is None or r.start >= anchor)
    old = {r.key: r for r in previous}
    changed = [r.start for r in incoming if old.get(r.key) != r]
    merged, corrections = merge(previous, incoming)
    first = min(changed).replace(minute=0, second=0, microsecond=0) if changed else None
    stats = {}
    for direction in ("import", "export"):
        total = Decimal(str(base.get(direction, "0")))
        stats[direction] = []
        for hour, energy, _ in hourly(merged, direction):
            total += energy
            if first is not None and hour >= first:
                stats[direction].append({"start": hour, "sum": float(total)})
    cutoff = (now.astimezone(timezone.utc) - timedelta(days=RETENTION_DAYS)).replace(
        minute=0, second=0, microsecond=0)
    new_base = {d: Decimal(str(base.get(d, "0"))) for d in ("import", "export")}
    for r in merged:
        if r.start < cutoff:
            new_base[r.direction] += r.kwh
    retained = tuple(r for r in merged if r.start >= cutoff)
    return retained, {d: str(v) for d, v in new_base.items()}, cutoff, stats, corrections


def overview(readings, now):
    result = summary(readings) if readings else {"import": {}, "export": {}, "latest": None}
    today = now.astimezone(LOCAL).date()
    for direction in ("import", "export"):
        source = [r for r in readings if r.direction == direction]
        by_day = {}
        for r in source:
            day = r.start.astimezone(LOCAL).date()
            by_day.setdefault(day, []).append(r)
        complete = []
        for day, rows in by_day.items():
            start = datetime.combine(day, datetime.min.time(), LOCAL).astimezone(timezone.utc)
            end = datetime.combine(day + timedelta(days=1), datetime.min.time(), LOCAL).astimezone(timezone.utc)
            if day < today and len(rows) == int((end-start).total_seconds() / 1800):
                complete.append(day)
        latest_day = max(complete) if complete else None
        month_rows = [r for r in source if r.start.astimezone(LOCAL).date().replace(day=1) == today.replace(day=1)]
        result[direction].update(
            available=bool(source),
            latest_complete_day=latest_day.isoformat() if latest_day else None,
            latest_day_kwh=float(sum((r.kwh for r in by_day[latest_day]), Decimal(0))) if latest_day else None,
            month_kwh=float(sum((r.kwh for r in month_rows), Decimal(0))) if month_rows else None,
        )
    result["lag_hours"] = round((now-result["latest"]).total_seconds()/3600, 1) if result["latest"] else None
    return result

"""Strict ESB interval parsing. Independent of HA; never logs source rows."""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
import hashlib
import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

MAX_BYTES = 32 * 1024 * 1024
MAX_ROWS = 150_000
TYPES = {
    "Active Import Interval (kW)": ("import", Decimal("0.5")),
    "Active Export Interval (kW)": ("export", Decimal("0.5")),
    "Active Import Interval (kWh)": ("import", Decimal("1")),
    "Active Export Interval (kWh)": ("export", Decimal("1")),
}


class HDFError(ValueError):
    """A safe error code; never includes identifiers or source content."""


@dataclass(frozen=True)
class Reading:
    direction: str
    start: datetime
    kwh: Decimal

    @property
    def key(self) -> tuple[str, datetime]:
        return self.direction, self.start


@dataclass(frozen=True)
class Parsed:
    meter_key: str
    readings: tuple[Reading, ...]
    duplicates: int


def interval_start(label: str, basis: str) -> datetime:
    """Interpret end labels explicitly; reject DST ambiguity instead of guessing."""
    if basis not in ("UTC", "Europe/Dublin"):
        raise HDFError("invalid_time_basis")
    try:
        naive = datetime.strptime(label, "%d-%m-%Y %H:%M")
    except ValueError as exc:
        raise HDFError("invalid_timestamp") from exc
    if naive.minute not in (0, 30):
        raise HDFError("non_half_hour_timestamp")
    zone = ZoneInfo(basis)
    candidates = {
        aware.astimezone(timezone.utc)
        for fold in (0, 1)
        if (aware := naive.replace(tzinfo=zone, fold=fold))
        .astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive
    }
    if not candidates:
        raise HDFError("nonexistent_local_time")
    if len(candidates) != 1:
        raise HDFError("ambiguous_local_time")
    return candidates.pop() - timedelta(minutes=30)


def _resolve_local_labels(source: list[dict]) -> dict[int, datetime]:
    """Resolve a complete repeated hour only from verified ordered occurrences.

    ESB local-time CSVs can repeat 01:00 and 01:30 during the autumn transition.
    Require two occurrences of both labels per direction and monotonic UTC order.
    Partial/unsorted ambiguous data fails closed; ordinary duplicates elsewhere
    remain subject to the normal conflict checks.
    """
    zone = ZoneInfo("Europe/Dublin")
    groups = defaultdict(list)
    for index, row in enumerate(source):
        kind = TYPES.get((row.get("Read Type") or "").strip())
        if kind is None:
            raise HDFError("unsupported_read_type")
        label = row.get("Read Date and End Time") or ""
        try:
            end = interval_start(label, "Europe/Dublin") + timedelta(minutes=30)
            candidates = [end]
        except HDFError as exc:
            if str(exc) != "ambiguous_local_time":
                raise
            naive = datetime.strptime(label, "%d-%m-%Y %H:%M")
            candidates = sorted({naive.replace(tzinfo=zone, fold=f).astimezone(timezone.utc)
                                 for f in (0, 1)})
        groups[kind[0]].append((index, label, candidates))
    resolved = {}
    for group in groups.values():
        ambiguous = Counter(label for _, label, choices in group if len(choices) == 2)
        days = Counter(label.split(" ")[0] for label in ambiguous)
        if ambiguous and (any(count != 2 for count in ambiguous.values())
                          or any(count != 2 for count in days.values())):
            raise HDFError("incomplete_repeated_hour")
        ordinary = [choices[0] for _, _, choices in group if len(choices) == 1]
        descending = next((a > b for a, b in zip(ordinary, ordinary[1:]) if a != b), None)
        if ambiguous and descending is None:
            raise HDFError("ambiguous_file_order")
        seen = Counter()
        previous = None
        for index, label, choices in group:
            if len(choices) == 1:
                end = choices[0]
            else:
                occurrence = seen[label]
                end = choices[1 - occurrence if descending else occurrence]
                seen[label] += 1
            if ambiguous and previous is not None and ((descending and end > previous)
                                                       or (not descending and end < previous)):
                raise HDFError("ambiguous_file_order")
            resolved[index] = end - timedelta(minutes=30)
            previous = end
    return resolved


def parse_text(text: str, basis: str) -> Parsed:
    """Accept one meter's interval file; identical duplicates are harmless."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise HDFError("file_too_large")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff"), newline=""))
    required = {"MPRN", "Read Type", "Read Value", "Read Date and End Time"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise HDFError("invalid_columns")
    source = list(reader)
    if len(source) > MAX_ROWS:
        raise HDFError("too_many_rows")
    local_labels = _resolve_local_labels(source) if basis == "Europe/Dublin" else {}
    meter = None
    rows: dict[tuple[str, datetime], Reading] = {}
    duplicates = 0
    try:
        for index, row in enumerate(source, 1):
            if index > MAX_ROWS:
                raise HDFError("too_many_rows")
            identity = (row.get("MPRN") or "").strip()
            if not identity or not identity.isdecimal() or len(identity) != 11:
                raise HDFError("invalid_meter_id")
            if meter is not None and meter != identity:
                raise HDFError("multiple_meters")
            meter = identity
            kind = TYPES.get((row.get("Read Type") or "").strip())
            if kind is None:
                raise HDFError("unsupported_read_type")
            try:
                value = Decimal((row.get("Read Value") or "").strip())
            except InvalidOperation as exc:
                raise HDFError("invalid_value") from exc
            if not value.is_finite() or value < 0 or value > 1_000_000:
                raise HDFError("invalid_value")
            start = local_labels[index - 1] if basis == "Europe/Dublin" else interval_start(
                row.get("Read Date and End Time") or "", basis)
            reading = Reading(kind[0], start, value * kind[1])
            previous = rows.get(reading.key)
            if previous is not None:
                if previous.kwh != reading.kwh:
                    raise HDFError("conflicting_duplicate")
                duplicates += 1
            rows[reading.key] = reading
    except csv.Error as exc:
        raise HDFError("invalid_csv") from exc
    if not rows or meter is None:
        raise HDFError("empty_file")
    # Stable identity for local entry matching; raw MPRN is not retained.
    return Parsed(hashlib.sha256(meter.encode()).hexdigest(),
                  tuple(sorted(rows.values(), key=lambda r: (r.start, r.direction))),
                  duplicates)


def import_path(folder: Path, filename: str) -> Path:
    """Only plain CSV filenames in the dedicated import folder; no symlink escape."""
    if not filename or Path(filename).name != filename or not filename.lower().endswith(".csv"):
        raise HDFError("invalid_filename")
    root = folder.resolve()
    path = (root / filename).resolve()
    if path.parent != root:
        raise HDFError("invalid_filename")
    return path


def parse_file(folder: Path, filename: str, basis: str) -> Parsed:
    path = import_path(folder, filename)
    try:
        with path.open("rb") as file:
            data = file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise HDFError("file_too_large")
        return parse_text(data.decode("utf-8-sig"), basis)
    except (OSError, UnicodeError) as exc:
        raise HDFError("cannot_read_file") from exc


def merge(existing: tuple[Reading, ...], incoming: tuple[Reading, ...]) -> tuple[tuple[Reading, ...], int]:
    """Latest accepted file corrects overlaps; preserve older history."""
    rows = {r.key: r for r in existing}
    corrections = sum(r.key in rows and rows[r.key].kwh != r.kwh for r in incoming)
    rows.update({r.key: r for r in incoming})
    return tuple(sorted(rows.values(), key=lambda r: (r.start, r.direction))), corrections


def hourly(readings: tuple[Reading, ...], direction: str) -> list[tuple[datetime, Decimal, int]]:
    """Return observed kWh and interval count per UTC hour; do not invent gaps."""
    buckets: dict[datetime, tuple[Decimal, int]] = {}
    for r in readings:
        if r.direction != direction:
            continue
        hour = r.start.replace(minute=0, second=0, microsecond=0)
        energy, count = buckets.get(hour, (Decimal(0), 0))
        buckets[hour] = energy + r.kwh, count + 1
    return [(hour, energy, count) for hour, (energy, count) in sorted(buckets.items())]


def summary(readings: tuple[Reading, ...]) -> dict:
    result = {}
    for direction in ("import", "export"):
        subset = [r for r in readings if r.direction == direction]
        hours = hourly(readings, direction)
        missing = (int((subset[-1].start - subset[0].start).total_seconds() / 1800)
                   + 1 - len(subset)) if subset else 0
        result[direction] = {
            "kwh": float(sum((r.kwh for r in subset), Decimal(0))),
            "intervals": len(subset), "missing_intervals": missing,
            "partial_hours": sum(count != 2 for _, _, count in hours),
        }
    result["latest"] = max(r.start for r in readings) + timedelta(minutes=30)
    return result

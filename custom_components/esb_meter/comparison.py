"""Compare matching complete Irish days; no live readings or fabricated gaps."""
from datetime import date, datetime, timedelta, timezone
import math
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo('Europe/Dublin')


def day_window(label):
    day = date.fromisoformat(label)
    start = datetime.combine(day, datetime.min.time(), LOCAL).astimezone(timezone.utc)
    end = datetime.combine(day+timedelta(days=1), datetime.min.time(), LOCAL).astimezone(timezone.utc)
    return start, end


def compare_day(label, esb_kwh, rows, absolute=0.5, percentage=5.0):
    """Rows must be HA hourly changes, converted to kWh by recorder."""
    start, end = day_window(label)
    expected = {int(start.timestamp())+3600*i for i in range(int((end-start).total_seconds()/3600))}
    boundary = [row for row in rows if int(row['start']) == int(start.timestamp())-3600]
    if len(boundary) != 1 or boundary[0].get('sum') is None or not math.isfinite(boundary[0]['sum']):
        return {'status':'incomplete_reference','date':label}
    values = {}
    for row in rows:
        stamp = int(row['start'])
        if stamp not in expected:
            continue
        value = row.get('change')
        if stamp in values or value is None or not math.isfinite(value) or value < 0:
            return {'status':'incomplete_reference','date':label}
        values[stamp] = value
    if set(values) != expected:
        return {'status':'incomplete_reference','date':label,'reference_hours':len(values),'expected_hours':len(expected)}
    reference = math.fsum(values.values())
    difference = reference-esb_kwh
    percent = difference/esb_kwh*100 if esb_kwh > 0 else None
    # Both limits must be exceeded; small absolute differences never trigger on tiny days.
    anomaly = abs(difference) > absolute and (esb_kwh == 0 or abs(percent) > percentage)
    return {'status':'anomaly' if anomaly else 'matched','date':label,
            'esb_kwh':esb_kwh,'reference_kwh':reference,'difference_kwh':difference,
            'difference_percent':percent,'reference_hours':len(values),
            'absolute_threshold_kwh':absolute,'percentage_threshold':percentage}

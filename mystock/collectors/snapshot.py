"""Pure snapshot normalization. No API calls or account information."""
from datetime import datetime, timezone
import math
from zoneinfo import ZoneInfo

FIELDS = ('last_price', 'prev_close_price', 'open_price', 'high_price', 'low_price', 'volume_ratio')


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def source_time(code, value):
    zone = 'Asia/Hong_Kong' if code.startswith('HK.') else 'America/New_York' if code.startswith('US.') else None
    raw = value if isinstance(value, str) and value.strip() else None
    stamp = None
    if zone and raw:
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                local = dt.replace(tzinfo=ZoneInfo(zone))
                # Reject ambiguous and nonexistent local times, rather than guess.
                if local.utcoffset() != local.replace(fold=1).utcoffset():
                    raise ValueError('ambiguous/nonexistent time')
                dt = local
            stamp = dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return dict(snapshot_time_raw=raw, snapshot_timezone=zone, snapshot_time_utc=stamp)


def fields(code, row):
    result = {key: number(row.get(key)) for key in FIELDS}
    result.update({key: number(row.get(key)) for key in ('lot_size', 'price_spread')})
    value = row.get('suspension')
    # Do not let bool('False') or NaN turn unknown into a valid market state.
    text = str(value).lower()
    result['suspension'] = 1 if text in ('true', '1', '1.0') else 0 if text in ('false', '0', '0.0') else None
    status = row.get('sec_status')
    result['sec_status'] = str(status) if status is not None and str(status).lower() not in ('nan', 'none', 'n/a', '') else None
    result.update(source_time(code, row.get('update_time')))
    return result


def outcome(row, columns):
    if not {'last_price', 'update_time'} <= set(columns):
        return 'unsupported', 'missing_core_fields'
    if not row.get('snapshot_timezone'):
        return 'unsupported', 'unknown_market'
    if not row.get('snapshot_time_utc'):
        return 'unknown', 'unknown_source_time'
    try:
        if datetime.fromisoformat(row['snapshot_time_utc']) > datetime.fromisoformat(row['snap_synced_at']):
            return 'unknown', 'unknown_source_time'
    except (ValueError, TypeError, KeyError):
        return 'unknown', 'unknown_source_time'
    if not row.get('last_price') or row['last_price'] <= 0:
        return 'partial', 'invalid_price'
    if any(row.get(k) is None for k in (*FIELDS, 'suspension', 'sec_status', 'lot_size')):
        return 'partial', 'missing_fields'
    return 'ok', None

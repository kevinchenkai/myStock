"""Read-only cache provenance. No collectors, schema initialization or writes."""
from datetime import datetime
from zoneinfo import ZoneInfo
import re
from ..ml import sessions

SOURCES = {
    'futu_position': '持仓快照', 'futu_funds': '账户资金',
    'futu_order': '订单', 'futu_deal': '成交', 'yfinance': '日线行情',
    'fx_usdcny': '美元汇率', 'yf_profile': '公司资料',
    'futu_snapshot': 'Futu 缓存快照', 'futu_capflow': '资金流向',
}
REASONS = {
    'request_failed': '请求失败，保留上次缓存', 'not_returned': '本次未返回该标的',
    'missing_core_fields': '来源未提供必要字段', 'missing_fields': '字段不完整，保留上次缓存',
    'invalid_price': '价格无效，保留上次缓存', 'unknown_market': '市场无法识别',
    'unknown_source_time': '来源时间无法确认',
}


def table(conn, name):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def valid_code(code):
    return bool(re.fullmatch(r'(HK|US)\.[A-Za-z0-9._-]{1,24}', code))


def stamp(value):
    """Old naive timestamps retain their text, never an invented time zone."""
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.isoformat() if dt.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def aggregate(conn, source):
    rows = []
    if table(conn, 'sync_log'):
        for condition in ('', " AND status='ok'"):
            row = conn.execute('SELECT status,run_at FROM sync_log WHERE source=?' + condition + ' ORDER BY id DESC LIMIT 1', (source,)).fetchone()
            rows.append(dict(row) if row else {})
    else:
        rows = [{}, {}]
    latest, success = rows
    time_columns = {
        'futu_position': ('positions', 'snapshot_date'), 'futu_funds': ('account_funds', 'snapshot_date'),
        'futu_order': ('orders', 'updated_time'), 'futu_deal': ('deals', 'create_time'),
        'yfinance': ('daily_quotes', 'date'), 'fx_usdcny': ('fx_rates', 'date'),
        'futu_capflow': ('capital_flow', 'date'), 'futu_snapshot': ('stock_profiles', 'snapshot_time_utc'),
    }
    data_as_of = None
    if source in time_columns:
        name, column = time_columns[source]
        if column in {r[1] for r in conn.execute(f'PRAGMA table_info({name})')}:
            data_as_of = conn.execute(f'SELECT MAX({column}) FROM {name}').fetchone()[0]
    return dict(source=source, label=SOURCES[source], scope='source_summary',
                status=latest.get('status', 'unknown'), data_as_of=data_as_of,
                last_attempt_at=latest.get('run_at'), last_success_at=success.get('run_at'),
                attempt_timezone_known=stamp(latest.get('run_at')) is not None)


def daily_state(code, row, now):
    result = dict(status='unknown', expected_session=None, data_as_of=row.get('date') if row else None)
    try:
        if not valid_code(code):
            return result
        current = sessions.utc(now)
        zone = ZoneInfo('Asia/Hong_Kong' if code.startswith('HK.') else 'America/New_York')
        day = current.astimezone(zone).date().isoformat()
        dates = sessions.window(code, day, 2)
        closed = [d for d in dates if sessions.session(code, d)['final_at'] <= current]
        if not closed:
            return result
        expected = closed[-1]
        result['expected_session'] = expected
        if not row:
            result['status'] = 'empty'
        elif row['date'] < expected:
            result['status'] = 'stale'
        elif row['date'] > day:
            result['status'] = 'unknown'
        elif row['date'] > expected:
            result['status'] = 'pending'
        elif not stamp(row.get('synced_at')):
            result['status'] = 'unknown'
        elif sessions.utc(row['synced_at']) > current:
            result['status'] = 'unknown'
        elif sessions.daily_final(code, row, current):
            result['status'] = 'current'
        else:
            result['status'] = 'awaiting_final_data'
    except (ValueError, KeyError):
        pass
    return result


def stock_attempt(conn, source, code):
    if not table(conn, 'collection_status'):
        return None
    row = conn.execute('SELECT * FROM collection_status WHERE source=? AND code=?', (source, code)).fetchone()
    if not row:
        return None
    row = dict(row)
    row['scope'] = 'stock'
    row['reason'] = REASONS.get(row.get('reason'))
    return row


def stock_status(conn, code, now, summaries=None):
    summaries = summaries or {s: aggregate(conn, s) for s in ('yfinance', 'yf_profile')}
    quote = conn.execute('SELECT * FROM daily_quotes WHERE futu_code=? ORDER BY date DESC LIMIT 1', (code,)).fetchone() if table(conn, 'daily_quotes') else None
    profile = conn.execute('SELECT * FROM stock_profiles WHERE futu_code=?', (code,)).fetchone() if table(conn, 'stock_profiles') else None
    profile = dict(profile) if profile else {}
    q = dict(quote) if quote else None
    daily = {**summaries['yfinance'], **daily_state(code, q, now),
             'collected_at': q.get('synced_at') if q else None}
    quote_attempt = stock_attempt(conn, 'yfinance', code) or summaries['yfinance']
    daily.update({k: quote_attempt[k] for k in ('scope','last_attempt_at','last_success_at')})
    daily['attempt_status'] = quote_attempt['status']
    if daily['attempt_status'] != 'ok' and daily['status'] == 'current':
        daily['status'] = 'cached'
    details = {**summaries['yf_profile'], 'collected_at': profile.get('synced_at'),
               'data_as_of': None, 'has_cache': bool(profile.get('synced_at'))}
    profile_attempt = stock_attempt(conn, 'yf_profile', code)
    if profile_attempt:
        details.update(profile_attempt)
    if not details['has_cache']:
        details['status'] = 'empty'
    attempt = None
    migrated = 'snapshot_time_utc' in {r[1] for r in conn.execute('PRAGMA table_info(stock_profiles)')}
    if table(conn, 'collection_status'):
        attempt = conn.execute("SELECT * FROM collection_status WHERE source='futu_snapshot' AND code=?", (code,)).fetchone()
    attempt = dict(attempt) if attempt else {}
    snapshot = dict(source='futu_snapshot', scope='stock', status=attempt.get('status', 'unknown'),
                    last_attempt_at=attempt.get('last_attempt_at'), last_success_at=attempt.get('last_success_at'),
                    collected_at=profile.get('snap_synced_at'), data_as_of=profile.get('snapshot_time_raw'),
                    source_timezone=profile.get('snapshot_timezone'), source_time_utc=profile.get('snapshot_time_utc'),
                    reason=REASONS.get(attempt.get('reason')), migration_required=not migrated)
    values = {k: profile.get(k) for k in ('last_price','prev_close_price','open_price','high_price','low_price','volume_ratio','suspension','sec_status','lot_size','price_spread')}
    last, prev = values['last_price'], values['prev_close_price']
    snapshot['values'] = values
    snapshot['currency'] = 'HKD' if code.startswith('HK.') else 'USD'
    snapshot['change'] = last - prev if last is not None and prev is not None and prev > 0 else None
    snapshot['change_pct'] = snapshot['change'] / prev * 100 if snapshot['change'] is not None else None
    snapshot['has_cache'] = last is not None and bool(profile.get('snapshot_time_utc'))
    snapshot['freshness'] = 'unknown'
    try:
        current = sessions.utc(now)
        source = sessions.utc(profile['snapshot_time_utc'])
        observed = sessions.utc(profile['snap_synced_at'])
        if source > observed or observed > current:
            raise ValueError('future clock')
        zone = ZoneInfo(snapshot['source_timezone'])
        day = current.astimezone(zone).date().isoformat()
        dates = sessions.window(code, day, 2)
        active = next((sessions.session(code, d) for d in dates if sessions.session(code, d)['open'] <= current < sessions.session(code, d)['final_at']), None)
        closed = [sessions.session(code, d) for d in dates if sessions.session(code, d)['final_at'] <= current]
        cutoff = active['open'] if active else closed[-1]['open']
        # Cached quote time need not equal the closing auction time (suspensions,
        # illiquid securities). Show its exact time; no claim of real-time prices.
        snapshot['freshness'] = 'cached' if source >= cutoff else 'stale'
    except (ValueError, TypeError, KeyError, IndexError):
        pass
    return dict(code=code, daily=daily, profile=details, snapshot=snapshot)


def overview(conn, now):
    codes = set()
    for name in ('positions', 'orders', 'deals'):
        if table(conn, name):
            codes.update(r[0] for r in conn.execute(f'SELECT DISTINCT code FROM {name}') if valid_code(r[0] or ''))
    # Bounded response; aggregate collection status does not imply per-stock health.
    codes = sorted(codes)
    summaries = {s: aggregate(conn, s) for s in SOURCES}
    return dict(schema_version=1, sources=list(summaries.values()),
                stocks=[stock_status(conn, code, now, summaries) for code in codes[:400]], truncated=len(codes)>400)

"""One append-only prediction table, deterministic idempotency, explicit projection."""
import hashlib
import json
from pathlib import Path
from . import sessions

class PredictionConflict(ValueError): pass

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)

def digest(value): return hashlib.sha256(canonical(value).encode()).hexdigest()

def append(conn, rows, *, run_id, manifest_path=None, status='generated'):
    from .db import PRED_COLS
    count=0
    with conn:
        for row in rows:
            p=dict(row)
            target=p.get('target_session') or sessions.next_session(p['code'],p['as_of'])
            p['target_session']=target
            source=p.get('source') or 'unknown'
            identity=[run_id,p['code'],p['as_of'],target]
            pid=digest(identity); h=digest(p)
            old=conn.execute('select content_hash from ml_prediction_versions where prediction_id=?',(pid,)).fetchone()
            if old:
                if old[0]!=h: raise PredictionConflict('same run identity has different prediction content')
                continue
            rowstatus=p.get('status', status)
            if source in ('legacy','backfill','unknown'): rowstatus='audit_unknown_timing'
            if source=='recomputed': rowstatus='recomputed'
            if source=='live':
                try:
                    if not p.get('decision_at'): raise sessions.Unavailable('unknown_timestamp')
                    decision=sessions.utc(p['decision_at'])
                    if not p.get('generated_at') or sessions.utc(p['generated_at']) > decision: raise sessions.Unavailable('invalid_generation_time')
                    if decision < sessions.session(p['code'],p['as_of'])['final_at']: raise sessions.Unavailable('premature')
                    sessions.check_deadline(p['code'],target,decision)
                except (sessions.Unavailable,ValueError): rowstatus='audit_invalid_timing'
            if run_id.startswith('legacy-'): rowstatus='audit_unknown_timing'
            conn.execute('insert into ml_prediction_versions values (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (pid,run_id,p['code'],p['as_of'],target,source,rowstatus,p.get('generated_at'),
                          p.get('decision_at'),p.get('published_at'),str(manifest_path) if manifest_path else None,canonical(p),h))
            # Compatibility is deterministic: keep historical projection until a
            # validated live generation arrives; backfill never replaces live.
            cols=PRED_COLS
            vals=[p.get(k) for k in cols]
            conn.execute(f"insert or ignore into ml_predictions ({','.join(cols)}) values ({','.join('?' for _ in cols)})",vals)
            if source=='live' and rowstatus=='generated':
                updates=[c for c in cols if c not in ('code','as_of')]
                conn.execute(f"update ml_predictions set {','.join(c+'=?' for c in updates)} where code=? and as_of=?",
                             [p.get(c) for c in updates]+[p['code'],p['as_of']])
            count+=1
    return count

def migrate_legacy(conn):
    rows=[dict(r) for r in conn.execute('select * from ml_predictions order by code,as_of')]
    for p in rows:
        # Preserve original source in payload, including unverified historical live.
        append(conn,[p],run_id='legacy-'+digest(p),status='audit_unknown_timing')
    return len(rows)

def load(conn, code=None, *, include_audit=False, source=None):
    sql='select * from ml_prediction_versions where 1=1';params=[]
    if code: sql+=' and code=?';params.append(code)
    if not include_audit: sql+=" and status in ('generated','published','recomputed')"
    if source: sql+=' and source=?';params.append(source)
    sql+=' order by target_session,coalesce(decision_at,generated_at,\'\'),prediction_id'
    out=[]
    for row in conn.execute(sql,params):
        r=dict(row); payload=json.loads(r.pop('payload_json'));payload.update(r);out.append(payload)
    return out

def select_by_target(rows, *, allow_recomputed=False):
    selected={}
    for r in rows:
        if r['source']=='recomputed' and not allow_recomputed: continue
        if r['status'] not in ('generated','published','recomputed'):continue
        key=r['target_session']
        old=selected.get(key)
        if old and old['source']=='live' and r['source']!='live':continue
        selected[key]=r
    return selected

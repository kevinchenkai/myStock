"""Manual pipeline receipt; publish only this run's exact, unexpired artifact."""
import hashlib
import json
import os
from pathlib import Path
from . import config, sessions

def receipt_path():
    return Path(os.environ.get('MYSTOCK_ML_RECEIPT', str(config.ML_DIR/'pipeline-status.json')))

def write_data_status(rows, failures, warnings):
    # A data receipt must never replace or authorize a train artifact.
    p = receipt_path().with_suffix('.data.json')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(dict(run_id=os.environ.get('MYSTOCK_ML_RUN_ID'), phase='data',
                                status='data_failed' if failures else 'data_complete',
                                markets=rows, failures=failures, warnings=warnings), indent=2))
    return p

def write_status(rows, artifact, db_path=None, *, warnings=None):
    status = 'failed' if any(r['status']=='failed' for r in rows) else ('partial' if artifact and any(r['status']!='generated' for r in rows) else 'generated' if artifact else 'all_skipped')
    p=receipt_path(); p.parent.mkdir(parents=True,exist_ok=True)
    result=dict(run_id=os.environ.get('MYSTOCK_ML_RUN_ID'),status=status,markets=rows,warnings=warnings or [],
                artifact=str(Path(artifact).resolve()) if artifact else None,
                db_path=str(Path(db_path or config.ML_DB_PATH).resolve()),
                sha256=hashlib.sha256(Path(artifact).read_bytes()).hexdigest() if artifact else None)
    p.write_text(json.dumps(result,indent=2)); return result

def exit_code():
    return int(json.loads(receipt_path().read_text())['status']=='failed')

def validate_publish(path=None, now=None, run_id=None):
    r=json.loads(Path(path or receipt_path()).read_text())
    if r['status'] not in ('partial','generated') or not r.get('artifact'): raise ValueError('no current valid artifact')
    expected=run_id or os.environ.get('MYSTOCK_ML_RUN_ID')
    if expected and expected!=r['run_id']: raise ValueError('receipt belongs to another run')
    p=Path(r['artifact'])
    if hashlib.sha256(p.read_bytes()).hexdigest()!=r['sha256']: raise ValueError('artifact changed')
    for row in r['markets']:
        if row['status']=='generated': sessions.check_deadline(row['code'],row['target_session'],now)
    return p

def record_publication(path=None, now=None):
    """Call only after successful transfer. Preserve actual time, even if late."""
    from .db import get_ml_connection
    p=Path(path or receipt_path());r=json.loads(p.read_text());now=sessions.utc(now or sessions.utc_now())
    statuses=[]
    with get_ml_connection(r['db_path']) as c:
        for row in r['markets']:
            if row['status']!='generated':continue
            status='published' if now < sessions.session(row['code'],row['target_session'])['deadline'] else 'published_late'
            c.execute('update ml_prediction_versions set published_at=?,status=? where run_id=? and code=? and status=?',
                      (now.isoformat(),status,r['run_id'],row['code'],'generated'))
            statuses.append(status)
    r['published_at']=now.isoformat();r['publication_status']='published_late' if 'published_late' in statuses else 'published'
    p.write_text(json.dumps(r,indent=2))
    return r['publication_status']

if __name__=='__main__':
    import sys
    if '--record-published' in sys.argv: print(record_publication())
    else: print(validate_publish())

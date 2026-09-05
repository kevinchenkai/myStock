"""Manual pipeline receipt; publish only this run's exact, unexpired artifact."""
import hashlib
import json
import os
from pathlib import Path
from . import config, sessions

def receipt_path():
    return Path(os.environ.get('MYSTOCK_ML_RECEIPT', str(config.ML_DIR/'pipeline-status.json')))

def write_status(rows, artifact):
    status = 'failed' if any(r['status']=='failed' for r in rows) else ('partial' if artifact and any(r['status']!='generated' for r in rows) else 'generated' if artifact else 'all_skipped')
    p=receipt_path(); p.parent.mkdir(parents=True,exist_ok=True)
    result=dict(run_id=os.environ.get('MYSTOCK_ML_RUN_ID'),status=status,markets=rows,
                artifact=str(Path(artifact).resolve()) if artifact else None,
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

if __name__=='__main__':
    print(validate_publish())

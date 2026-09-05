"""Recoverable run manifest; input SQLite snapshot is private and ignored."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import uuid
from . import config, sessions
from .versions import canonical

def start(db_path=None, run_id=None, root=None, protocol=None):
    rid=run_id or os.environ.get('MYSTOCK_ML_RUN_ID') or uuid.uuid4().hex
    if not rid or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in rid):raise ValueError('unsafe run_id')
    dest=Path(root or config.ML_DIR/'runs')/rid;dest.mkdir(parents=True,exist_ok=False)
    source=Path(db_path or config.ML_DB_PATH).resolve(); snap=dest/'input.db'
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(snap) as out:src.backup(out)
    manifest=dict(run_id=rid,git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=config.ROOT_DIR,text=True).strip(),
                  git_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=config.ROOT_DIR,text=True)),
                  generated_at=sessions.utc_now().isoformat(),decision_at=None,published_at=None,status='running',seed=0,
                  calendar=sessions.CALENDAR_VERSION,protocol=protocol or 'session-guard-v1',
                  input_path=str(snap.resolve()),input_sha256=hashlib.sha256(snap.read_bytes()).hexdigest(),
                  source_path=str(source),dependencies={n:importlib.metadata.version(n) for n in ['numpy','pandas','lightgbm','scikit-learn']})
    from .features import FEATURE_COLS
    manifest['features']=FEATURE_COLS;manifest['model']='IntervalModel-legacy-capacity-explicit-bagging0'
    path=dest/'manifest.json';path.write_text(canonical(manifest));return manifest,path

def finish(manifest,path,rows,statuses):
    manifest.update(status='completed',predictions=[dict(code=p['code'],as_of=p['as_of'],target_session=p['target_session'],
                    decision_at=p.get('decision_at'),training_label_cutoff=p['as_of'],calibration_cutoff=p['as_of']) for p in rows],statuses=statuses)
    Path(path).write_text(canonical(manifest))

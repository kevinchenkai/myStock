"""Archive E0 development predictions as recomputed (never live).

Requires explicit destination and frozen input; output remains private under runs.
"""
import argparse
import json
from pathlib import Path
from mystock.ml import config,db,runs,versions

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--input',required=True);p.add_argument('--matrix',required=True);a=p.parse_args()
    source=Path(a.matrix);raw=json.loads((source/'predictions.json').read_text())
    db.init_ml_db(a.db);m,mp=runs.start(a.input,protocol='development-120-session-v1');rows=[]
    m['evaluation_protocol']=json.loads((source/'protocol.json').read_text())
    m['folds_and_metrics']=json.loads((source/'metrics.json').read_text())
    for code in config.TARGETS:
        for r in raw[f'{code}|E0_old|0']:
            rows.append(dict(code=code,as_of=r['as_of'],target_session=r['target_session'],close=r['close'],
                             l_hat=r['close']*(1+r['clo']),h_hat=r['close']*(1+r['chi']),raw_low=r['lo'],raw_high=r['hi'],
                             source='recomputed',generated_at=m['generated_at'],decision_at=None,protocol='development-120-session-v1'))
    with db.get_ml_connection(a.db) as c:n=versions.append(c,rows,run_id=m['run_id'],manifest_path=mp.resolve())
    runs.finish(m,mp,rows,[{'status':'offline_recomputed'}]);print(f'{n} recomputed versions; manifest {mp}')
if __name__=='__main__':main()

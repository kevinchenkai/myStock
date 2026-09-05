"""Fixed synthetic scenarios over isolated offline inputs. Aggregate output only."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time
import numpy as np
from mystock.ml import service,config,sessions
from mystock.ml.execution import Scenario

def main():
    a=argparse.ArgumentParser();a.add_argument('--db',required=True);a.add_argument('--out',required=True);args=a.parse_args()
    summaries=[];now=sessions.utc('2026-09-05T06:00:00Z')
    for code in config.TARGETS:
        for days in [20,60,120]:
            for fee in [0,10]:
                hk=code.startswith('HK.')
                s=Scenario(20000,0,100 if hk else 10,500 if hk else 50,20,lot_size=100 if hk else 1,
                           fee_bps=fee,fee_flat=1 if fee else 0,parameter_source='synthetic_fixture',rules_source='synthetic_HK100_US1')
                r=service.compare(args.db,[code],days,s,'2026-09-03',allow_recomputed=True,now=now)
                for p in r['results'][0]['policies']:
                    summaries.append(dict(code=code,currency='HKD' if hk else 'USD',days=days,fee_bps=fee,
                                          policy=p['policy'],input_version=r['results'][0]['input_version'],scenario=asdict(s),**p['summary']))
    # Content read + preparation + all three policies included in wall time.
    service._CACHE.clear();service._REVIEW_CACHE.clear();times=[]
    for i in range(11):
        t=time.perf_counter();service.compare(args.db,config.TARGETS,120,Scenario(20000,0,10,50,20,fee_bps=10,fee_flat=1,parameter_source='synthetic_performance_fixture'),
                                             '2026-09-03',allow_recomputed=True,now=now);times.append(time.perf_counter()-t)
    result=dict(summaries=summaries,performance=dict(cold=times[0],warm_p95=float(np.percentile(times[1:],95)),samples=times))
    Path(args.out).write_text(json.dumps(result,indent=2,allow_nan=False));print(json.dumps(result['performance']))
if __name__=='__main__':main()

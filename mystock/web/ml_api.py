"""Read-only v2 contracts; scenario inputs never imply a real account budget."""
import sqlite3
from flask import Blueprint,request,jsonify,current_app
from ..ml import service, config, sessions
from ..ml.execution import Scenario
bp=Blueprint('ml_v2',__name__)

def path():return current_app.config.get('ML_DB_PATH',config.ML_DB_PATH)

def args():
    codes=request.args.get('codes','').split(',');days=int(request.args.get('days','60'))
    codes=service.validate(codes,days)
    source=request.args.get('source','live')
    if source not in ('live','recomputed'):raise ValueError('invalid source')
    end=request.args.get('end') or None
    if end:
        from datetime import date
        if date.fromisoformat(end).isoformat()!=end:raise ValueError('invalid end')
    return codes,days,end,source=='recomputed'

def scenario():
    required=['initial_cash','initial_inventory','order_qty','max_inventory','max_holding','lot_size']
    missing=[k for k in required if k not in request.args]
    if missing:raise ValueError('scenario parameters required: '+','.join(missing))
    values={k:float(request.args[k]) for k in required}
    for k in ['order_qty','max_inventory','max_holding','lot_size']:
        if values[k]!=int(values[k]):raise ValueError(k+' must be integer')
        values[k]=int(values[k])
    for k in ['fee_bps','fee_flat','initial_price','tick_size']:
        if request.args.get(k):values[k]=float(request.args[k])
    values['parameter_source']=request.args.get('parameter_source','user_input')
    return Scenario(**values).validate()

@bp.errorhandler(ValueError)
def bad(e):return jsonify(schema_version=2,error=str(e)),400
@bp.errorhandler(FileNotFoundError)
def missing(e):return jsonify(schema_version=2,error=str(e)),503
@bp.errorhandler(sqlite3.OperationalError)
def unavailable(e):return jsonify(schema_version=2,error='ML database/schema unavailable'),503

@bp.route('/api/ml/v2/latest')
def latest():
    codes,_,_,recomputed=args();return jsonify(service.latest(path(),codes,allow_recomputed=recomputed))

@bp.route('/api/ml/v2/review')
def review():
    codes,days,end,recomputed=args()
    results=[service.review(path(),code,days,end,allow_recomputed=recomputed) for code in codes]
    selected=request.args.get('selected')
    for r in results:
        for row in r['rows']:
            if row['date']==selected:row['orders']=service.facts(path(),r['code'],selected,row['prediction'])
    return jsonify(schema_version=2,results=results)

@bp.route('/api/ml/v2/compare')
def compare():
    codes,days,end,recomputed=args();return jsonify(service.compare(path(),codes,days,scenario(),end,allow_recomputed=recomputed))

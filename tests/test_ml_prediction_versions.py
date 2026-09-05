import sqlite3
import pytest
from mystock.ml import db, versions as v

def pred(value=90):
    return dict(code='US.NVDA',as_of='2026-09-04',target_session='2026-09-08',close=100,l_hat=value,h_hat=110,
                source='live',generated_at='2026-09-04T22:00:00Z',decision_at='2026-09-04T22:00:00Z')

def test_append_idempotent_conflict_and_no_replace(tmp_path):
    p=tmp_path/'ml.db';db.init_ml_db(p)
    with db.get_ml_connection(p) as c:
        assert v.append(c,[pred()],run_id='a')==1
        assert v.append(c,[pred()],run_id='a')==0
        with pytest.raises(v.PredictionConflict):v.append(c,[pred(89)],run_id='a')
        v.append(c,[pred(88)],run_id='b')
        assert len(v.load(c))==2
        r=pred(70);r['source']='recomputed';v.append(c,[r],run_id='c')
        assert c.execute('select l_hat from ml_predictions').fetchone()[0]==88
        with pytest.raises(sqlite3.IntegrityError):c.execute('delete from ml_prediction_versions')
    with db.get_ml_connection_readonly(p) as c:
        with pytest.raises(sqlite3.OperationalError):c.execute('delete from ml_predictions')

def test_migrate_unknown_live_preserves_content(tmp_path):
    p=tmp_path/'ml.db';db.init_ml_db(p)
    with db.get_ml_connection(p) as c:
        c.execute("insert into ml_predictions(code,as_of,close,l_hat,h_hat,source,generated_at) values ('US.NVDA','2026-09-04',100,90,110,'live','2026-09-04 12:00:00')")
        c.commit();v.migrate_legacy(c);v.migrate_legacy(c)
        assert v.load(c)==[]
        rows=v.load(c,include_audit=True);assert len(rows)==1
        assert rows[0]['generated_at']=='2026-09-04 12:00:00'

def test_metrics_and_cv_tail():
    import numpy as np
    from mystock.ml.evaluation import skill,scale,metrics
    from mystock.ml.cv import purged_walk_forward, PurgedConfig
    folds=purged_walk_forward(391,PurgedConfig(n_folds=4,min_train=250))
    assert [i for _,te in folds for i in te]==list(range(272,391))
    assert skill(0,0) is None and skill(float('nan'),1) is None
    assert np.isfinite(scale([0,float('inf'),float('nan')])).all()
    m=metrics([-.02],[.02],[-.01],[.01],[-.05],[.05],[.2,.8])
    assert m['coverage']==1 and m['raw_coverage']==0 and m['pinball_low']==pytest.approx(.008)

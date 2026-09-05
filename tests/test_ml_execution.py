import pytest
from mystock.ml.execution import Scenario,replay

def s(**kw):
    p=dict(initial_cash=100,initial_inventory=0,order_qty=1,max_inventory=2,max_holding=20,lot_size=1,fee_bps=0,parameter_source='synthetic_fixture');p.update(kw);return Scenario(**p)

def day(date,price,lo=100,hi=110,**kw):
    bar=dict(ts_et=date+' 09:30:00',open=price,high=price,low=price,close=price)
    return dict(date=date,status='ok',daily=dict(close=price,dividends=kw.get('dividends',0),splits=kw.get('splits',0)),bars=[bar],prediction=dict(l_hat=lo,h_hat=hi,close=100))

def test_hand_cash_peak_and_conservation():
    r=replay([day('2026-09-01',100),day('2026-09-02',110)],s())
    q=r['summary'];assert q['equity']==110 and q['peak_cash_used']==100 and q['peak_reserved_cash']==100
    assert len(r['rounds'])==1 and not r['open_lots']
    assert r['rows'][0]['cash']==0 and r['rows'][0]['inventory']==1

def test_no_naked_short_or_unfunded_buy_gap():
    r=replay([day('2026-09-01',120)],s());assert r['summary']['inventory']==0
    r=replay([day('2026-09-01',90)],s(initial_cash=50));assert r['summary']['cash']==50
    r=replay([day('2026-09-01',90)],s());assert r['summary']['cash']==10
    assert r['rows'][0]['events'][-1]['price']==90

def test_fees_and_missing_fee_label():
    r=replay([day('2026-09-01',100),day('2026-09-02',110)],s(initial_cash=102,fee_bps=10,fee_flat=1))
    assert r['summary']['equity']==pytest.approx(109.79)
    assert r['summary']['fees']==pytest.approx(2.21)
    assert replay([],s(fee_bps=None))['summary']['fee_status']=='gross_fees_missing'

def test_pending_position_timeout_no_close_cheat():
    a=day('2026-09-01',100);b=day('2026-09-02',95);b['prediction']=None;b['bars']=[];b['status']='missing_bars'
    c=day('2026-09-03',96);c['daily']['close']=130;c['prediction']=None
    r=replay([a,b,c],s(max_holding=1))
    assert r['rows'][1]['inventory']==1
    assert r['rounds'][0]['gross_pnl']==-4

def test_lots_unique_and_actions():
    a=day('2026-09-01',100);b=day('2026-09-02',50,splits=2,dividends=1);b['prediction']=None
    r=replay([a,b],s())
    assert r['summary']['inventory']==2 and r['summary']['receivable']==2
    assert r['summary']['equity']==102 and r['summary']['cash']==0
    assert len(r['open_lots'])==1

def test_same_bar_ambiguous_reserved_inventory():
    d=day('2026-09-01',100);d['bars'][0].update(high=120,low=80)
    r=replay([d],s(initial_cash=100,initial_inventory=1,initial_price=100,allow_add=True))
    assert r['rows'][0]['ambiguity']
    assert [e['side'] for e in r['rows'][0]['events'] if e['kind']=='fill']==['SELL','BUY']
    assert r['summary']['inventory']==1

def test_restarts_differ_from_continuous_slice():
    days=[day('2026-09-01',100),day('2026-09-02',110)]
    assert replay(days,s())['summary']['equity']==110
    assert replay(days[1:],s())['summary']['equity']==100

@pytest.mark.parametrize('kw',[dict(initial_cash=float('nan')),dict(order_qty=2,lot_size=3),dict(initial_inventory=1),dict(fee_bps=-1)])
def test_invalid_scenarios(kw):
    with pytest.raises(ValueError):replay([],s(**kw))


def test_expired_and_multi_buy_unique_exits():
    a=day('2026-09-01',105);a['bars'][0]['ts_et']='2026-09-02 09:30:00';a['bars'][0].update(low=80,high=120)
    r=replay([a],s());assert r['summary']['inventory']==0 and any(e['kind']=='expired' for e in r['rows'][0]['events'])
    days=[day('2026-09-01',100),day('2026-09-02',100),day('2026-09-03',110),day('2026-09-04',110)]
    r=replay(days,s(initial_cash=300,allow_add=True))
    assert sum(x['qty'] for x in r['rounds'])==2
    assert r['summary']['inventory']==0

import pandas as pd
import pytest
from mystock.ml import sessions as s
from mystock.ml.fetch import _ohlc_ok

def bar(day, stamp):
    return dict(date=day,open=100,high=110,low=90,close=102,adj_close=102,volume=100,synced_at=stamp)

def test_calendar_holidays_dst_halfday_lunch():
    assert s.next_session('US.NVDA','2026-09-04')=='2026-09-08'
    assert s.session('US.NVDA','2026-11-27')['close'].hour==18
    assert s.session('US.NVDA','2026-03-06')['open'].hour==14
    assert s.session('US.NVDA','2026-03-09')['open'].hour==13
    assert s.state('HK.00700',s.utc('2026-09-04T04:30:00Z'))['status']=='skipped_in_session'
    assert s.session('HK.00700','2026-12-24')['final_at'].hour==4
    with pytest.raises(s.Unavailable):s.session('US.NVDA','2027-01-04')

def test_stale_and_intraday_not_final():
    b=bar('2026-09-04','2026-09-04T18:00:00Z')
    assert not s.daily_final('US.NVDA',b,s.utc('2026-09-04T22:00:00Z'))
    with pytest.raises(s.Unavailable,match='awaiting_final_data'):
        s.prepare_daily(pd.DataFrame([b]),'US.NVDA',s.utc('2026-09-04T22:00:00Z'))
    b['synced_at']='2026-09-04T20:06:00Z'
    assert s.daily_final('US.NVDA',b,s.utc('2026-09-04T22:00:00Z'))

def test_gap_preserved_and_deadline():
    df=pd.DataFrame([bar('2026-09-01','2026-09-04T23:00:00Z'),bar('2026-09-03','2026-09-04T23:00:00Z')])
    out=s.prepare_daily(df,'US.NVDA',s.utc('2026-09-04T23:00:00Z'),live=False)
    assert list(out.date)==['2026-09-01','2026-09-02','2026-09-03']
    assert pd.isna(out.iloc[1]['close'])
    with pytest.raises(s.Unavailable,match='missed_deadline'):
        s.check_deadline('US.NVDA','2026-09-08',s.utc('2026-09-08T13:30:00Z'))

@pytest.mark.parametrize('key,value',[('high',float('inf')),('open',float('nan')),('low',111),('open',120),('close',80),('close',0)])
def test_invalid_ohlc(key,value):
    r=bar('2026-09-04','');r[key]=value;assert not _ohlc_ok(r)

def test_hourly_only_complete_and_no_lunch():
    r=bar('2026-09-04','');r['ts_utc']='2026-09-04 03:30:00'
    assert not s.hourly_final('HK.00700',r,s.utc('2026-09-04T03:45:00Z'))
    assert s.hourly_final('HK.00700',r,s.utc('2026-09-04T04:00:00Z'))
    r['ts_utc']='2026-09-04 04:30:00';assert not s.hourly_final('HK.00700',r,s.utc('2026-09-04T06:00:00Z'))

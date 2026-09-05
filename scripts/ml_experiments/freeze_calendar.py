"""Run in isolated tool env with pmc 5.1.3 and exchange-calendars 4.11.1.

HKEX half-days use XHKG (PMC 5.1.3 omits 2026 Christmas Eve half-day).
Static output intentionally expires at 2026-12-31. Review annual exchange notices.
"""
from pathlib import Path
import pandas as pd
import pandas_market_calendars as pmc
import exchange_calendars as xc

def main():
    for market in ['US','HK']:
        if market=='US':
            s=pmc.get_calendar('NYSE').schedule('2020-01-01','2026-12-31').rename(columns={'market_open':'open','market_close':'close'})
        else:
            s=xc.get_calendar('XHKG',start='2020-01-01',end='2026-12-31').schedule
            # Confirmed HKEX full-day weather closures, see calendars/README.md.
            s=s.loc[~s.index.strftime('%Y-%m-%d').isin(['2023-09-01', '2023-09-08'])]
        o=pd.DataFrame(index=s.index)
        o['date']=s.index.strftime('%Y-%m-%d');o['open']=s.open;o['close']=s.close
        o['deadline']=s.open-pd.Timedelta(minutes=30 if market=='HK' else 0)
        o['final_at']=s.close+pd.Timedelta(minutes=15 if market=='HK' else 5)
        for col in ['break_start','break_end']:o[col]=s[col] if col in s else ''
        o.to_csv(Path(__file__).resolve().parents[2]/'mystock/ml/calendars'/f'{market}.csv',index=False)
if __name__=='__main__':main()

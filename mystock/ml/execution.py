"""Pure cash/inventory event replay. No IO, fitting, Web, or broker calls.

Orders reserve cash/inventory at submission; DAY expiry. Bar ambiguity is explicit.
Initial-lot sells precede buys in ambiguous bars; no sale of unreserved inventory.
"""
from dataclasses import dataclass, asdict
import math
from .rules import round_quote

@dataclass(frozen=True)
class Scenario:
    initial_cash: float
    initial_inventory: float
    order_qty: int
    max_inventory: int
    max_holding: int
    lot_size: int = 1
    tick_size: float | None = None
    fee_bps: float | None = None
    fee_flat: float = 0
    initial_price: float | None = None
    allow_add: bool = False
    parameter_source: str = 'user_input'
    rules_source: str = 'explicit_approximation'

    def validate(self):
        for k in ('initial_cash','initial_inventory','order_qty','max_inventory','max_holding','lot_size','fee_flat'):
            v=getattr(self,k)
            if not isinstance(v,(int,float)) or not math.isfinite(v) or v<0:raise ValueError(f'invalid {k}')
        for k in ('order_qty','max_inventory','max_holding','lot_size'):
            if getattr(self,k)!=int(getattr(self,k)) or getattr(self,k)<=0:raise ValueError(f'invalid {k}')
        if self.order_qty%self.lot_size or self.max_inventory%self.lot_size:raise ValueError('quantity must be multiple of security lot')
        if self.initial_inventory>self.max_inventory:raise ValueError('initial inventory exceeds cap')
        if self.initial_inventory and (self.initial_price is None or not math.isfinite(self.initial_price) or self.initial_price<=0):raise ValueError('initial_price required for existing inventory')
        if self.fee_bps is not None and (not math.isfinite(self.fee_bps) or self.fee_bps<0):raise ValueError('invalid fees')
        if self.tick_size is not None and (not math.isfinite(self.tick_size) or self.tick_size<=0):raise ValueError('invalid tick')
        if not 1<=self.max_holding<=400:raise ValueError('max_holding out of range')
        return self

def fee(price,qty,s):return price*qty*(s.fee_bps or 0)/10000+s.fee_flat

def fill_price(order,bar):
    if order['side']=='BUY' and bar['low']<=order['price']:return min(bar['open'],order['price'])
    if order['side']=='SELL' and bar['high']>=order['price']:return max(bar['open'],order['price'])
    return None

def replay(days,scenario:Scenario,*,policy='boundary_inventory',offset=.02):
    s=scenario.validate()
    if policy not in ('boundary_inventory','naive_vol_inventory','fixed_offset_inventory'):raise ValueError('invalid policy')
    if not math.isfinite(offset) or not 0<offset<1:raise ValueError('invalid offset')
    cash=float(s.initial_cash);pos=float(s.initial_inventory);receivable=0.;fees=0.
    initial=cash+pos*(s.initial_price or 0);min_cash=cash;peak_reserved=0.;peak_inventory=pos*(s.initial_price or 0)
    lots=[dict(qty=pos,entry_price=s.initial_price,entry_day=None,age=0,entry_fee=0.)] if pos else []
    rows=[];events=[];rounds=[];peak_equity=initial;max_dd=0.;last_mark=s.initial_price
    previous=None
    for day in days:
        date=day['date']
        if previous is not None and date<=previous:raise ValueError('sessions must be unique ascending')
        previous=date
        daily=day.get('daily'); bars=day.get('bars') or [];pred=day.get('prediction'); orders=[]
        rowevents=[]
        def event(kind,**values):
            e=dict(date=date,kind=kind,**values);events.append(e);rowevents.append(e)
        for lot in lots:lot['age']+=1
        if daily:
            split=daily.get('splits') or 0
            if split and split!=1:
                pos*=split
                for lot in lots:lot['qty']*=split;lot['entry_price']/=split
                if last_mark:last_mark/=split
                event('split',ratio=split,inventory=pos)
            dividend=daily.get('dividends') or 0
            if dividend and pos:
                # Ex-date accrual is equity, not spendable cash without pay-date evidence.
                receivable+=pos*dividend;event('dividend_receivable',amount=pos*dividend)
        reserved_cash=0.;reserved_qty=0.
        def submit(side,price,qty,reason='boundary'):
            nonlocal reserved_cash,reserved_qty,peak_reserved
            if qty<=0 or not math.isfinite(price) or price<=0:return
            price=round_quote(price,s.tick_size,side)
            needed=qty*price+fee(price,qty,s) if side=='BUY' else 0
            reject=None
            if side=='SELL' and qty>pos-reserved_qty+1e-8:reject='insufficient_inventory'
            if side=='BUY' and (needed>cash-reserved_cash+1e-8 or pos+sum(o['qty'] for o in orders if o['side']=='BUY')+qty>s.max_inventory):reject='insufficient_cash_or_cap'
            if reject:event('rejected',side=side,reason=reject,qty=qty);return
            order=dict(side=side,price=price,qty=qty,reason=reason,reserved=needed,valid_from=date,expires=date,filled=False)
            orders.append(order);reserved_cash+=needed;reserved_qty+=qty if side=='SELL' else 0
            peak_reserved=max(peak_reserved,reserved_cash)
            event('submitted',side=side,price=price,qty=qty,reserved_cash=reserved_cash,reserved_inventory=reserved_qty)
        due=sum(lot['qty'] for lot in lots if lot['age']>=s.max_holding)
        # Exit planned by holding-age at session start, filled at the first observed
        # regular-session open only when a complete session bar set is available.
        if due and bars:submit('SELL',float(bars[0]['open']),due,'scheduled_open_exit')
        lo=hi=None
        if pred:
            if policy=='boundary_inventory':lo,hi=pred.get('l_hat'),pred.get('h_hat')
            elif policy=='naive_vol_inventory':lo,hi=pred.get('naive_low'),pred.get('naive_high')
            else:
                base=pred.get('close');lo=base*(1-offset) if base else None;hi=base*(1+offset) if base else None
        if pos-reserved_qty>0 and hi:submit('SELL',hi,min(s.order_qty,pos-reserved_qty))
        if (pos==0 or s.allow_add) and lo:submit('BUY',lo,s.order_qty)
        ambiguous=False
        for bar in bars:
            if bar.get('ts_et') and bar['ts_et'][:10] != date:
                event('out_of_session_bar_ignored');continue
            from .sessions import ohlc_ok
            if not ohlc_ok(bar):event('invalid_bar');continue
            touched=[o for o in orders if not o['filled'] and fill_price(o,bar) is not None]
            if len({o['side'] for o in touched})>1:
                ambiguous=True;event('same_bar_ambiguity',timestamp=bar.get('ts_et'),assumption='reserved_inventory_sell_first')
            for o in sorted(touched,key=lambda o:o['side']=='BUY'):
                price=fill_price(o,bar);qty=o['qty'];cost=fee(price,qty,s)
                if o['reason']=='scheduled_open_exit':price=float(bar['open']);cost=fee(price,qty,s)
                if o['side']=='BUY':
                    cash-=qty*price+cost;pos+=qty;reserved_cash-=o['reserved']
                    lots.append(dict(qty=qty,entry_price=price,entry_day=date,age=0,entry_fee=cost))
                else:
                    cash+=qty*price-cost;pos-=qty;reserved_qty-=qty
                    left=qty
                    while left>1e-8:
                        lot=lots[0];take=min(left,lot['qty']);entry_fee=lot['entry_fee']*take/lot['qty']
                        rounds.append(dict(entry_day=lot['entry_day'],exit_day=date,qty=take,
                                           gross_pnl=take*(price-lot['entry_price']),fees=entry_fee+cost*take/qty,reason=o['reason']))
                        lot['entry_fee']-=entry_fee;lot['qty']-=take;left-=take
                        if lot['qty']<1e-8:lots.pop(0)
                fees+=cost;o['filled']=True;min_cash=min(min_cash,cash);peak_inventory=max(peak_inventory,pos*price)
                if cash < -1e-7 or pos < -1e-7:raise AssertionError('account invariant violated')
                event('fill',side=o['side'],price=price,qty=qty,fee=cost,cash=cash,inventory=pos,timestamp=bar.get('ts_et'),reason=o['reason'])
        for o in orders:
            if not o['filled']:event('expired',side=o['side'],price=o['price'],qty=o['qty'])
        status=day.get('status','ok')
        if daily and daily.get('close') and math.isfinite(daily['close']):last_mark=daily['close']
        mark_stale=not bool(daily)
        equity=cash+pos*last_mark+receivable if last_mark is not None else (cash+receivable if pos==0 else None)
        if equity is not None:
            peak_equity=max(peak_equity,equity);max_dd=max(max_dd,1-equity/peak_equity if peak_equity else 0)
            if last_mark:peak_inventory=max(peak_inventory,pos*last_mark)
        rows.append(dict(date=date,status=status,as_of=pred.get('as_of') if pred else None,
                         prediction_id=pred.get('prediction_id') if pred else None,cash=cash,inventory=pos,
                         receivable=receivable,equity=equity,mark=last_mark,mark_stale=mark_stale,
                         buy_quote=next((o['price'] for o in orders if o['side']=='BUY'),None),
                         sell_quote=next((o['price'] for o in orders if o['side']=='SELL'),None),
                         ambiguity=ambiguous,events=rowevents))
    end=rows[-1]['equity'] if rows else initial
    return dict(schema_version=2,mode='inventory',policy=policy,scenario=asdict(s),rows=rows,rounds=rounds,
                open_lots=lots,summary=dict(initial_equity=initial,equity=end,pnl=end-initial if end is not None else None,
                    return_pct=(end/initial-1)*100 if initial and end is not None else None,
                    fees=fees,fee_status='net_synthetic_or_user_profile' if s.fee_bps is not None else 'gross_fees_missing',
                    peak_cash_used=s.initial_cash-min_cash,peak_reserved_cash=peak_reserved,peak_inventory_value=peak_inventory,
                    max_drawdown=max_dd,inventory=pos,cash=cash,receivable=receivable,
                    ambiguous_sessions=sum(r['ambiguity'] for r in rows),missing_sessions=sum(r['status']!='ok' for r in rows)))

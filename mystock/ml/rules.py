"""Security rules are observations, separate from strategy order size.

Unknown/historical rules require an explicit approximation; no volume inference.
"""
from dataclasses import dataclass, asdict
from datetime import date
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

@dataclass(frozen=True)
class SecurityRule:
    lot_size: int | None = None
    tick_size: float | None = None
    source: str = 'unknown'
    effective_from: str | None = None
    observed_at: str | None = None
    approximate: bool = True

    def at(self, day):
        if not self.effective_from or day < self.effective_from:
            return SecurityRule()
        return self

def from_snapshot(row):
    return SecurityRule(row.get('lot_size'),row.get('price_spread'),
                        'futu_snapshot',row.get('rules_effective_from'),
                        row.get('snap_synced_at'),True)

def round_quote(price, tick, side):
    if tick is None: return float(price)
    return float((Decimal(str(price))/Decimal(str(tick))).to_integral_value(
        rounding=ROUND_FLOOR if side=='BUY' else ROUND_CEILING)*Decimal(str(tick)))

def read_rule(code, db_path, day):
    from .db import get_prod_connection_readonly
    with get_prod_connection_readonly(db_path) as c:
        cols={r[1] for r in c.execute('pragma table_info(stock_profiles)')}
        if not {'lot_size','price_spread','rules_effective_from'} <= cols: return SecurityRule()
        row=c.execute('select * from stock_profiles where futu_code=?',(code,)).fetchone()
    return from_snapshot(dict(row)).at(day) if row else SecurityRule()

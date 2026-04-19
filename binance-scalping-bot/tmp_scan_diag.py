from collections import Counter
from app.services.pump_scanner_service import PumpScannerService
svc = PumpScannerService()
ranked = svc._ranked_symbols(max_symbols=10)
print('ranked', len(ranked))
passed=[]
score_low=0
target_fail=0
analysis_fail=0
stages=Counter()
sides=Counter()
sigtypes=Counter()
for symbol,ticker in ranked:
    try:
        row = svc.analyze_symbol(symbol=symbol, ticker=ticker, include_candles=False)
    except Exception as e:
        analysis_fail += 1
        print('analysis_fail_symbol', symbol, repr(e))
        continue
    score=float(row.get('effective_score') or row.get('pump_score') or 0.0)
    stages[str(row.get('stage') or '?')] += 1
    sides[str(row.get('signal_side') or '?')] += 1
    sigtypes[str(row.get('signal_type') or '?')] += 1
    if score < 58:
        score_low += 1
        continue
    mark=float(row.get('mark_price') or 0.0)
    target=float(row.get('est_liq_target_price') or 0.0)
    side=str(row.get('signal_side') or '').upper()
    ok = (target > 0 and mark > 0 and ((side=='SHORT' and target < mark) or (side!='SHORT' and target > mark)))
    if not ok:
        target_fail += 1
        continue
    passed.append({k: row.get(k) for k in ['symbol','signal_type','signal_label','stage','signal_side','effective_score','execution_mode','entry_ready','continuation_risk','est_liq_target_price','mark_price','estimated_tp_pct','follow_through_score','entry_stretch_pct','spread_bps','risk_pct']})
print('analysis_fail', analysis_fail)
print('score_low', score_low)
print('target_fail', target_fail)
print('passed', len(passed))
print('stages', stages.most_common(12))
print('sides', sides)
print('sigtypes', sigtypes.most_common(12))
for row in passed[:10]:
    print(row)

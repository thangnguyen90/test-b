import sys
import os

sys.path.append("/home/thangnguyen/project/test-b/binance-scalping-bot/backend")

try:
    from app.services.risk_manager import calc_dynamic_take_profit
    
    # Test case 1: Very small ATR (calm market) -> should be capped by MIN margin
    # Entry: 1000, Leverage: 10, Min_margin: 5%
    # Margin req for $1000 with 10x is $100. 5% profit = $5.
    # Price difference should be +$5 for LONG.
    tp1 = calc_dynamic_take_profit("LONG", 1000.0, 0.1, 10, 2.0, 5.0, 10.0)
    print(f"Calm TP: {tp1} (Expected 1005.0)")
    
    # Test case 2: Very high ATR (volatile market) -> should be capped by MAX margin
    # Entry: 1000, Leverage: 10, Max_margin: 10%
    # Margin req: $100. 10% profit = $10.
    # Price difference should be -$10 for SHORT.
    tp2 = calc_dynamic_take_profit("SHORT", 1000.0, 50.0, 10, 2.0, 5.0, 10.0)
    print(f"Volatile TP: {tp2} (Expected 990.0)")
    
    # Test case 3: Normal ATR (between bounds)
    # Entry: 1000, ATR: 4.0, Multiplier: 2.0 -> Range: 8.0
    # $8 is between 5% ($5) and 10% ($10) of margin.
    tp3 = calc_dynamic_take_profit("LONG", 1000.0, 4.0, 10, 2.0, 5.0, 10.0)
    print(f"Normal TP: {tp3} (Expected 1008.0)")

except Exception as e:
    print(f"Error: {e}")

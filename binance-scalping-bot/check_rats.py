import asyncio
import sys

sys.path.append("/home/thangnguyen/project/test-b/binance-scalping-bot/backend")

from app.core.config import settings
from app.services.mysql_trade_repo import MySQLTradeRepository

async def main():
    repo = MySQLTradeRepository(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
    )
    
    with repo._get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT status, pnl_usdt, close_reason FROM paper_trades WHERE symbol LIKE '%1000RATS%'")
            rows = cursor.fetchall()
            
            wins = 0
            losses = 0
            open_trades = 0
            close_reasons = {}
            total = len(rows)
            for r in rows:
                if r['status'] == 'CLOSED':
                    pnl = float(r['pnl_usdt']) if r['pnl_usdt'] is not None else 0.0
                    reason = r['close_reason'] or 'Unknown'
                    close_reasons[reason] = close_reasons.get(reason, 0) + 1
                    
                    if pnl > 0:
                        wins += 1
                    elif pnl <= 0:  # Count 0 or negative as loss/scratch for simplicity
                        losses += 1
                else:
                    open_trades += 1
            print(f"Total trades for 1000RATS: {total}")
            print(f"Wins: {wins}")
            print(f"Losses: {losses}")
            print(f"Open: {open_trades}")
            print(f"Close Reasons: {close_reasons}")
            
            # Get latest 5 trades
            cursor.execute("SELECT symbol, side, status, pnl_usdt, pnl_pct, close_reason FROM paper_trades WHERE symbol LIKE '%1000RATS%' ORDER BY id DESC LIMIT 5")
            latest = cursor.fetchall()
            print("\nLatest 5 trades:")
            for l in latest:
                print(l)

if __name__ == "__main__":
    asyncio.run(main())

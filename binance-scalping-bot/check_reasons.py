import sys
import os

sys.path.append(os.path.abspath('backend'))
sys.path.append(os.path.abspath('.'))

try:
    from app.core.config import settings
    from app.services.mysql_trade_repo import MySQLTradeRepository
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

def main():
    repo = MySQLTradeRepository(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
    )

    query = """
    SELECT symbol, side, close_reason, COUNT(*) as cnt, AVG(pnl_pct) as avg_pnl
    FROM paper_trades
    WHERE result = 0 AND symbol IN ('AGT/USDT:USDT', 'AIXBT/USDT:USDT', 'BAS/USDT:USDT', '1000SATS/USDT:USDT')
    GROUP BY symbol, side, close_reason
    ORDER BY symbol, cnt DESC
    """

    with repo._get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
            print(f"{'SYMBOL':<20} | {'SIDE':<5} | {'REASON':<20} | {'COUNT':<5} | {'AVG PNL %':<10}")
            print("-" * 70)
            for r in rows:
                print(f"{r['symbol']:<20} | {r['side']:<5} | {r['close_reason']:<20} | {r['cnt']:<5} | {r['avg_pnl']:.2f}%")

if __name__ == "__main__":
    main()

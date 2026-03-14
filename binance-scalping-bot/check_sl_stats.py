import asyncio
import sys

sys.path.append("/home/thangnguyen/project/test-b/binance-scalping-bot/backend")

try:
    from app.core.config import settings
    from app.services.mysql_trade_repo import MySQLTradeRepository
except ImportError as e:
    print(f"Failed to import app modules: {e}")
    sys.exit(1)

def main():
    try:
        repo = MySQLTradeRepository(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            database=settings.mysql_database,
        )
        
        query = """
        SELECT symbol, side, COUNT(*) as total_trades,
               SUM(CASE WHEN result = 1 THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result = 0 THEN 1 ELSE 0 END) as losses,
               SUM(CASE WHEN close_reason = 'STOP_LOSS' THEN 1 ELSE 0 END) as sl_hits
        FROM paper_trades
        WHERE status = 'CLOSED'
        GROUP BY symbol, side
        HAVING sl_hits > 0
        ORDER BY sl_hits DESC, total_trades DESC
        LIMIT 20
        """
        
        print("-" * 80)
        print(f"{'SYMBOL':<15} | {'SIDE':<5} | {'TOTAL':<6} | {'WINS':<5} | {'LOSSES':<6} | {'SL HITS':<8}")
        print("-" * 80)

        with repo._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                rows = cursor.fetchall()
                for r in rows:
                    print(f"{r['symbol']:<15} | {r['side']:<5} | {r['total_trades']:<6} | {r['wins']:<5} | {r['losses']:<6} | {r['sl_hits']:<8}")
        print("-" * 80)
    except Exception as e:
        print(f"Database error: {e}")

if __name__ == "__main__":
    main()

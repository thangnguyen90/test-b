from pathlib import Path
path = Path('/home/thangnguyen/project/ml-candles/binance-scalping-bot/backend/.env')
text = path.read_text(encoding='utf-8')
updates = {
    'BINANCE_EXECUTION_MODE': 'test',
    'BINANCE_EXECUTION_ALLOW_LIVE': 'false',
    'BINANCE_EXECUTION_RECV_WINDOW_MS': '5000',
    'BINANCE_EXECUTION_SET_LEVERAGE_BEFORE_ORDER': 'false',
}
lines = text.splitlines()
out = []
seen = set()
for line in lines:
    if '=' in line and not line.lstrip().startswith('#'):
        key = line.split('=', 1)[0].strip()
        if key in updates:
            out.append(f'{key}={updates[key]}')
            seen.add(key)
            continue
    out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f'{key}={value}')
path.write_text('\n'.join(out) + '\n', encoding='utf-8')

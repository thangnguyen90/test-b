from pathlib import Path

base = Path('/home/thangnguyen/project/ml-candles/binance-scalping-bot/backend')

files = {}
files['app/models/binance_execution.py'] = '''from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class BinanceExecutionRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=32)
    side: str = Field(pattern="^(LONG|SHORT)$")
    quantity: float = Field(gt=0)
    leverage: Optional[int] = Field(default=None, ge=1, le=125)
    mode: Optional[str] = Field(default=None, pattern="^(test|live)$")
    order_type: str = Field(default="MARKET", pattern="^(MARKET)$")
    position_side: Optional[str] = Field(default=None, pattern="^(BOTH|LONG|SHORT)$")
    reduce_only: bool = False
    set_leverage: bool = False
    confirm_live: bool = False


class BinanceExecutionResponse(BaseModel):
    ok: bool
    model: str = "ML_CANDLES_BG"
    mode: str
    symbol: str
    exchange_symbol: str
    exchange_side: str
    order_type: str
    quantity: float
    leverage: Optional[int] = None
    reduce_only: bool = False
    client_order_id: str
    test_order: bool = True
    live_sent: bool = False
    order_id: Optional[str] = None
    order_status: Optional[str] = None
    mark_price: Optional[float] = None
    price_source: Optional[str] = None
    base_url: Optional[str] = None
    note: Optional[str] = None
    response: Optional[dict[str, Any]] = None


class BinanceExecutionStatusResponse(BaseModel):
    configured: bool
    mode: str
    allow_live: bool
    can_place_test_orders: bool
    can_place_live_orders: bool
    base_url: str
'''

files['app/services/binance_execution_client.py'] = '''from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.models.binance_execution import BinanceExecutionRequest, BinanceExecutionResponse
from app.services.binance_client import BinanceFuturesClient


class BinanceExecutionError(RuntimeError):
    pass


class BinanceExecutionDisabledError(BinanceExecutionError):
    pass


class BinanceLiveConfirmationError(BinanceExecutionError):
    pass


class BinanceFuturesExecutionClient:
    def __init__(self) -> None:
        self.market_client = BinanceFuturesClient()

    @staticmethod
    def _normalize_mode(value: object) -> str:
        text = str(value or "disabled").strip().lower()
        if text not in {"disabled", "test", "live"}:
            return "disabled"
        return text

    def _ensure_credentials(self) -> None:
        if not settings.binance_api_key or not settings.binance_api_secret:
            raise BinanceExecutionDisabledError("Binance API credentials are not configured")

    def _resolve_mode(self, requested_mode: str | None) -> str:
        configured_mode = self._normalize_mode(settings.binance_execution_mode)
        requested = self._normalize_mode(requested_mode) if requested_mode else None
        if configured_mode == "disabled":
            raise BinanceExecutionDisabledError("BINANCE_EXECUTION_MODE is disabled")
        if requested == "live" and configured_mode != "live":
            raise BinanceExecutionDisabledError("Live Binance execution is not enabled by config")
        if requested == "test":
            return "test"
        if requested == "live":
            return "live"
        return configured_mode

    @property
    def _base_url(self) -> str:
        base_url = str(settings.binance_api_base_url or "https://fapi.binance.com").strip()
        return base_url.rstrip("/")

    def status(self) -> dict[str, Any]:
        mode = self._normalize_mode(settings.binance_execution_mode)
        configured = bool(settings.binance_api_key and settings.binance_api_secret)
        can_place_test = configured and mode in {"test", "live"}
        can_place_live = configured and mode == "live" and bool(settings.binance_execution_allow_live)
        return {
            "configured": configured,
            "mode": mode,
            "allow_live": bool(settings.binance_execution_allow_live),
            "can_place_test_orders": can_place_test,
            "can_place_live_orders": can_place_live,
            "base_url": self._base_url,
        }

    @staticmethod
    def _symbol_candidates(symbol: str) -> list[str]:
        raw = str(symbol or "").strip().upper()
        no_settle = raw.replace(":USDT", "")
        compact = no_settle.replace("/", "")
        out = [raw, no_settle, compact]
        if "/USDT" in no_settle:
            out.append(f"{no_settle}:USDT")
        return [item for item in out if item]

    def _resolve_market(self, symbol: str) -> dict[str, Any]:
        markets = self.market_client.load_markets()
        for candidate in self._symbol_candidates(symbol):
            market = markets.get(candidate)
            if market:
                return market
        candidate_set = set(self._symbol_candidates(symbol))
        for market in markets.values():
            market_symbol = str(market.get("symbol") or "").upper()
            market_id = str(market.get("id") or "").upper()
            aliases = set(self._symbol_candidates(market_symbol)) | set(self._symbol_candidates(market_id))
            if candidate_set & aliases:
                return market
        raise BinanceExecutionError(f"Unsupported Binance futures symbol: {symbol}")

    def _normalize_quantity(self, market_symbol: str, quantity: float) -> tuple[str, float]:
        exchange = self.market_client._get_exchange()
        exchange.load_markets()
        try:
            quantity_text = exchange.amount_to_precision(market_symbol, quantity)
        except Exception as exc:
            raise BinanceExecutionError(f"Cannot normalize quantity for {market_symbol}: {exc}") from exc
        try:
            quantity_value = float(quantity_text)
        except Exception as exc:
            raise BinanceExecutionError(f"Invalid normalized quantity for {market_symbol}: {quantity_text}") from exc
        if quantity_value <= 0:
            raise BinanceExecutionError(f"Quantity rounds to zero for {market_symbol}")
        return quantity_text, quantity_value

    def _resolve_mark_price(self, symbol: str) -> tuple[float | None, str | None]:
        try:
            ticker = self.market_client.fetch_ticker(symbol)
            last = ticker.get("last")
            if last is None:
                last = ticker.get("close")
            if last is None:
                bid = ticker.get("bid")
                ask = ticker.get("ask")
                if bid is not None and ask is not None:
                    last = (bid + ask) / 2
            if last is not None:
                return float(last), "ticker"
        except Exception:
            pass
        try:
            rows = self.market_client.fetch_ohlcv(symbol, timeframe="1m", limit=2)
            if rows:
                return float(rows[-1][4]), "ohlcv"
        except Exception:
            pass
        return None, None

    @staticmethod
    def _to_exchange_side(side: str) -> str:
        return "BUY" if str(side).upper() == "LONG" else "SELL"

    @staticmethod
    def _client_order_id(market_id: str, mode: str) -> str:
        symbol_token = "".join(ch for ch in str(market_id).upper() if ch.isalnum())[:10] or "UNKNOWN"
        suffix = "T" if mode == "test" else "L"
        ts = str(int(time.time() * 1000))[-10:]
        return f"ML_CBG_{suffix}_{symbol_token}_{ts}"[:36]

    @staticmethod
    def _signed_query(params: dict[str, Any]) -> str:
        cleaned: list[tuple[str, str]] = []
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                value = "true" if value else "false"
            cleaned.append((str(key), str(value)))
        return urlencode(cleaned)

    def _signed_request(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self._ensure_credentials()
        query = self._signed_query(params)
        signature = hmac.new(
            settings.binance_api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        url = f"{self._base_url}{path}?{query}&signature={signature}"
        headers = {"X-MBX-APIKEY": settings.binance_api_key}
        try:
            response = httpx.request(method=method.upper(), url=url, headers=headers, timeout=20.0)
        except Exception as exc:
            raise BinanceExecutionError(f"Cannot reach Binance futures API: {exc}") from exc
        if response.status_code >= 400:
            try:
                payload = response.json()
            except Exception:
                payload = {"detail": response.text}
            detail = payload.get("msg") or payload.get("detail") or response.text
            code = payload.get("code")
            if code is not None:
                detail = f"{detail} (code={code})"
            raise BinanceExecutionError(f"Binance futures rejected request: {detail}")
        if not response.text.strip():
            return {}
        try:
            return response.json()
        except Exception:
            return {"raw_text": response.text}

    def _maybe_set_leverage(self, *, market_id: str, leverage: int | None, enable: bool) -> dict[str, Any] | None:
        if leverage is None or leverage <= 0 or not enable:
            return None
        params = {
            "symbol": market_id,
            "leverage": int(leverage),
            "timestamp": int(time.time() * 1000),
            "recvWindow": int(settings.binance_execution_recv_window_ms),
        }
        return self._signed_request("POST", "/fapi/v1/leverage", params)

    def place_ml_candles_bg_order(self, req: BinanceExecutionRequest) -> BinanceExecutionResponse:
        effective_mode = self._resolve_mode(req.mode)
        self._ensure_credentials()
        market = self._resolve_market(req.symbol)
        market_symbol = str(market.get("symbol") or req.symbol)
        market_id = str(market.get("id") or req.symbol).upper()
        quantity_text, quantity_value = self._normalize_quantity(market_symbol, req.quantity)
        exchange_side = self._to_exchange_side(req.side)
        client_order_id = self._client_order_id(market_id, effective_mode)
        mark_price, price_source = self._resolve_mark_price(market_symbol)

        if effective_mode == "live":
            if not bool(settings.binance_execution_allow_live):
                raise BinanceExecutionDisabledError("BINANCE_EXECUTION_ALLOW_LIVE is false")
            if not req.confirm_live:
                raise BinanceLiveConfirmationError("confirm_live=true is required for live Binance orders")

        leverage_response = None
        should_set_leverage = bool(req.set_leverage or settings.binance_execution_set_leverage_before_order)
        if effective_mode == "live":
            leverage_response = self._maybe_set_leverage(
                market_id=market_id,
                leverage=req.leverage,
                enable=should_set_leverage,
            )

        order_params = {
            "symbol": market_id,
            "side": exchange_side,
            "type": req.order_type,
            "quantity": quantity_text,
            "newClientOrderId": client_order_id,
            "timestamp": int(time.time() * 1000),
            "recvWindow": int(settings.binance_execution_recv_window_ms),
            "reduceOnly": req.reduce_only,
        }
        if req.position_side:
            order_params["positionSide"] = req.position_side
        if effective_mode == "live":
            order_params["newOrderRespType"] = "RESULT"
            response_payload = self._signed_request("POST", "/fapi/v1/order", order_params)
        else:
            response_payload = self._signed_request("POST", "/fapi/v1/order/test", order_params)

        note_parts = ["This endpoint is scoped to ML_CANDLES_BG entries only"]
        if effective_mode == "test":
            note_parts.append("Binance futures test order validates the payload only and does not open a real position")
        else:
            note_parts.append("This endpoint currently sends only the entry order; TP/SL is not auto-created yet")
        if req.leverage and not should_set_leverage:
            note_parts.append("Leverage was not changed because set_leverage is false")
        if leverage_response is not None:
            note_parts.append("Leverage was updated before sending the live order")

        order_id = response_payload.get("orderId")
        order_status = response_payload.get("status")
        response_body = dict(response_payload) if response_payload else {}
        if leverage_response is not None:
            response_body["leverage_change"] = leverage_response

        return BinanceExecutionResponse(
            ok=True,
            model="ML_CANDLES_BG",
            mode=effective_mode,
            symbol=req.symbol,
            exchange_symbol=market_symbol,
            exchange_side=exchange_side,
            order_type=req.order_type,
            quantity=quantity_value,
            leverage=req.leverage,
            reduce_only=req.reduce_only,
            client_order_id=client_order_id,
            test_order=(effective_mode == "test"),
            live_sent=(effective_mode == "live"),
            order_id=str(order_id) if order_id is not None else None,
            order_status=str(order_status) if order_status is not None else None,
            mark_price=mark_price,
            price_source=price_source,
            base_url=self._base_url,
            note=" | ".join(note_parts),
            response=response_body or None,
        )
'''

files['app/api/binance_execution.py'] = '''from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app.models.binance_execution import (
    BinanceExecutionRequest,
    BinanceExecutionResponse,
    BinanceExecutionStatusResponse,
)
from app.services.binance_execution_client import (
    BinanceExecutionDisabledError,
    BinanceExecutionError,
    BinanceFuturesExecutionClient,
    BinanceLiveConfirmationError,
)

router = APIRouter(prefix="/api/v1/binance-execution", tags=["binance-execution"])
_client = BinanceFuturesExecutionClient()


@router.get("/status", response_model=BinanceExecutionStatusResponse)
def get_binance_execution_status() -> BinanceExecutionStatusResponse:
    return BinanceExecutionStatusResponse(**_client.status())


@router.post("/ml-candles-bg/order", response_model=BinanceExecutionResponse)
async def place_ml_candles_bg_order(req: BinanceExecutionRequest) -> BinanceExecutionResponse:
    try:
        return await asyncio.to_thread(_client.place_ml_candles_bg_order, req)
    except BinanceLiveConfirmationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BinanceExecutionDisabledError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except BinanceExecutionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
'''

for rel, content in files.items():
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')

config_path = base / 'app/core/config.py'
config_text = config_path.read_text(encoding='utf-8')
anchor = "    ml_feedback_severe_loss_weight_multiplier: float = float(os.getenv(\"ML_FEEDBACK_SEVERE_LOSS_WEIGHT_MULTIPLIER\", \"8.0\"))\n"
insert = anchor + "\n    binance_api_key: str = os.getenv(\"BINANCE_API_KEY\", \"\").strip()\n    binance_api_secret: str = os.getenv(\"BINANCE_API_SECRET\", \"\").strip()\n    binance_api_base_url: str = os.getenv(\"BINANCE_API_BASE_URL\", \"https://fapi.binance.com\").strip() or \"https://fapi.binance.com\"\n    binance_execution_mode: str = os.getenv(\"BINANCE_EXECUTION_MODE\", \"disabled\").strip().lower() or \"disabled\"\n    binance_execution_allow_live: bool = os.getenv(\"BINANCE_EXECUTION_ALLOW_LIVE\", \"false\").lower() == \"true\"\n    binance_execution_recv_window_ms: int = int(os.getenv(\"BINANCE_EXECUTION_RECV_WINDOW_MS\", \"5000\"))\n    binance_execution_set_leverage_before_order: bool = os.getenv(\"BINANCE_EXECUTION_SET_LEVERAGE_BEFORE_ORDER\", \"false\").lower() == \"true\"\n"
if 'binance_execution_mode' not in config_text:
    config_text = config_text.replace(anchor, insert)
config_path.write_text(config_text, encoding='utf-8')

main_path = base / 'app/main.py'
main_text = main_path.read_text(encoding='utf-8')
if 'from app.api.binance_execution import router as binance_execution_router' not in main_text:
    main_text = main_text.replace(
        'from app.api.analytics import router as analytics_router\n',
        'from app.api.analytics import router as analytics_router\nfrom app.api.binance_execution import router as binance_execution_router\n',
    )
if 'app.include_router(binance_execution_router)' not in main_text:
    main_text = main_text.replace(
        'app.include_router(analytics_router)\napp.include_router(paper_trades_router)\n',
        'app.include_router(analytics_router)\napp.include_router(binance_execution_router)\napp.include_router(paper_trades_router)\n',
    )
main_path.write_text(main_text, encoding='utf-8')

env_example_path = base / '.env.example'
env_example_text = env_example_path.read_text(encoding='utf-8')
block = '\nBINANCE_EXECUTION_MODE=disabled\nBINANCE_EXECUTION_ALLOW_LIVE=false\nBINANCE_EXECUTION_RECV_WINDOW_MS=5000\nBINANCE_EXECUTION_SET_LEVERAGE_BEFORE_ORDER=false\n'
if 'BINANCE_EXECUTION_MODE=' not in env_example_text:
    env_example_text = env_example_text.rstrip() + '\n' + block
    env_example_path.write_text(env_example_text, encoding='utf-8')

env_path = base / '.env'
env_text = env_path.read_text(encoding='utf-8')
if 'BINANCE_EXECUTION_MODE=' not in env_text:
    env_text = env_text.rstrip() + '\n\nBINANCE_EXECUTION_MODE=disabled\nBINANCE_EXECUTION_ALLOW_LIVE=false\nBINANCE_EXECUTION_RECV_WINDOW_MS=5000\nBINANCE_EXECUTION_SET_LEVERAGE_BEFORE_ORDER=false\n'
    env_path.write_text(env_text, encoding='utf-8')

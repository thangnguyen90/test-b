from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.core.config import settings

BINANCE_MIN_NOTIONAL_USDT = 5.0


class BinanceApiError(RuntimeError):
    def __init__(self, *, status_code: int, message: str, payload: Any | None = None) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.payload = payload


class BinanceFuturesTradeService:
    def __init__(self) -> None:
        self.api_key = str(settings.binance_api_key or "").strip()
        self.api_secret = str(settings.binance_api_secret or "").strip()
        self.base_url = str(settings.binance_api_base_url or "https://fapi.binance.com").rstrip("/")
        self.recv_window_ms = max(1000, int(settings.binance_recv_window_ms))
        self._account_config_cache: dict[str, dict[str, Any]] = {}
        self._exchange_info_cache: dict[str, Any] | None = None
        self._exchange_info_ts: float = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        text = str(symbol or "").strip().upper()
        if not text:
            raise ValueError("Missing symbol")
        if "/" in text:
            base = text.split("/", 1)[0]
            return f"{base}USDT"
        if text.endswith(":USDT"):
            return text.replace(":USDT", "")
        return text.replace("/", "").replace(":", "")

    @staticmethod
    def _floor_to_step(value: float, step: float) -> float:
        if step <= 0:
            return float(value)
        value_dec = Decimal(str(value))
        step_dec = Decimal(str(step))
        units = (value_dec / step_dec).quantize(Decimal("1"), rounding=ROUND_DOWN)
        return float(units * step_dec)

    @staticmethod
    def _format_decimal(value: float, step: float) -> str:
        floored = BinanceFuturesTradeService._floor_to_step(value, step)
        step_dec = Decimal(str(step))
        digits = max(0, -step_dec.as_tuple().exponent)
        text = f"{floored:.{digits}f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    def _public_request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "binance-scalping-bot/1.0",
            },
            method="GET",
        )
        with urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8").strip()
        return json.loads(raw) if raw else {}

    def _get_exchange_info(self, ttl_sec: int = 3600) -> dict[str, Any]:
        with self._lock:
            if self._exchange_info_cache is not None and (time.time() - self._exchange_info_ts) <= max(60, int(ttl_sec)):
                return self._exchange_info_cache
        payload = self._public_request("/fapi/v1/exchangeInfo")
        with self._lock:
            self._exchange_info_cache = payload if isinstance(payload, dict) else {}
            self._exchange_info_ts = time.time()
            return self._exchange_info_cache

    def get_mark_price(self, symbol: str) -> float:
        normalized = self._normalize_symbol(symbol)
        payload = self._public_request("/fapi/v1/premiumIndex", {"symbol": normalized})
        return float(payload.get("markPrice") or 0.0)

    def _signed_request(self, method: str, path: str, params: dict[str, Any]) -> Any:
        if not self.api_key or not self.api_secret:
            raise ValueError("BINANCE_API_KEY or BINANCE_API_SECRET is missing")
        method_upper = method.upper()
        payload = {k: v for k, v in params.items() if v is not None}
        payload["recvWindow"] = self.recv_window_ms
        payload["timestamp"] = int(time.time() * 1000)
        query = urlencode(payload)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signed_query = f"{query}&signature={signature}"
        url = f"{self.base_url}{path}"
        body: bytes | None = None
        if method_upper in {"GET", "DELETE"}:
            url = f"{url}?{signed_query}"
        else:
            body = signed_query.encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-MBX-APIKEY": self.api_key,
                "Accept": "application/json",
                "User-Agent": "binance-scalping-bot/1.0",
            },
            method=method_upper,
        )
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read().decode("utf-8").strip()
        except HTTPError as exc:
            raw = ""
            payload: Any | None = None
            try:
                raw = exc.read().decode("utf-8").strip()
            except Exception:
                raw = ""
            if raw:
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = {"raw": raw}
            message = f"Binance API {exc.code} on {path}"
            if isinstance(payload, dict):
                code = payload.get("code")
                msg = payload.get("msg")
                if code is not None or msg:
                    message = f"{message}: code={code} msg={msg}"
            elif raw:
                message = f"{message}: {raw}"
            raise BinanceApiError(status_code=exc.code, message=message, payload=payload) from exc
        return json.loads(raw) if raw else {}

    def _order_lookup_params(
        self,
        *,
        symbol: str,
        order_id: int | str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalize_symbol(symbol)
        params: dict[str, Any] = {"symbol": normalized}
        if order_id is not None and str(order_id).strip():
            params["orderId"] = int(order_id)
        elif client_order_id:
            params["origClientOrderId"] = str(client_order_id).strip()
        else:
            raise ValueError("Missing order_id or client_order_id")
        return params

    def get_symbol_rules(self, symbol: str) -> dict[str, Any]:
        normalized = self._normalize_symbol(symbol)
        payload = self._get_exchange_info()
        for item in payload.get("symbols", []):
            if str(item.get("symbol")) == normalized:
                return item
        raise ValueError(f"Symbol {normalized} not found in Binance exchangeInfo")

    @staticmethod
    def _config_cache_key(symbol: str, leverage: int, margin_type: str) -> str:
        return f"{symbol}:{int(leverage)}:{str(margin_type or 'ISOLATED').upper()}"

    def _has_recent_account_config(self, symbol: str, leverage: int, margin_type: str, ttl_sec: int = 3600) -> bool:
        key = self._config_cache_key(symbol, leverage, margin_type)
        with self._lock:
            item = self._account_config_cache.get(key)
        if not item:
            return False
        return (time.time() - float(item.get("ts") or 0.0)) <= max(60, int(ttl_sec))

    def _mark_account_config(self, symbol: str, leverage: int, margin_type: str, payload: dict[str, Any]) -> None:
        key = self._config_cache_key(symbol, leverage, margin_type)
        with self._lock:
            self._account_config_cache[key] = {
                "ts": time.time(),
                "payload": payload,
            }

    def _get_account_config_payload(self, symbol: str, leverage: int, margin_type: str) -> dict[str, Any] | None:
        key = self._config_cache_key(symbol, leverage, margin_type)
        with self._lock:
            item = self._account_config_cache.get(key)
        if not item:
            return None
        return item.get("payload") if isinstance(item, dict) else None

    def ensure_margin_and_leverage(self, symbol: str, *, leverage: int, margin_type: str) -> dict[str, Any]:
        normalized = self._normalize_symbol(symbol)
        if self._has_recent_account_config(normalized, leverage, margin_type):
            cached = self._get_account_config_payload(normalized, leverage, margin_type)
            return cached or {
                "margin_type": {"code": 200, "msg": "cached"},
                "leverage": {"symbol": normalized, "leverage": int(leverage), "msg": "cached"},
            }
        margin_resp: dict[str, Any]
        try:
            margin_resp = self._signed_request(
                "POST",
                "/fapi/v1/marginType",
                {
                    "symbol": normalized,
                    "marginType": str(margin_type or "ISOLATED").upper(),
                },
            )
        except Exception as exc:
            text = str(exc)
            if "No need to change margin type" in text or '"code":-4046' in text:
                margin_resp = {"code": 200, "msg": "unchanged"}
            else:
                raise
        leverage_resp = self._signed_request(
            "POST",
            "/fapi/v1/leverage",
            {
                "symbol": normalized,
                "leverage": int(leverage),
            },
        )
        payload = {
            "margin_type": margin_resp,
            "leverage": leverage_resp,
        }
        self._mark_account_config(normalized, leverage, margin_type, payload)
        return payload

    def get_order_status(
        self,
        *,
        symbol: str,
        order_id: int | str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        params = self._order_lookup_params(
            symbol=symbol,
            order_id=order_id,
            client_order_id=client_order_id,
        )
        return self._signed_request("GET", "/fapi/v1/order", params)

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: int | str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        params = self._order_lookup_params(
            symbol=symbol,
            order_id=order_id,
            client_order_id=client_order_id,
        )
        return self._signed_request("DELETE", "/fapi/v1/order", params)

    def build_limit_order(self, *, symbol: str, side: str, entry_price: float, order_usdt: float) -> dict[str, Any]:
        normalized = self._normalize_symbol(symbol)
        rules = self.get_symbol_rules(normalized)
        filters = {str(item.get("filterType")): item for item in rules.get("filters", [])}
        price_filter = filters.get("PRICE_FILTER", {})
        lot_filter = filters.get("LOT_SIZE") or filters.get("MARKET_LOT_SIZE") or {}
        tick_size = float(price_filter.get("tickSize") or 0.0)
        step_size = float(lot_filter.get("stepSize") or 0.0)
        min_qty = float(lot_filter.get("minQty") or 0.0)
        if entry_price <= 0 or order_usdt <= 0:
            raise ValueError("Invalid entry price or notional_usdt")
        quantity_raw = order_usdt / entry_price
        quantity = self._floor_to_step(quantity_raw, step_size or (10 ** -6))
        if min_qty > 0 and quantity < min_qty:
            quantity = min_qty
        if quantity <= 0:
            raise ValueError("Calculated quantity is too small")
        side_text = str(side or "").upper()
        if side_text not in {"LONG", "SHORT"}:
            raise ValueError(f"Unsupported side {side}")
        order_side = "BUY" if side_text == "LONG" else "SELL"
        return {
            "symbol": normalized,
            "side": order_side,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": self._format_decimal(quantity, step_size or (10 ** -6)),
            "price": self._format_decimal(entry_price, tick_size or (10 ** -6)),
            "newOrderRespType": "ACK",
            "newClientOrderId": f"codexph_{int(time.time() * 1000)}",
        }

    def build_reduce_only_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        price: float,
        quantity: float | str,
        client_order_prefix: str = "codexph_tp",
    ) -> dict[str, Any]:
        normalized = self._normalize_symbol(symbol)
        rules = self.get_symbol_rules(normalized)
        filters = {str(item.get("filterType")): item for item in rules.get("filters", [])}
        price_filter = filters.get("PRICE_FILTER", {})
        lot_filter = filters.get("LOT_SIZE") or filters.get("MARKET_LOT_SIZE") or {}
        tick_size = float(price_filter.get("tickSize") or 0.0)
        step_size = float(lot_filter.get("stepSize") or 0.0)
        min_qty = float(lot_filter.get("minQty") or 0.0)
        qty_value = float(quantity)
        qty_value = self._floor_to_step(qty_value, step_size or (10 ** -6))
        if min_qty > 0 and qty_value < min_qty:
            raise ValueError("Quantity below exchange minimum for TP order")
        side_text = str(side or "").upper()
        if side_text not in {"BUY", "SELL"}:
            raise ValueError(f"Unsupported Binance order side {side}")
        return {
            "symbol": normalized,
            "side": side_text,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "reduceOnly": "true",
            "quantity": self._format_decimal(qty_value, step_size or (10 ** -6)),
            "price": self._format_decimal(price, tick_size or (10 ** -6)),
            "newOrderRespType": "ACK",
            "newClientOrderId": f"{client_order_prefix}_{int(time.time() * 1000)}",
        }

    def build_reduce_only_stop_order(
        self,
        *,
        symbol: str,
        side: str,
        stop_price: float,
        quantity: float | str,
        client_order_prefix: str = "codexph_sl",
    ) -> dict[str, Any]:
        normalized = self._normalize_symbol(symbol)
        rules = self.get_symbol_rules(normalized)
        filters = {str(item.get("filterType")): item for item in rules.get("filters", [])}
        price_filter = filters.get("PRICE_FILTER", {})
        lot_filter = filters.get("LOT_SIZE") or filters.get("MARKET_LOT_SIZE") or {}
        tick_size = float(price_filter.get("tickSize") or 0.0)
        step_size = float(lot_filter.get("stepSize") or 0.0)
        min_qty = float(lot_filter.get("minQty") or 0.0)
        side_text = str(side or "").upper()
        if side_text not in {"BUY", "SELL"}:
            raise ValueError(f"Unsupported Binance order side {side}")
        if stop_price <= 0:
            raise ValueError("Invalid stop_price")
        qty_value = float(quantity)
        qty_value = self._floor_to_step(qty_value, step_size or (10 ** -6))
        if min_qty > 0 and qty_value < min_qty:
            raise ValueError("Quantity below exchange minimum for SL order")
        return {
            "symbol": normalized,
            "side": side_text,
            "type": "STOP",
            "timeInForce": "GTC",
            "quantity": self._format_decimal(qty_value, step_size or (10 ** -6)),
            "price": self._format_decimal(stop_price, tick_size or (10 ** -6)),
            "stopPrice": self._format_decimal(stop_price, tick_size or (10 ** -6)),
            "reduceOnly": "true",
            "workingType": "MARK_PRICE",
            "priceProtect": "true",
            "newOrderRespType": "ACK",
            "newClientOrderId": f"{client_order_prefix}_{int(time.time() * 1000)}",
        }

    def place_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        entry_price: float,
        order_usdt: float,
        leverage: int,
        margin_type: str,
        test_mode: bool,
    ) -> dict[str, Any]:
        margin_usdt = float(order_usdt)
        leverage_value = int(leverage)
        if margin_usdt <= 0:
            raise ValueError("Margin USDT must be positive")
        if leverage_value <= 0:
            raise ValueError("Leverage must be positive")
        notional_usdt = margin_usdt * leverage_value
        if notional_usdt < BINANCE_MIN_NOTIONAL_USDT:
            raise ValueError(
                f"Notional {notional_usdt:.2f} USDT is below Binance minimum {BINANCE_MIN_NOTIONAL_USDT:.2f} USDT"
            )
        config_resp = self.ensure_margin_and_leverage(
            symbol,
            leverage=leverage_value,
            margin_type=str(margin_type or "ISOLATED").upper(),
        )
        order_params = self.build_limit_order(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            order_usdt=notional_usdt,
        )
        endpoint = "/fapi/v1/order/test" if test_mode else "/fapi/v1/order"
        order_resp = self._signed_request("POST", endpoint, order_params)
        return {
            "test_mode": bool(test_mode),
            "symbol": order_params["symbol"],
            "side": order_params["side"],
            "entry_price": order_params["price"],
            "quantity": order_params["quantity"],
            "margin_usdt": margin_usdt,
            "notional_usdt": notional_usdt,
            "leverage": leverage_value,
            "margin_type": str(margin_type or "ISOLATED").upper(),
            "config": config_resp,
            "exchange_response": order_resp,
        }

    def place_reduce_only_tp_order(
        self,
        *,
        symbol: str,
        position_side: str,
        tp_price: float,
        quantity: float | str,
        test_mode: bool,
    ) -> dict[str, Any]:
        position_side_text = str(position_side or "").upper()
        if position_side_text not in {"LONG", "SHORT"}:
            raise ValueError(f"Unsupported position side {position_side}")
        exit_side = "SELL" if position_side_text == "LONG" else "BUY"
        order_params = self.build_reduce_only_limit_order(
            symbol=symbol,
            side=exit_side,
            price=tp_price,
            quantity=quantity,
        )
        endpoint = "/fapi/v1/order/test" if test_mode else "/fapi/v1/order"
        order_resp = self._signed_request("POST", endpoint, order_params)
        return {
            "test_mode": bool(test_mode),
            "symbol": order_params["symbol"],
            "side": order_params["side"],
            "tp_price": order_params["price"],
            "quantity": order_params["quantity"],
            "reduce_only": True,
            "exchange_response": order_resp,
        }

    def place_close_position_sl_order(
        self,
        *,
        symbol: str,
        position_side: str,
        stop_price: float,
        quantity: float | str,
        test_mode: bool,
    ) -> dict[str, Any]:
        position_side_text = str(position_side or "").upper()
        if position_side_text not in {"LONG", "SHORT"}:
            raise ValueError(f"Unsupported position side {position_side}")
        exit_side = "SELL" if position_side_text == "LONG" else "BUY"
        order_params = self.build_reduce_only_stop_order(
            symbol=symbol,
            side=exit_side,
            stop_price=stop_price,
            quantity=quantity,
        )
        endpoint = "/fapi/v1/order/test" if test_mode else "/fapi/v1/order"
        order_resp = self._signed_request("POST", endpoint, order_params)
        return {
            "test_mode": bool(test_mode),
            "symbol": order_params["symbol"],
            "side": order_params["side"],
            "quantity": order_params["quantity"],
            "stop_price": order_params["stopPrice"],
            "reduce_only": True,
            "exchange_response": order_resp,
        }

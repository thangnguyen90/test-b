from __future__ import annotations

from fastapi import HTTPException
from fastapi import APIRouter, Query

from app.models.pump_hunter import PumpHunterBinanceOrderRequest, PumpHunterEntrySideControlRequest
from app.services.analytics_service import AnalyticsService
from app.services.binance_futures_trade_service import BinanceApiError
from app.services.pump_scanner_service import PumpScannerService

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])
service = AnalyticsService()
pump_service = PumpScannerService()


@router.get("/top-volatility")
def get_top_volatility(
    days: int = Query(default=1, ge=1, le=7),
    limit: int = Query(default=30, ge=5, le=100),
) -> dict:
    items = service.top_volatility(days=days, limit=limit)
    return {
        "days": days,
        "count": len(items),
        "items": items,
    }


@router.get("/liquidation-overview")
def get_liquidation_overview(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=10, le=100),
    full_symbols: bool = Query(default=True),
) -> dict:
    payload = service.liquidation_overview(page=page, page_size=page_size, full_symbols=full_symbols)
    return {
        "page": payload["page"],
        "page_size": payload["page_size"],
        "total_symbols": payload["total_symbols"],
        "count": payload["count"],
        "items": payload["items"],
        "note": "Liquidation zone/value are estimated proxies based on OI, mark price, and long-short ratio.",
    }


@router.get("/btc-trend")
def get_btc_trend() -> dict:
    return service.btc_trend_forecast()


@router.get("/pump-hunter")
def get_pump_hunter_scan(
    max_symbols: int = Query(default=250, ge=0, le=500),
    min_score: float = Query(default=58.0, ge=0.0, le=100.0),
    limit: int = Query(default=18, ge=1, le=100),
    debug_near_miss: bool = Query(default=False),
    near_miss_limit: int = Query(default=12, ge=1, le=50),
) -> dict:
    payload = pump_service.scan(
        max_symbols=max_symbols,
        min_score=min_score,
        limit=limit,
        debug_near_miss=debug_near_miss,
        near_miss_limit=near_miss_limit,
    )
    return {
        "scanned": payload["scanned"],
        "count": payload["count"],
        "min_score": payload["min_score"],
        "max_symbols": payload["max_symbols"],
        "items": payload["items"],
        "near_misses": payload.get("near_misses", []),
        "updated_at": payload["updated_at"],
        "note": payload["note"],
        "paper_trade_enabled": payload.get("paper_trade_enabled"),
        "live_order_status": payload.get("live_order_status"),
    }


@router.get("/pump-hunter/entry-side-control")
def get_pump_hunter_entry_side_control() -> dict:
    return pump_service.get_entry_side_control()


@router.post("/pump-hunter/entry-side-control")
def update_pump_hunter_entry_side_control(req: PumpHunterEntrySideControlRequest) -> dict:
    return pump_service.set_entry_side_control(
        allow_long=bool(req.allow_long),
        allow_short=bool(req.allow_short),
    )


@router.get("/pump-hunter/detail")
def get_pump_hunter_detail(symbol: str = Query(..., min_length=3)) -> dict:
    payload = pump_service.analyze_symbol(symbol=symbol, include_candles=True)
    return payload


@router.post("/pump-hunter/binance-order")
def submit_pump_hunter_binance_order(req: PumpHunterBinanceOrderRequest) -> dict:
    row = pump_service.analyze_symbol(symbol=req.symbol, include_candles=False)
    try:
        return pump_service.submit_binance_order(
            row,
            test_mode=bool(req.test_mode),
            order_usdt=req.order_usdt,
            leverage=req.leverage,
            margin_type=req.margin_type,
        )
    except BinanceApiError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": str(exc),
                "binance_status_code": exc.status_code,
                "binance_payload": exc.payload,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

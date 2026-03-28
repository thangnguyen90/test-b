from __future__ import annotations

from fastapi import APIRouter, Query

from app.services.analytics_service import AnalyticsService
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
    max_symbols: int = Query(default=35, ge=0, le=500),
    min_score: float = Query(default=58.0, ge=0.0, le=100.0),
    limit: int = Query(default=18, ge=1, le=100),
) -> dict:
    payload = pump_service.scan(max_symbols=max_symbols, min_score=min_score, limit=limit)
    return {
        "scanned": payload["scanned"],
        "count": payload["count"],
        "min_score": payload["min_score"],
        "max_symbols": payload["max_symbols"],
        "items": payload["items"],
        "updated_at": payload["updated_at"],
        "note": payload["note"],
    }


@router.get("/pump-hunter/detail")
def get_pump_hunter_detail(symbol: str = Query(..., min_length=3)) -> dict:
    payload = pump_service.analyze_symbol(symbol=symbol, include_candles=True)
    return payload

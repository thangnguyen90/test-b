from __future__ import annotations

from fastapi import APIRouter, Query

from app.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])
service = AnalyticsService()


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

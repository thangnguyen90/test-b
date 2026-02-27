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
    limit: int = Query(default=30, ge=5, le=100),
) -> dict:
    items = service.liquidation_overview(limit=limit)
    return {
        "count": len(items),
        "items": items,
        "note": "Liquidation zone/value are estimated proxies based on OI, mark price, and long-short ratio.",
    }

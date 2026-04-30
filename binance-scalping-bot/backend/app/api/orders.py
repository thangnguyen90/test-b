from fastapi import APIRouter, HTTPException

from app.api.analytics import pump_service
from app.deps import order_manager
from app.models.orders import HunterLiveOrder, Order, OrderCreate

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


@router.post("/pending", response_model=Order)
def create_pending_order(order_in: OrderCreate) -> Order:
    try:
        return order_manager.create_pending_order(order_in)
    except Exception as exc:  # pragma: no cover - defensive handler
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/pending", response_model=list[Order])
def get_pending_orders() -> list[Order]:
    return order_manager.list_pending()


@router.get("/open", response_model=list[Order])
def get_open_orders() -> list[Order]:
    return order_manager.list_open()


@router.get("/closed", response_model=list[Order])
def get_closed_orders() -> list[Order]:
    return order_manager.list_closed()


@router.get("/hunter-live", response_model=list[HunterLiveOrder])
def get_hunter_live_orders() -> list[HunterLiveOrder]:
    items = pump_service.list_live_orders()
    return [HunterLiveOrder(**item) for item in items]

from fastapi import APIRouter, HTTPException

from app.deps import order_manager
from app.models.orders import Order, OrderCreate

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

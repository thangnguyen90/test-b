from __future__ import annotations

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

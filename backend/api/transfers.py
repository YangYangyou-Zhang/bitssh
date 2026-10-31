from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/transfers")


@router.get("")
async def list_transfers(request: Request) -> list[dict]:
    return request.app.state.transfers.list()


@router.post("/{transfer_id}/cancel")
async def cancel_transfer(transfer_id: str, request: Request) -> dict:
    if not request.app.state.transfers.cancel(transfer_id):
        raise HTTPException(404, "TRANSFER_NOT_FOUND")
    return {"ok": True}

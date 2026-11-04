import logging
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)
router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    service: str = "magi-api"
    version: str = "0.1.0"
    database: Literal["connected", "unavailable"]


@router.get("/health", response_model=HealthResponse)
def health(request: Request, response: Response) -> HealthResponse:
    """Readiness includes a real database query; no credentials are returned."""
    try:
        with request.app.state.sessions() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("Database readiness check failed")
        response.status_code = 503
        return HealthResponse(status="unavailable", database="unavailable")
    return HealthResponse(status="ok", database="connected")

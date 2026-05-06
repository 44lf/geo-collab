from fastapi import APIRouter

from server.app.schemas.system import SystemStatus
from server.app.services.system_status import get_system_status

router = APIRouter()


@router.get("/status", response_model=SystemStatus)
def read_system_status() -> SystemStatus:
    return get_system_status()


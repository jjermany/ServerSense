from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from serversense.db import get_db
from serversense.models import Integration
from serversense.security import current_user
from serversense.services.integrations import registry

router = APIRouter(
    prefix="/api/integrations",
    tags=["integrations"],
    dependencies=[Depends(current_user)],
)


@router.get("")
def list_integrations(db: Session = Depends(get_db)) -> dict:
    configured = list(db.scalars(select(Integration).order_by(Integration.name)))
    return {
        "available_providers": [item.__dict__ for item in registry.descriptors()],
        "configured": [
            {
                "id": item.id,
                "provider": item.provider,
                "name": item.name,
                "enabled": item.enabled,
                "configured": bool(item.config),
            }
            for item in configured
        ],
    }

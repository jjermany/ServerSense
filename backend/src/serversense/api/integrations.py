import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from serversense.db import get_db
from serversense.models import Integration, MediaActivity, MediaSchedule
from serversense.schemas import MediaIntegrationRequest
from serversense.security import current_user
from serversense.services.integrations import DESCRIPTORS, normalize_url, test_integration
from serversense.services.secrets import encrypt_secret

router = APIRouter(
    prefix="/api/integrations",
    tags=["integrations"],
    dependencies=[Depends(current_user)],
)


def _response(item: Integration) -> dict[str, object]:
    return {
        "id": item.id,
        "provider": item.provider,
        "name": item.name,
        "enabled": item.enabled,
        "url": str(item.config.get("url", "")),
        "api_key_configured": bool(item.config.get("api_key_encrypted")),
    }


@router.get("")
def list_integrations(db: Session = Depends(get_db)) -> dict[str, object]:
    configured = list(db.scalars(select(Integration).order_by(Integration.name)))
    return {
        "available_providers": [item.__dict__ for item in DESCRIPTORS],
        "configured": [_response(item) for item in configured],
    }


def _configuration(
    payload: MediaIntegrationRequest, previous: dict[str, object] | None = None
) -> dict:
    value = dict(previous or {})
    value["url"] = normalize_url(payload.url)
    if payload.api_key:
        value["api_key_encrypted"] = encrypt_secret(payload.api_key)
    if not value.get("api_key_encrypted"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "API key is required")
    value.pop("last_collected_at", None)
    return value


def _require_unique_name(db: Session, name: str, excluding_id: int | None = None) -> None:
    query = select(Integration.id).where(func.lower(Integration.name) == name.lower())
    if excluding_id is not None:
        query = query.where(Integration.id != excluding_id)
    if db.scalar(query) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Integration name is already in use")


@router.post("", status_code=status.HTTP_201_CREATED)
def create_integration(
    payload: MediaIntegrationRequest, db: Session = Depends(get_db)
) -> dict[str, object]:
    name = payload.name.strip()
    _require_unique_name(db, name)
    try:
        config = _configuration(payload)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    item = Integration(
        provider=payload.provider,
        name=name,
        enabled=payload.enabled,
        config=config,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _response(item)


@router.put("/{integration_id}")
def update_integration(
    integration_id: int,
    payload: MediaIntegrationRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    item = db.get(Integration, integration_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    if item.provider != payload.provider:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "An existing integration's type cannot be changed",
        )
    name = payload.name.strip()
    _require_unique_name(db, name, item.id)
    try:
        item.config = _configuration(payload, item.config)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    item.provider = payload.provider
    if item.name != name:
        db.execute(
            update(MediaActivity)
            .where(MediaActivity.integration_id == item.id)
            .values(instance_name=name)
        )
        db.execute(
            update(MediaSchedule)
            .where(MediaSchedule.integration_id == item.id)
            .values(instance_name=name)
        )
    item.name = name
    item.enabled = payload.enabled
    db.commit()
    db.refresh(item)
    return _response(item)


@router.post("/{integration_id}/test")
def test_configured_integration(
    integration_id: int, db: Session = Depends(get_db)
) -> dict[str, str]:
    item = db.get(Integration, integration_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    try:
        return test_integration(item)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Could not connect to {item.name}: {type(exc).__name__}",
        ) from exc


@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_integration(integration_id: int, db: Session = Depends(get_db)) -> Response:
    item = db.get(Integration, integration_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    db.execute(delete(MediaActivity).where(MediaActivity.integration_id == item.id))
    db.execute(delete(MediaSchedule).where(MediaSchedule.integration_id == item.id))
    db.delete(item)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

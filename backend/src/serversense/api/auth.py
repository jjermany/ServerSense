from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from serversense.db import get_db
from serversense.models import Setting, User
from serversense.schemas import LoginRequest, SetupRequest, UserResponse
from serversense.security import (
    COOKIE_NAME,
    clear_session,
    create_session,
    current_user,
    login_limiter,
    password_hash,
)
from serversense.services.demo import seed_demo_data

router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.get("/status")
def status_info(db: Session = Depends(get_db)) -> dict:
    return {"setup_required": (db.scalar(select(func.count(User.id))) or 0) == 0}


@router.post("/setup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def setup(payload: SetupRequest, response: Response, db: Session = Depends(get_db)) -> User:
    if db.scalar(select(func.count(User.id))) or 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup has already been completed")
    user = User(username=payload.username, password_hash=password_hash.hash(payload.password))
    db.add(user)
    db.add(
        Setting(
            key="general",
            value={"server_name": payload.server_name, "demo_mode": payload.demo_mode},
        )
    )
    db.commit()
    db.refresh(user)
    if payload.demo_mode:
        seed_demo_data(db)
    create_session(db, user, response)
    return user


@router.post("/login", response_model=UserResponse)
def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    client_host = request.client.host if request.client else "unknown"
    limit_key = f"{client_host}:{payload.username.lower()}"
    login_limiter.check(limit_key)
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not password_hash.verify(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    login_limiter.reset(limit_key)
    create_session(db, user, response)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Session = Depends(get_db),
) -> None:
    clear_session(db, response, token)


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(current_user)) -> User:
    return user

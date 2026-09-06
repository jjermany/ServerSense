from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from serversense.db import get_db
from serversense.models import Setting, User
from serversense.schemas import (
    LoginRequest,
    MFAPasswordRequest,
    MFARequiredResponse,
    MFAVerifyRequest,
    SetupRequest,
    UserResponse,
)
from serversense.security import (
    COOKIE_NAME,
    DUMMY_PASSWORD_HASH,
    clear_session,
    create_session,
    current_user,
    login_limiter,
    login_source_limiter,
    password_hash,
)
from serversense.services import mfa
from serversense.services.demo import seed_demo_data

router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.get("/status")
def status_info(db: Session = Depends(get_db)) -> dict:
    return {"setup_required": (db.scalar(select(func.count(User.id))) or 0) == 0}


@router.post("/setup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def setup(
    payload: SetupRequest, response: Response, request: Request, db: Session = Depends(get_db)
) -> User:
    login_source_limiter.check(request.client.host if request.client else "unknown")
    if db.scalar(select(func.count(User.id))) or 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup has already been completed")
    db.rollback()
    hashed = password_hash.hash(payload.password)
    # Serialize the check and insert across connections/processes. Hash first so
    # the expensive password operation does not hold SQLite's writer lock.
    db.execute(text("BEGIN IMMEDIATE"))
    if db.scalar(select(func.count(User.id))) or 0:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup has already been completed")
    user = User(username=payload.username, password_hash=hashed)
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


@router.post("/login", response_model=UserResponse | MFARequiredResponse)
def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
) -> User | MFARequiredResponse:
    client_host = request.client.host if request.client else "unknown"
    login_source_limiter.check(client_host)
    limit_key = f"account:{payload.username.lower()}"
    login_limiter.check(limit_key)
    user = db.scalar(select(User).where(func.lower(User.username) == payload.username.lower()))
    valid_password = password_hash.verify(
        payload.password, user.password_hash if user else DUMMY_PASSWORD_HASH
    )
    if not user or not valid_password:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    user = mfa.lock_user(db, user)
    if user.mfa_secret is not None:
        if payload.code is None:
            db.rollback()
            return MFARequiredResponse()
        mfa.consume_code(user, payload.code)
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


@router.get("/mfa")
def mfa_status(user: User = Depends(current_user)) -> dict:
    return mfa.status(user)


@router.post("/mfa/setup")
def mfa_setup(
    payload: MFAPasswordRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    return mfa.start_enrollment(db, user, payload.password)


@router.post("/mfa/confirm")
def mfa_confirm(
    payload: MFAVerifyRequest,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    codes = mfa.confirm_enrollment(db, user, payload.password, payload.code)
    create_session(db, user, response)
    return {"enabled": True, "recovery_codes": codes}


@router.post("/mfa/disable")
def mfa_disable(
    payload: MFAVerifyRequest,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    mfa.disable(db, user, payload.password, payload.code)
    create_session(db, user, response)
    return {"enabled": False, "recovery_codes_remaining": 0}


@router.post("/mfa/recovery-codes")
def mfa_recovery_codes(
    payload: MFAVerifyRequest,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    codes = mfa.regenerate_recovery_codes(db, user, payload.password, payload.code)
    create_session(db, user, response)
    return {"enabled": True, "recovery_codes": codes}

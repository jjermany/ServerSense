import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, Response
from starlette.types import Scope

from serversense.api import ai, auth, integrations, monitoring, settings
from serversense.config import get_settings
from serversense.db import SessionLocal, initialize_database
from serversense.logging import configure_logging
from serversense.middleware import APIProtectionMiddleware
from serversense.services.demo import seed_demo_data
from serversense.services.jobs import dashboard_summary_loop, monitoring_loop
from serversense.services.sense_jobs import sense_job_loop, stop_sense_jobs


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    initialize_database()
    settings_value = get_settings()
    if settings_value.demo_mode:
        with SessionLocal() as db:
            seed_demo_data(db)
    tasks = [
        asyncio.create_task(monitoring_loop()),
        asyncio.create_task(dashboard_summary_loop()),
        asyncio.create_task(sense_job_loop()),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await stop_sense_jobs()


app = FastAPI(title="ServerSense API", version="1.0.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Pydantic's default error includes the rejected input, which can be a
    # password, credential, or an entire malformed request body.
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
                for error in exc.errors()
            ]
        },
    )


app.add_middleware(APIProtectionMiddleware)
app.include_router(auth.router)
app.include_router(monitoring.router)
app.include_router(settings.router)
app.include_router(ai.router)
app.include_router(integrations.router)


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ServerSense"}


static_dir = Path("/app/static")
if static_dir.exists():

    class SPAStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope: Scope) -> Response:
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code == 404 and "." not in Path(path).name:
                    return await super().get_response("index.html", scope)
                raise

    app.mount("/", SPAStaticFiles(directory=static_dir, html=True), name="web")

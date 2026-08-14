import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from serversense.api import ai, auth, integrations, monitoring, settings
from serversense.config import get_settings
from serversense.db import SessionLocal, initialize_database
from serversense.logging import configure_logging
from serversense.services.demo import seed_demo_data
from serversense.services.jobs import monitoring_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    initialize_database()
    settings_value = get_settings()
    if settings_value.demo_mode:
        with SessionLocal() as db:
            seed_demo_data(db)
    task = asyncio.create_task(monitoring_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="ServerSense API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
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

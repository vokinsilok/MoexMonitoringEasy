from contextlib import asynccontextmanager
import asyncio

import uvicorn
from fastapi import APIRouter, FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from src.core.logging import main_logger
from src.db.database import async_session_maker
from src.core.config import settings
from src.init import redis_manager
from src.moduls.tbank.router import tbank_router
from src.moduls.tbank.tasks import sync_russian_shares_task
from src.tasks.broker import broker as taskiq_broker
from src.utils.db_manager import DBManager



@asynccontextmanager
async def lifespan(add: FastAPI):
    permission_codes: set[str] = set()
    for route in add.routes:
        if not isinstance(route, APIRoute):
            continue

    stop_event = asyncio.Event()

    async def schedule_periodic_tbank_sync() -> None:
        interval_seconds = max(10, settings.TBANK_SYNC_INTERVAL_SECONDS)
        while not stop_event.is_set():
            try:
                await sync_russian_shares_task.kiq()
            except Exception as exc:
                main_logger.info(f"Failed to enqueue periodic T-Bank sync task: {exc}")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue

    main_logger.info("Connecting to Redis...")
    await redis_manager.connect()
    await taskiq_broker.startup()
    periodic_sync_task = asyncio.create_task(schedule_periodic_tbank_sync())
    try:
        yield
    finally:
        stop_event.set()
        await periodic_sync_task
        await taskiq_broker.shutdown()
        main_logger.info("Disconnecting from Redis...")
        await redis_manager.close()


app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)
main_router = APIRouter(prefix="/api/v1")
main_router.include_router(tbank_router)


@main_router.get("/openapi.json", include_in_schema=False)
async def custom_openapi():
    return JSONResponse(app.openapi())


@main_router.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url="/api/v1/openapi.json",
        title=app.title + " - Swagger UI",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css",
    )


app.include_router(main_router)


if __name__ == "__main__":
    uvicorn.run("src.main:app", reload=True)

from contextlib import asynccontextmanager

import uvicorn
from fastapi import APIRouter, FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse

from src.db.database import async_session_maker
from src.init import redis_manager
from src.moduls.auth.access_policy import public_access_meta
from src.moduls.auth.access_service import AccessService
from src.moduls.auth.auth_router import auth_router
from src.moduls.moex.moex_router import moex_router
from src.utils.db_manager import DBManager


@asynccontextmanager
async def lifespan(add: FastAPI):
    async with DBManager(async_session_maker) as db:
        async with db.transaction():
            await AccessService(db).sync_permissions_from_app(add)
    print("Подключение к Redis...")
    await redis_manager.connect()
    try:
        yield
    finally:
        print("Отключение от Redis...")
        await redis_manager.close()


app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)
main_router = APIRouter(prefix="/api/v1")
main_router.include_router(auth_router)
main_router.include_router(moex_router)


@main_router.get(
    "/openapi.json",
    include_in_schema=False,
    openapi_extra=public_access_meta(),
)
async def custom_openapi():
    return JSONResponse(app.openapi())


@main_router.get(
    "/docs",
    include_in_schema=False,
    openapi_extra=public_access_meta(),
)
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

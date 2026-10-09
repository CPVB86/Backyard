from contextlib import asynccontextmanager
import logging
from pathlib import Path
import secrets
import re
from fastapi import FastAPI, Request, Security
from fastapi.security import HTTPBearer
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.core.config import Settings
from app.core.database import create_database, initialize_database
from app.core.logging import configure_logging
from app.modules.birds.router import router as birds_router
from app.modules.observations.router import router as observations_router
from observations.policy import Policy
from generator.store import AssetStore
from generator.scheduler import Scheduler
from app.generator.router import router as generator_router
from app.modules.avian_visitors.router import router as avian_visitors_router
from app.modules.species.router import router as species_router
from app.modules.avian_collage_exporter.router import router as collage_router

logger = logging.getLogger("backyard.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        config = settings if settings is not None else Settings()
        token = config.api_token.get_secret_value()
        if not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token):
            raise RuntimeError("Configure a nonempty valid BACKYARD_API_TOKEN before starting the API")
        configure_logging(config.log_level)
        engine = create_database(config)
        scheduler = None
        try:
            initialize_database(engine)
            app.state.engine = engine
            app.state.settings = config
            app.state.generator = AssetStore(config.resolved_storage_root / "generator")
            scheduler = Scheduler(app.state.generator)
            app.state.generator_scheduler = scheduler
            scheduler.start()
            app.state.observation_policy = Policy.load(config.policy_path)
            logger.info("Backyard started")
            yield
        finally:
            if scheduler:
                scheduler.stop()
            engine.dispose()
            logger.info("Backyard stopped")

    app = FastAPI(title="Backyard", version="0.2.0", lifespan=lifespan,
                  dependencies=[Security(HTTPBearer(auto_error=False))])
    @app.middleware("http")
    async def authenticate_api(request: Request, call_next):
        if request.url.path == "/api" or request.url.path.startswith("/api/"):
            values = request.headers.getlist("authorization")
            parts = values[0].split(" ") if len(values) == 1 else []
            expected = request.app.state.settings.api_token.get_secret_value()
            if (len(parts) != 2 or parts[0].lower() != "bearer"
                    or not secrets.compare_digest(parts[1].encode("utf-8"), expected.encode("utf-8"))):
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"},
                                    headers={"WWW-Authenticate": "Bearer"})
        return await call_next(request)

    app.include_router(birds_router)
    app.include_router(observations_router)
    app.include_router(generator_router)
    app.include_router(avian_visitors_router)
    app.include_router(species_router)
    app.include_router(collage_router)
    app.mount("/avian-visitors", StaticFiles(
        directory=Path(__file__).parent / "modules" / "avian_visitors" / "static", html=True,
    ), name="avian-visitors")

    @app.exception_handler(SQLAlchemyError)
    async def database_failure(request, error):
        logger.error("Database operation failed")
        return JSONResponse(status_code=503, content={"detail": "Database unavailable; retry later"})

    @app.exception_handler(OSError)
    async def storage_failure(request, error):
        logger.error("Storage operation failed")
        return JSONResponse(status_code=503, content={"detail": "Storage unavailable; retry later"})

    @app.get("/api/health", tags=["core"])
    def health(request: Request):
        try:
            with request.app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            logger.error("Database health check failed")
            return JSONResponse(status_code=503, content={
                "status": "error", "database": "unavailable",
            })
        return {"status": "ok", "service": "backyard", "database": "ok"}

    return app


app = create_app()

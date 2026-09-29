from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.core.config import Settings
from app.core.database import create_database, initialize_database
from app.core.logging import configure_logging
from app.modules.birds.router import router as birds_router

logger = logging.getLogger("backyard.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        config = settings if settings is not None else Settings()
        configure_logging(config.log_level)
        engine = create_database(config)
        try:
            initialize_database(engine)
            app.state.engine = engine
            logger.info("Backyard started")
            yield
        finally:
            engine.dispose()
            logger.info("Backyard stopped")

    app = FastAPI(title="Backyard", version="0.1.0", lifespan=lifespan)
    app.include_router(birds_router)

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

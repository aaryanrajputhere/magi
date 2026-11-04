from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.api.campaigns import router as campaigns_router
from app.api.health import router as health_router
from app.api.retrieval import router as retrieval_router
from app.api.sources import router as sources_router
from app.memory import router as memory_router
from app.config import Settings, get_settings
from app.db import create_database_engine, initialize_database, session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_database_engine(config.magi_database_url)
        try:
            initialize_database(engine)
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            app.state.sessions = session_factory(engine)
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Magi API", version="0.1.0", lifespan=lifespan)
    app.include_router(memory_router, prefix="/api", tags=["memory"])
    app.include_router(campaigns_router, prefix="/api", tags=["campaigns"])
    app.include_router(health_router, prefix="/api", tags=["health"])
    app.include_router(sources_router, prefix="/api", tags=["sources"])
    app.include_router(retrieval_router, prefix="/api", tags=["retrieval"])
    app.dependency_overrides[get_settings] = lambda: config
    return app


app = create_app()

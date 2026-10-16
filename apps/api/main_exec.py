from apps.api.main import app
from apps.api.execution_routes import router as execution_router

app.include_router(execution_router)

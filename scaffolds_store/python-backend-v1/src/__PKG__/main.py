from fastapi import FastAPI

from __PKG__.routes import health

app = FastAPI(title="__APP__")
app.include_router(health.router)

from fastapi import FastAPI
from forge.api.routes import manifest

app = FastAPI(title="Forge API", version="0.1.0")

app.include_router(manifest.router)

# Future:
# from forge.api.routes import terminal
# app.include_router(terminal.router)

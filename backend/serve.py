"""Production entrypoint: Uvicorn serving the FastAPI API plus the built React SPA."""
import os
from pathlib import Path

import uvicorn
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server import app

STATIC_DIR = Path(__file__).parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"

if INDEX_FILE.is_file():
    app.mount("/static", StaticFiles(directory=STATIC_DIR / "static"), name="spa-static")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # API routes are registered first, so only non-API paths reach this handler.
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Endpoint tidak ditemukan")
        candidate = (STATIC_DIR / full_path).resolve()
        if full_path and candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(INDEX_FILE)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips="*",
        access_log=False,
    )

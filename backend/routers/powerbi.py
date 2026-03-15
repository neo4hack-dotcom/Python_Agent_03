"""
Router Power BI — sert les captures d'écran prises par l'agent Playwright.
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/powerbi", tags=["PowerBI"])

_SCREENSHOTS_BASE = Path("data/powerbi_screenshots")


@router.get("/screenshot/{filename}")
async def get_screenshot(filename: str):
    """Sert une capture d'écran Power BI par nom de fichier."""
    # Sanitize: only allow simple filenames (no path traversal)
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    filepath = _SCREENSHOTS_BASE / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(str(filepath), media_type="image/png")

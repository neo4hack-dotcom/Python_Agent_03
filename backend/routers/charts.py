"""
Router pour servir les fichiers générés (graphiques PNG, présentations PPTX).
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/charts", tags=["charts"])

_CHARTS_DIR = Path("data/charts")
_PRESENTATIONS_DIR = Path("data/presentations")
_SCRAPER_SCREENSHOTS_DIR = Path("data/web_scraper_screenshots")


@router.get("/image/{filename}")
async def get_chart_image(filename: str):
    """Serve a chart PNG file."""
    # Security: block path traversal
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    filepath = _CHARTS_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Chart not found")
    return FileResponse(str(filepath), media_type="image/png")


@router.get("/presentation/{filename}")
async def get_presentation(filename: str):
    """Serve a PPTX presentation file as download."""
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    filepath = _PRESENTATIONS_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Presentation not found")
    return FileResponse(
        str(filepath),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/scraper-screenshot/{filename}")
async def get_scraper_screenshot(filename: str):
    """Serve a web scraper screenshot."""
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    filepath = _SCRAPER_SCREENSHOTS_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(str(filepath), media_type="image/png")

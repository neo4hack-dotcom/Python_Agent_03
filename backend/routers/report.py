"""
Router pour la génération et le téléchargement de rapports PDF.

Endpoints :
  GET  /api/report/{report_id}/download  — télécharge un PDF généré
  GET  /api/report/list                  — liste les rapports disponibles
"""
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/report", tags=["Report"])


@router.get("/list")
def list_reports():
    """Liste tous les rapports PDF générés."""
    from backend.tools.pdf_generator import list_reports
    reports = list_reports()
    return [
        {
            "report_id": p.stem.replace("rapport_", ""),
            "filename": p.name,
            "size_bytes": p.stat().st_size,
            "created_at": p.stat().st_mtime,
        }
        for p in reports
    ]


@router.get("/{report_id}/download")
def download_report(report_id: str):
    """Télécharge un rapport PDF par son ID."""
    from backend.tools.pdf_generator import get_report_path
    path = get_report_path(report_id)
    if not path:
        raise HTTPException(404, f"Rapport '{report_id}' introuvable.")
    return FileResponse(
        path=str(path),
        media_type="application/pdf",
        filename=f"rapport_analyse_{report_id[:8]}.pdf",
        headers={"Content-Disposition": f'attachment; filename="rapport_analyse_{report_id[:8]}.pdf"'},
    )

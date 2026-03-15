"""
Générateur de PDF professionnels à partir de contenu Markdown.

Utilise weasyprint (HTML/CSS → PDF) pour produire des rapports d'analyse
de qualité publication avec :
  - Page de couverture (titre, date, agent, session)
  - Headers/footers avec numéros de page
  - Tables stylisées (header sombre, alternance de lignes)
  - Blocs SQL avec fond sombre (monospace)
  - Titres hiérarchiques (h1/h2/h3) avec couleurs corporate
  - Blockquotes encadrés pour les points clés

Répertoire de stockage : data/reports/
Format des noms de fichiers : rapport_{uuid}.pdf
"""
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import markdown as md

logger = logging.getLogger(__name__)

# Répertoire de stockage des PDF générés
_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "reports"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Template HTML + CSS ────────────────────────────────────────────────────────
_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<style>
/* ── Page setup ──────────────────────────────────────────────────────── */
@page {{
  size: A4;
  margin: 22mm 18mm 18mm 22mm;
}}

@page content {{
  @top-left   {{ content: "RAPPORT D'ANALYSE"; font-size: 7.5pt; color: #94a3b8; font-family: Helvetica, Arial, sans-serif; }}
  @top-right  {{ content: "Python Agent Platform"; font-size: 7.5pt; color: #94a3b8; font-family: Helvetica, Arial, sans-serif; }}
  @bottom-center {{ content: counter(page) " / " counter(pages); font-size: 7.5pt; color: #94a3b8; font-family: Helvetica, Arial, sans-serif; }}
}}

@page cover {{
  margin: 0;
  @top-left   {{ content: ""; }}
  @top-right  {{ content: ""; }}
  @bottom-center {{ content: ""; }}
}}

/* ── Base ────────────────────────────────────────────────────────────── */
* {{ box-sizing: border-box; }}

body {{
  font-family: Helvetica, Arial, sans-serif;
  font-size: 10pt;
  color: #1e293b;
  line-height: 1.65;
  margin: 0;
}}

/* ── Cover page ──────────────────────────────────────────────────────── */
.cover {{
  page: cover;
  page-break-after: always;
  width: 210mm;
  min-height: 297mm;
  background: #1a3a6c;
  color: #ffffff;
  padding: 0;
  display: block;
}}

.cover-top-bar {{
  background: #142d56;
  padding: 18pt 30pt;
  display: flex;
  align-items: center;
  gap: 12pt;
  border-bottom: 3pt solid #2563eb;
}}

.cover-logo-box {{
  width: 36pt;
  height: 36pt;
  background: rgba(255,255,255,0.15);
  border-radius: 6pt;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18pt;
  color: #93c5fd;
}}

.cover-platform {{
  font-size: 9pt;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #93c5fd;
}}

.cover-body {{
  padding: 50pt 40pt 30pt;
}}

.cover-label {{
  font-size: 8pt;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: #7dd3fc;
  font-weight: 700;
  margin-bottom: 14pt;
}}

.cover-title {{
  font-size: 26pt;
  font-weight: 800;
  line-height: 1.15;
  margin: 0 0 12pt;
  color: #ffffff;
}}

.cover-subtitle {{
  font-size: 12pt;
  color: #bfdbfe;
  margin-bottom: 40pt;
  line-height: 1.5;
}}

.cover-divider {{
  border: none;
  border-top: 1pt solid rgba(255,255,255,0.2);
  margin: 0 0 24pt;
}}

.cover-meta {{
  display: flex;
  flex-wrap: wrap;
  gap: 10pt 32pt;
}}

.cover-meta-item {{
  font-size: 9pt;
  color: #bfdbfe;
}}

.cover-meta-label {{
  color: #7dd3fc;
  font-weight: 700;
  font-size: 7.5pt;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  display: block;
  margin-bottom: 2pt;
}}

.cover-footer {{
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 10pt 30pt;
  background: rgba(0,0,0,0.2);
  font-size: 7.5pt;
  color: rgba(255,255,255,0.45);
  text-align: center;
}}

/* ── Content area ────────────────────────────────────────────────────── */
.content {{
  page: content;
}}

/* ── Headings ────────────────────────────────────────────────────────── */
h1 {{
  font-size: 17pt;
  color: #1a3a6c;
  font-weight: 800;
  margin: 22pt 0 10pt;
  padding-bottom: 5pt;
  border-bottom: 2pt solid #2563eb;
  page-break-after: avoid;
}}

h2 {{
  font-size: 13pt;
  color: #1a3a6c;
  font-weight: 700;
  margin: 16pt 0 6pt;
  padding-left: 8pt;
  border-left: 3pt solid #2563eb;
  page-break-after: avoid;
}}

h3 {{
  font-size: 11pt;
  color: #334155;
  font-weight: 600;
  margin: 10pt 0 4pt;
  page-break-after: avoid;
}}

h4 {{
  font-size: 10pt;
  color: #475569;
  font-weight: 600;
  margin: 8pt 0 4pt;
}}

/* ── Paragraphs & lists ──────────────────────────────────────────────── */
p {{
  margin: 0 0 7pt;
}}

ul, ol {{
  margin: 5pt 0 8pt;
  padding-left: 16pt;
}}

li {{
  margin: 2pt 0;
}}

strong, b {{
  color: #1a3a6c;
  font-weight: 700;
}}

em, i {{
  color: #475569;
}}

/* ── Horizontal rule ─────────────────────────────────────────────────── */
hr {{
  border: none;
  border-top: 1pt solid #e2e8f0;
  margin: 14pt 0;
}}

/* ── Tables ──────────────────────────────────────────────────────────── */
table {{
  width: 100%;
  border-collapse: collapse;
  margin: 10pt 0 14pt;
  font-size: 9pt;
  page-break-inside: avoid;
}}

thead tr {{
  background: #1a3a6c;
  color: #ffffff;
}}

th {{
  padding: 6pt 9pt;
  text-align: left;
  font-weight: 700;
  font-size: 8.5pt;
  letter-spacing: 0.03em;
  white-space: nowrap;
}}

td {{
  padding: 5pt 9pt;
  border-bottom: 1pt solid #e2e8f0;
  vertical-align: top;
}}

tbody tr:nth-child(even) td {{
  background: #f8fafc;
}}

tbody tr:last-child td {{
  border-bottom: 2pt solid #1a3a6c;
}}

/* ── Code blocks ─────────────────────────────────────────────────────── */
pre {{
  background: #0f172a;
  color: #e2e8f0;
  padding: 10pt 12pt;
  border-radius: 4pt;
  font-size: 8pt;
  font-family: "Courier New", Courier, monospace;
  margin: 8pt 0 12pt;
  page-break-inside: avoid;
  overflow: hidden;
  border-left: 3pt solid #2563eb;
}}

pre code {{
  background: transparent;
  color: #93c5fd;
  padding: 0;
  font-size: 8pt;
  border-radius: 0;
}}

code {{
  font-family: "Courier New", Courier, monospace;
  background: #f1f5f9;
  color: #1a3a6c;
  padding: 1pt 4pt;
  border-radius: 2pt;
  font-size: 9pt;
  border: 0.5pt solid #e2e8f0;
}}

/* ── Blockquotes ─────────────────────────────────────────────────────── */
blockquote {{
  border-left: 3pt solid #2563eb;
  background: #eff6ff;
  margin: 10pt 0;
  padding: 8pt 14pt;
  color: #1a3a6c;
  border-radius: 0 4pt 4pt 0;
}}

blockquote p {{
  margin: 0;
}}

/* ── Custom callout (via div.callout) ────────────────────────────────── */
.callout {{
  border: 1pt solid #bfdbfe;
  border-left: 4pt solid #2563eb;
  background: #eff6ff;
  padding: 8pt 14pt;
  border-radius: 4pt;
  margin: 10pt 0;
}}

/* ── First h1 after cover ────────────────────────────────────────────── */
.content > h1:first-child {{
  margin-top: 8pt;
}}

</style>
</head>
<body>

<!-- ── COVER PAGE ──────────────────────────────────────────────────────── -->
<div class="cover">
  <div class="cover-top-bar">
    <div class="cover-logo-box">📊</div>
    <div class="cover-platform">Python Agent Platform</div>
  </div>
  <div class="cover-body">
    <div class="cover-label">Rapport d'Analyse Automatisé</div>
    <div class="cover-title">{title}</div>
    <div class="cover-subtitle">{subtitle}</div>
    <hr class="cover-divider">
    <div class="cover-meta">
      <div class="cover-meta-item">
        <span class="cover-meta-label">Date de génération</span>
        {date}
      </div>
      <div class="cover-meta-item">
        <span class="cover-meta-label">Agent</span>
        {agent_name}
      </div>
      <div class="cover-meta-item">
        <span class="cover-meta-label">Session</span>
        {session_id_short}
      </div>
    </div>
  </div>
  <div class="cover-footer">Généré automatiquement par Python Agent Platform — Confidentiel</div>
</div>

<!-- ── CONTENT ─────────────────────────────────────────────────────────── -->
<div class="content">
{content_html}
</div>

</body>
</html>
"""


def generate_pdf(
    markdown_content: str,
    title: str,
    subtitle: str = "",
    agent_name: str = "Agent Analyste",
    session_id: str = "",
    output_filename: Optional[str] = None,
) -> str:
    """
    Convertit un contenu Markdown en PDF professionnel.

    Args:
        markdown_content: Contenu du rapport en Markdown (GFM supporté).
        title: Titre principal affiché sur la page de couverture.
        subtitle: Sous-titre (ex: période analysée, scope).
        agent_name: Nom de l'agent qui a produit l'analyse.
        session_id: ID de session (affiché abrégé sur la couverture).
        output_filename: Nom du fichier PDF de sortie. Si None, génère un UUID.

    Returns:
        Chemin absolu vers le fichier PDF généré.

    Raises:
        RuntimeError: Si la génération weasyprint échoue.
    """
    try:
        import weasyprint
    except ImportError:
        raise RuntimeError(
            "weasyprint is required for PDF generation. "
            "Install it with: pip install weasyprint"
        )

    # Génère le nom de fichier si non fourni
    if not output_filename:
        report_id = str(uuid.uuid4())
        output_filename = f"rapport_{report_id}.pdf"
    else:
        report_id = output_filename.replace("rapport_", "").replace(".pdf", "")

    output_path = _DATA_DIR / output_filename

    # Markdown → HTML (avec extensions GFM-like)
    md_converter = md.Markdown(
        extensions=["tables", "fenced_code", "codehilite", "nl2br", "sane_lists"],
        extension_configs={
            "codehilite": {"guess_lang": False, "noclasses": True},
        },
    )
    content_html = md_converter.convert(markdown_content)

    # Injecter dans le template HTML
    html = _HTML_TEMPLATE.format(
        title=title or "Rapport d'Analyse",
        subtitle=subtitle or "",
        date=datetime.now().strftime("%d/%m/%Y à %H:%M"),
        agent_name=agent_name,
        session_id_short=session_id[:8] if session_id else "N/A",
        content_html=content_html,
    )

    # HTML → PDF via weasyprint
    try:
        doc = weasyprint.HTML(string=html)
        doc.write_pdf(str(output_path))
        logger.info("PDF generated: %s (%d bytes)", output_path, output_path.stat().st_size)
    except Exception as e:
        raise RuntimeError(f"weasyprint PDF generation failed: {e}") from e

    return str(output_path)


def get_report_path(report_id: str) -> Optional[Path]:
    """Retourne le chemin du PDF si il existe, None sinon."""
    path = _DATA_DIR / f"rapport_{report_id}.pdf"
    return path if path.exists() else None


def list_reports() -> list:
    """Liste tous les PDF générés dans data/reports/."""
    return sorted(_DATA_DIR.glob("rapport_*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)

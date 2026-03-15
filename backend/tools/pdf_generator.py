"""
Générateur de PDF professionnels à partir de contenu Markdown.

Utilise reportlab (pur Python, zéro dépendance système) pour produire des
rapports d'analyse de qualité publication avec :
  - Page de couverture (titre, date, agent, session)
  - En-tête et pied de page avec numéros de page sur chaque page
  - Tables stylisées (header marine, alternance de lignes)
  - Blocs code/SQL avec fond sombre (monospace)
  - Titres hiérarchiques h1/h2/h3 avec couleurs corporate
  - Blockquotes encadrés pour les points clés

Répertoire de stockage : data/reports/
Format des noms de fichiers : rapport_{uuid}.pdf
"""
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Répertoire de stockage des PDF générés
_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "reports"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Couleurs corporate ────────────────────────────────────────────────────────
_NAVY      = (0.102, 0.227, 0.424)   # #1a3a6c
_BLUE      = (0.145, 0.392, 0.922)   # #2563eb
_LIGHT_BG  = (0.973, 0.984, 1.000)   # #f8fafc
_SLATE     = (0.200, 0.255, 0.357)   # #334155
_DARK_BG   = (0.059, 0.090, 0.165)   # #0f172a
_CODE_FG   = (0.580, 0.773, 0.992)   # #93c5fd
_WHITE     = (1, 1, 1)
_LIGHT_BLUE = (0.937, 0.965, 1.000)  # #eff6ff


def _rgb(*t):
    from reportlab.lib.colors import Color
    return Color(*t)


# ── Parseur Markdown simplifié ────────────────────────────────────────────────

def _parse_markdown(text: str) -> list:
    """
    Convertit le Markdown en une liste de blocs structurés.

    Retourne une liste de dicts :
      {"type": "h1"|"h2"|"h3"|"paragraph"|"code"|"table"|"hr"|"blockquote"|"list_item", ...}
    """
    blocks = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]

        # Code block (fenced ```)
        if line.strip().startswith("```"):
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            blocks.append({"type": "code", "text": "\n".join(code_lines)})
            i += 1
            continue

        # Horizontal rule
        if re.match(r"^(-{3,}|_{3,}|\*{3,})\s*$", line.strip()):
            blocks.append({"type": "hr"})
            i += 1
            continue

        # Headings
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            blocks.append({"type": f"h{min(level, 3)}", "text": m.group(2).strip()})
            i += 1
            continue

        # Blockquote
        if line.startswith("> ") or line.startswith(">"):
            quote_lines = []
            while i < len(lines) and (lines[i].startswith("> ") or lines[i].startswith(">")):
                quote_lines.append(lines[i].lstrip("> ").strip())
                i += 1
            blocks.append({"type": "blockquote", "text": " ".join(quote_lines)})
            continue

        # List items (- / * / 1.)
        if re.match(r"^\s*[-*]\s+", line) or re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and (re.match(r"^\s*[-*]\s+", lines[i]) or re.match(r"^\s*\d+\.\s+", lines[i])):
                item_text = re.sub(r"^\s*[-*\d.]+\s+", "", lines[i])
                items.append(item_text.strip())
                i += 1
            blocks.append({"type": "list", "items": items})
            continue

        # Table (line with |)
        if "|" in line and i + 1 < len(lines) and re.match(r"^\|?[\s\-|:]+\|?$", lines[i + 1]):
            table_rows = []
            while i < len(lines) and "|" in lines[i]:
                if re.match(r"^\|?[\s\-|:]+\|?$", lines[i]):
                    i += 1
                    continue
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                table_rows.append(cells)
                i += 1
            if table_rows:
                blocks.append({"type": "table", "rows": table_rows})
            continue

        # Empty line → skip
        if not line.strip():
            i += 1
            continue

        # Paragraph: accumulate until empty line
        para_lines = []
        while i < len(lines) and lines[i].strip() and not lines[i].startswith("#") and not lines[i].startswith(">") and "|" not in lines[i] and not lines[i].strip().startswith("```") and not re.match(r"^\s*[-*\d.]+\s+", lines[i]):
            para_lines.append(lines[i].strip())
            i += 1
        if para_lines:
            blocks.append({"type": "paragraph", "text": " ".join(para_lines)})
        else:
            i += 1

    return blocks


def _inline_style(text: str) -> str:
    """Convertit **bold**, *italic*, `code` en balises XML reportlab."""
    # Escape XML first
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"_(.+?)_", r"<i>\1</i>", text)
    # Inline code
    text = re.sub(r"`(.+?)`", r'<font name="Courier" color="#1a3a6c">\1</font>', text)
    # Links — just show the text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text


# ── Numérotation de pages (canvas callback) ────────────────────────────────────

class _PageTemplate:
    def __init__(self, title: str, agent_name: str):
        self.title = title
        self.agent_name = agent_name

    def __call__(self, canvas, doc):
        from reportlab.lib.units import mm
        from reportlab.lib.colors import Color

        page_num = doc.page
        w, h = canvas._pagesize

        # Skip cover page
        if page_num == 1:
            return

        canvas.saveState()

        # Header bar
        canvas.setFillColor(Color(*_NAVY))
        canvas.rect(0, h - 10 * mm, w, 10 * mm, fill=1, stroke=0)

        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(Color(0.580, 0.773, 0.992))  # light blue
        canvas.drawString(15 * mm, h - 7 * mm, "RAPPORT D'ANALYSE")

        canvas.setFillColor(Color(0.8, 0.87, 1.0))
        canvas.drawRightString(w - 15 * mm, h - 7 * mm, self.agent_name)

        # Footer
        canvas.setFillColor(Color(0.9, 0.92, 0.96))
        canvas.setFont("Helvetica", 7)
        canvas.drawString(15 * mm, 7 * mm, "Python Agent Platform — Confidentiel")
        canvas.drawRightString(w - 15 * mm, 7 * mm, f"Page {page_num}")

        # Footer line
        canvas.setStrokeColor(Color(*_BLUE))
        canvas.setLineWidth(0.5)
        canvas.line(15 * mm, 10 * mm, w - 15 * mm, 10 * mm)

        canvas.restoreState()


# ── Fonctions de construction de la couverture ─────────────────────────────────

def _build_cover(story, title: str, subtitle: str, date_str: str, agent_name: str, session_short: str):
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle, PageBreak, HRFlowable
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.colors import Color
    from reportlab.platypus import KeepTogether

    # Cover background is drawn via the canvas; we use a table as a color block
    cover_data = [[""]]
    cover_table = Table(cover_data, colWidths=[180 * mm], rowHeights=[260 * mm])
    cover_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), Color(*_NAVY)),
        ("LEFTPADDING",  (0, 0), (-1, -1), 30),
        ("TOPPADDING",   (0, 0), (-1, -1), 40),
    ]))
    story.append(cover_table)

    # We build cover content as a layered Paragraph block approach
    # instead of the table approach so it overlays properly — use a Frame directly via canvas
    # Simpler: just use styled paragraphs with heavy top padding for cover effect

    story.clear()

    # ── Logo bar ────────────────────────────────────────────────────────────
    logo_style = ParagraphStyle("logo",
        fontName="Helvetica-Bold", fontSize=8, textColor=Color(0.58, 0.77, 0.99),
        letterSpacing=2, spaceAfter=4 * mm,
        backColor=Color(0.078, 0.176, 0.337),
        leftIndent=0, borderPadding=(6, 12, 6, 12))
    story.append(Paragraph("▪  PYTHON AGENT PLATFORM", logo_style))
    story.append(HRFlowable(width="100%", thickness=2, color=Color(*_BLUE), spaceAfter=14 * mm))

    # ── Tag ─────────────────────────────────────────────────────────────────
    tag_style = ParagraphStyle("tag",
        fontName="Helvetica-Bold", fontSize=7.5, textColor=Color(0.49, 0.83, 0.99),
        letterSpacing=2.5, spaceAfter=6 * mm)
    story.append(Paragraph("RAPPORT D'ANALYSE AUTOMATISÉ", tag_style))

    # ── Title ────────────────────────────────────────────────────────────────
    title_style = ParagraphStyle("cvrtitle",
        fontName="Helvetica-Bold", fontSize=26, textColor=Color(*_WHITE),
        leading=32, spaceAfter=8 * mm)
    story.append(Paragraph(title, title_style))

    # ── Subtitle ─────────────────────────────────────────────────────────────
    if subtitle:
        sub_style = ParagraphStyle("cvrsub",
            fontName="Helvetica", fontSize=12, textColor=Color(0.75, 0.86, 1.0),
            leading=18, spaceAfter=10 * mm)
        story.append(Paragraph(subtitle, sub_style))

    story.append(HRFlowable(width="100%", thickness=0.5, color=Color(1, 1, 1, 0.25), spaceAfter=10 * mm))

    # ── Meta info ─────────────────────────────────────────────────────────────
    meta_label = ParagraphStyle("metalabel",
        fontName="Helvetica-Bold", fontSize=7, textColor=Color(0.49, 0.83, 0.99),
        letterSpacing=1.5, spaceAfter=1 * mm)
    meta_value = ParagraphStyle("metaval",
        fontName="Helvetica", fontSize=9, textColor=Color(0.75, 0.86, 1.0),
        spaceAfter=6 * mm)

    story.append(Paragraph("DATE DE GÉNÉRATION", meta_label))
    story.append(Paragraph(date_str, meta_value))
    story.append(Paragraph("AGENT", meta_label))
    story.append(Paragraph(agent_name, meta_value))
    story.append(Paragraph("SESSION", meta_label))
    story.append(Paragraph(session_short or "N/A", meta_value))

    story.append(Spacer(1, 16 * mm))
    story.append(PageBreak())


# ── Builder principal ─────────────────────────────────────────────────────────

def _build_content(story, blocks: list):
    from reportlab.platypus import (
        Paragraph, Spacer, Table, TableStyle, HRFlowable, Preformatted
    )
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.colors import Color

    def _style(name, **kw):
        return ParagraphStyle(name, **kw)

    h1_style = _style("h1",
        fontName="Helvetica-Bold", fontSize=17, textColor=Color(*_NAVY),
        leading=22, spaceBefore=14 * mm, spaceAfter=4 * mm,
        borderPadding=(0, 0, 3, 0),
        underlineWidth=1.5, underlineColor=Color(*_BLUE))

    h2_style = _style("h2",
        fontName="Helvetica-Bold", fontSize=13, textColor=Color(*_NAVY),
        leading=18, spaceBefore=8 * mm, spaceAfter=3 * mm,
        leftIndent=8, borderPadding=(0, 0, 0, 6),
        borderColor=Color(*_BLUE), borderLeftWidth=3, borderLeftPadding=6)

    h3_style = _style("h3",
        fontName="Helvetica-Bold", fontSize=11, textColor=Color(*_SLATE),
        leading=16, spaceBefore=5 * mm, spaceAfter=2 * mm)

    para_style = _style("para",
        fontName="Helvetica", fontSize=10, textColor=Color(*_SLATE),
        leading=16, spaceAfter=4 * mm, wordWrap="LTR",
        allowWidows=0, allowOrphans=0)

    bq_style = _style("bq",
        fontName="Helvetica-Oblique", fontSize=10, textColor=Color(*_NAVY),
        leading=16, leftIndent=14, spaceAfter=4 * mm,
        backColor=Color(*_LIGHT_BLUE),
        borderPadding=(6, 10, 6, 10),
        borderLeftColor=Color(*_BLUE), borderLeftWidth=3, borderLeftPadding=10)

    bullet_style = _style("bullet",
        fontName="Helvetica", fontSize=10, textColor=Color(*_SLATE),
        leading=15, leftIndent=16, firstLineIndent=0,
        spaceAfter=1 * mm)

    for block in blocks:
        btype = block["type"]

        if btype == "h1":
            story.append(Paragraph(_inline_style(block["text"]), h1_style))
            story.append(HRFlowable(width="100%", thickness=1.5, color=Color(*_BLUE), spaceAfter=3 * mm))

        elif btype == "h2":
            story.append(Paragraph(_inline_style(block["text"]), h2_style))

        elif btype == "h3":
            story.append(Paragraph(_inline_style(block["text"]), h3_style))

        elif btype == "paragraph":
            story.append(Paragraph(_inline_style(block["text"]), para_style))

        elif btype == "blockquote":
            story.append(Paragraph(_inline_style(block["text"]), bq_style))

        elif btype == "list":
            for item in block["items"]:
                story.append(Paragraph("• " + _inline_style(item), bullet_style))
            story.append(Spacer(1, 2 * mm))

        elif btype == "hr":
            story.append(HRFlowable(width="100%", thickness=0.5,
                                    color=Color(0.882, 0.91, 0.941),
                                    spaceBefore=4 * mm, spaceAfter=4 * mm))

        elif btype == "code":
            code_style = _style("code",
                fontName="Courier", fontSize=8, textColor=Color(*_CODE_FG),
                leading=13, backColor=Color(*_DARK_BG),
                borderPadding=(8, 10, 8, 10),
                borderLeftColor=Color(*_BLUE), borderLeftWidth=2.5, borderLeftPadding=10,
                spaceAfter=5 * mm)
            # Use Paragraph with <pre>-like formatting (no wrapping issues via Preformatted)
            from reportlab.platypus import Preformatted as Pre
            code_text = block["text"]
            # Truncate very long lines to avoid overflow
            safe_lines = []
            for ln in code_text.split("\n"):
                if len(ln) > 110:
                    ln = ln[:107] + "…"
                safe_lines.append(ln.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

            pre = Pre("\n".join(safe_lines), _style("pre",
                fontName="Courier", fontSize=8, textColor=Color(*_CODE_FG),
                leading=12, backColor=Color(*_DARK_BG),
                leftIndent=10, rightIndent=4,
                spaceBefore=2 * mm, spaceAfter=5 * mm,
                borderPadding=(8, 10, 8, 12)))
            story.append(pre)

        elif btype == "table":
            rows = block["rows"]
            if not rows:
                continue
            max_cols = max(len(r) for r in rows)
            # Pad rows to same width
            rows = [r + [""] * (max_cols - len(r)) for r in rows]

            col_w = (170 * mm) / max_cols
            table = Table(rows, colWidths=[col_w] * max_cols, repeatRows=1)
            style = [
                ("BACKGROUND",   (0, 0), (-1, 0),  Color(*_NAVY)),
                ("TEXTCOLOR",    (0, 0), (-1, 0),  Color(*_WHITE)),
                ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
                ("FONTSIZE",     (0, 0), (-1, 0),  8),
                ("FONTNAME",     (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE",     (0, 1), (-1, -1), 8.5),
                ("TEXTCOLOR",    (0, 1), (-1, -1), Color(*_SLATE)),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [Color(*_WHITE), Color(*_LIGHT_BG)]),
                ("LINEBELOW",    (0, 0), (-1, -2), 0.5, Color(0.882, 0.91, 0.941)),
                ("LINEBELOW",    (0, -1), (-1, -1), 1.5, Color(*_NAVY)),
                ("LEFTPADDING",  (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING",   (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
                ("VALIGN",       (0, 0), (-1, -1), "TOP"),
                ("WORDWRAP",     (0, 0), (-1, -1), True),
            ]
            table.setStyle(TableStyle(style))
            story.append(table)
            story.append(Spacer(1, 4 * mm))


# ── API publique ───────────────────────────────────────────────────────────────

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

    Utilise reportlab (pur Python, aucune dépendance système).

    Args:
        markdown_content: Contenu du rapport en Markdown.
        title: Titre principal (page de couverture).
        subtitle: Sous-titre / scope.
        agent_name: Nom de l'agent.
        session_id: ID de session (affiché abrégé).
        output_filename: Nom du fichier PDF. Si None, génère un UUID.

    Returns:
        Chemin absolu vers le fichier PDF généré.
    """
    from reportlab.platypus import SimpleDocTemplate, Spacer
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm

    # Génère le nom de fichier si non fourni
    if not output_filename:
        report_id = str(uuid.uuid4())
        output_filename = f"rapport_{report_id}.pdf"
    else:
        report_id = output_filename.replace("rapport_", "").replace(".pdf", "")

    output_path = _DATA_DIR / output_filename
    date_str = datetime.now().strftime("%d/%m/%Y à %H:%M")
    session_short = session_id[:8] if session_id else "N/A"

    page_template = _PageTemplate(title=title, agent_name=agent_name)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=22 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        onPage=page_template,
    )

    story = []

    # ── Cover page ────────────────────────────────────────────────────────────
    _build_cover(story, title, subtitle, date_str, agent_name, session_short)

    # ── Content ───────────────────────────────────────────────────────────────
    story.append(Spacer(1, 4 * mm))
    blocks = _parse_markdown(markdown_content)
    _build_content(story, blocks)

    # ── Build PDF ─────────────────────────────────────────────────────────────
    try:
        doc.build(story)
        logger.info("PDF generated: %s (%d bytes)", output_path, output_path.stat().st_size)
    except Exception as e:
        raise RuntimeError(f"reportlab PDF generation failed: {e}") from e

    return str(output_path)


def get_report_path(report_id: str) -> Optional[Path]:
    """Retourne le chemin du PDF s'il existe, None sinon."""
    path = _DATA_DIR / f"rapport_{report_id}.pdf"
    return path if path.exists() else None


def list_reports() -> list:
    """Liste tous les PDF générés dans data/reports/."""
    return sorted(_DATA_DIR.glob("rapport_*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)

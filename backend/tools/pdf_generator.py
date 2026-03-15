"""
Générateur de PDF professionnels à partir de contenu Markdown.

Utilise reportlab (pur Python, zéro dépendance système) pour produire des
rapports d'analyse de qualité publication avec :
  - Page de couverture moderne avec design graphique soigné
  - En-tête et pied de page sur chaque page (sauf couverture)
  - Tables bien rendues avec header coloré et lignes alternées
  - Blocs code/SQL avec fond sombre et bordure bleue
  - Titres hiérarchiques avec barres d'accent colorées
  - Blockquotes encadrés pour les points clés
  - Listes à puces

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

_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "reports"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Palette ───────────────────────────────────────────────────────────────────
# Deep navy / corporate blue palette
_NAVY      = (0.071, 0.149, 0.353)   # #122459  deep navy
_BLUE      = (0.133, 0.459, 0.922)   # #2275EB  bright blue
_BLUE_MID  = (0.231, 0.510, 0.965)   # #3B82F6  medium blue
_BLUE_SOFT = (0.867, 0.922, 1.000)   # #DDEBFF  soft blue
_SLATE     = (0.118, 0.161, 0.235)   # #1E293B  dark text
_MUTED     = (0.392, 0.455, 0.545)   # #647489  muted text
_BORDER    = (0.890, 0.914, 0.945)   # #E3E9F1  border
_LIGHT_BG  = (0.973, 0.980, 0.992)   # #F8FAFC  alternating row
_WHITE     = (1.0,   1.0,   1.0)
_DARK_BG   = (0.059, 0.090, 0.165)   # #0F1729  code bg
_CODE_FG   = (0.569, 0.780, 0.992)   # #91C7FD  code text
_ACCENT    = (0.059, 0.788, 0.659)   # #0FC9A8  teal accent for highlights
_COVER_BOT = (0.039, 0.110, 0.298)   # #0A1C4C  cover bottom
_COVER_TOP = (0.071, 0.200, 0.510)   # #123382  cover top
_TAG_COL   = (0.400, 0.718, 1.000)   # #66B7FF  cover tag text
_ORANGE    = (0.961, 0.494, 0.071)   # #F57E12  warning / highlight


def _rgb(*t):
    from reportlab.lib.colors import Color
    return Color(*t)


# ── Parseur Markdown ──────────────────────────────────────────────────────────

def _is_table_separator(line: str) -> bool:
    """True if line is a Markdown table separator row (|---|---|)."""
    stripped = line.strip()
    if not stripped:
        return False
    # Allow optional outer pipes; cells must consist of dashes/colons/spaces only
    inner = stripped.strip("|")
    cells = inner.split("|")
    return len(cells) >= 1 and all(re.match(r"^[\s\-:]+$", c) for c in cells)


def _split_table_row(line: str) -> list:
    """Split a markdown table row into cells, stripping outer pipes."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _parse_markdown(text: str) -> list:
    """
    Convertit le Markdown en une liste de blocs structurés.

    Retourne des dicts :
      {"type": "h1"|"h2"|"h3"|"paragraph"|"code"|"table"|"hr"|"blockquote"|"list", ...}
    """
    blocks = []
    lines = text.split("\n")
    n = len(lines)
    i = 0

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # ── Fenced code block ────────────────────────────────────────────────
        if stripped.startswith("```"):
            code_lines = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            blocks.append({"type": "code", "text": "\n".join(code_lines)})
            i += 1
            continue

        # ── Horizontal rule ──────────────────────────────────────────────────
        if re.match(r"^(-{3,}|_{3,}|\*{3,})\s*$", stripped):
            blocks.append({"type": "hr"})
            i += 1
            continue

        # ── Heading ──────────────────────────────────────────────────────────
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            blocks.append({"type": f"h{min(level, 3)}", "text": m.group(2).strip()})
            i += 1
            continue

        # ── Blockquote ───────────────────────────────────────────────────────
        if stripped.startswith(">"):
            quote_lines = []
            while i < n and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].lstrip(">").strip())
                i += 1
            blocks.append({"type": "blockquote", "text": " ".join(quote_lines)})
            continue

        # ── List ─────────────────────────────────────────────────────────────
        if re.match(r"^\s*[-*+]\s+", line) or re.match(r"^\s*\d+[.)]\s+", line):
            items = []
            while i < n and (re.match(r"^\s*[-*+]\s+", lines[i]) or re.match(r"^\s*\d+[.)]\s+", lines[i])):
                item_text = re.sub(r"^\s*[-*+\d.)]+\s+", "", lines[i])
                items.append(item_text.strip())
                i += 1
            blocks.append({"type": "list", "items": items})
            continue

        # ── Table ─────────────────────────────────────────────────────────────
        # Detect: current line has | AND either next line is a separator OR
        # (current line is a separator — we already consumed the header)
        if "|" in line:
            # Look for separator in the next 2 lines
            sep_idx = None
            for look in (i + 1, i + 2):
                if look < n and _is_table_separator(lines[look]):
                    sep_idx = look
                    break

            if sep_idx is not None:
                # Header row is the line before the separator
                header_idx = sep_idx - 1
                table_rows = []

                # Collect header row(s) from i to sep_idx-1
                for hi in range(i, sep_idx):
                    if "|" in lines[hi] and not _is_table_separator(lines[hi]):
                        table_rows.append(_split_table_row(lines[hi]))

                # Skip separator
                i = sep_idx + 1

                # Collect data rows
                while i < n and "|" in lines[i]:
                    if not _is_table_separator(lines[i]):
                        table_rows.append(_split_table_row(lines[i]))
                    i += 1

                if table_rows:
                    blocks.append({"type": "table", "rows": table_rows})
                continue

        # ── Empty line ───────────────────────────────────────────────────────
        if not stripped:
            i += 1
            continue

        # ── Paragraph ────────────────────────────────────────────────────────
        para_lines = []
        while i < n:
            ln = lines[i]
            s = ln.strip()
            if not s:
                break
            if s.startswith("#") or s.startswith(">") or s.startswith("```"):
                break
            if "|" in ln and i + 1 < n and _is_table_separator(lines[i + 1]):
                break
            if re.match(r"^\s*[-*+]\s+", ln) or re.match(r"^\s*\d+[.)]\s+", ln):
                break
            if re.match(r"^(-{3,}|_{3,}|\*{3,})\s*$", s):
                break
            para_lines.append(s)
            i += 1
        if para_lines:
            blocks.append({"type": "paragraph", "text": " ".join(para_lines)})
        elif i < n and not lines[i].strip():
            i += 1
        else:
            i += 1  # safety: avoid infinite loop

    return blocks


def _inline_style(text: str) -> str:
    """Convertit **bold**, *italic*, `code` en balises XML ReportLab."""
    # Escape XML characters
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Bold  (**text** or __text__)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    # Italic  (*text* or _text_) — avoid clashing with **
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"<i>\1</i>", text)
    # Inline code  `text`
    text = re.sub(
        r"`(.+?)`",
        r'<font name="Courier" color="#1e3a8a" backColor="#dbeafe">\1</font>',
        text,
    )
    # Links — show text only
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text


# ── Page template (header / footer) ──────────────────────────────────────────

class _PageTemplate:
    def __init__(self, title: str, agent_name: str):
        self.title = title
        self.agent_name = agent_name

    def __call__(self, canvas, doc):
        from reportlab.lib.units import mm
        from reportlab.lib.colors import Color

        page_num = doc.page
        if page_num == 1:
            return  # cover page — no header/footer

        canvas.saveState()
        w, h = canvas._pagesize

        # ── Header bar ──────────────────────────────────────────────────────
        header_h = 9 * mm
        # Dark background strip
        canvas.setFillColor(Color(*_NAVY))
        canvas.rect(0, h - header_h, w, header_h, fill=1, stroke=0)
        # Bright accent left bar
        canvas.setFillColor(Color(*_BLUE))
        canvas.rect(0, h - header_h, 3.5 * mm, header_h, fill=1, stroke=0)

        canvas.setFont("Helvetica-Bold", 6.5)
        canvas.setFillColor(Color(*_TAG_COL))
        canvas.drawString(7 * mm, h - 6 * mm, "RAPPORT D'ANALYSE")

        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(Color(0.78, 0.88, 1.0))
        # Truncate long agent name
        aname = self.agent_name if len(self.agent_name) <= 40 else self.agent_name[:37] + "…"
        canvas.drawRightString(w - 7 * mm, h - 6 * mm, aname)

        # ── Footer ──────────────────────────────────────────────────────────
        canvas.setStrokeColor(Color(*_BORDER))
        canvas.setLineWidth(0.5)
        canvas.line(7 * mm, 10 * mm, w - 7 * mm, 10 * mm)

        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(Color(*_MUTED))
        canvas.drawString(7 * mm, 6.5 * mm, "Python Agent Platform — Confidentiel")
        canvas.setFillColor(Color(*_BLUE_MID))
        canvas.setFont("Helvetica-Bold", 6.5)
        canvas.drawRightString(w - 7 * mm, 6.5 * mm, f"Page {page_num}")

        canvas.restoreState()


# ── Cover page ────────────────────────────────────────────────────────────────

def _build_cover(story, title: str, subtitle: str, date_str: str, agent_name: str, session_short: str):
    from reportlab.platypus import Paragraph, Spacer, PageBreak, HRFlowable
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.colors import Color
    from reportlab.platypus import KeepTogether, Image
    import io

    # We build the cover with a canvas-drawn background via a flowable trick:
    # Use a "cover flowable" that draws the background on the canvas.
    from reportlab.platypus.flowables import Flowable

    class CoverBackground(Flowable):
        def draw(self):
            from reportlab.lib.pagesizes import A4
            w, h = A4
            c = self.canv

            # Dark gradient simulation: stack rectangles from top to bottom
            steps = 14
            for k in range(steps):
                t = k / (steps - 1)
                # Interpolate _COVER_TOP → _COVER_BOT
                r = _COVER_TOP[0] + t * (_COVER_BOT[0] - _COVER_TOP[0])
                g = _COVER_TOP[1] + t * (_COVER_BOT[1] - _COVER_TOP[1])
                b = _COVER_TOP[2] + t * (_COVER_BOT[2] - _COVER_TOP[2])
                strip_h = h / steps
                c.setFillColor(Color(r, g, b))
                c.rect(0, h - (k + 1) * strip_h, w, strip_h + 1, fill=1, stroke=0)

            # Decorative accent rectangle bottom-left
            c.setFillColor(Color(*_BLUE, 0.35))
            c.rect(0, 0, 60 * mm, 55 * mm, fill=1, stroke=0)

            # Decorative accent line top-right
            c.setFillColor(Color(*_ACCENT, 0.6))
            c.rect(w - 45 * mm, h - 5 * mm, 45 * mm, 2.5 * mm, fill=1, stroke=0)

            # Thin horizontal divider at ~65% height
            c.setStrokeColor(Color(1, 1, 1, 0.15))
            c.setLineWidth(0.5)
            y_div = h * 0.38
            c.line(22 * mm, y_div, w - 22 * mm, y_div)

        def wrap(self, aw, ah):
            return (0, 0)

    story.clear()
    story.append(CoverBackground())

    def _sp(name, **kw):
        return ParagraphStyle(name, **kw)

    # Top bar label
    story.append(Spacer(1, 16 * mm))
    story.append(Paragraph(
        "▸  PYTHON AGENT PLATFORM",
        _sp("logo",
            fontName="Helvetica-Bold", fontSize=7.5,
            textColor=Color(*_TAG_COL),
            letterSpacing=2.5, spaceAfter=2 * mm),
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=Color(*_BLUE, 0.6), spaceAfter=20 * mm))

    # Report type tag
    story.append(Paragraph(
        "RAPPORT D'ANALYSE AUTOMATISÉ",
        _sp("tag",
            fontName="Helvetica-Bold", fontSize=8,
            textColor=Color(*_ACCENT),
            letterSpacing=3, spaceAfter=5 * mm),
    ))

    # Main title
    safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    story.append(Paragraph(
        safe_title,
        _sp("cvrtitle",
            fontName="Helvetica-Bold", fontSize=28,
            textColor=Color(*_WHITE),
            leading=36, spaceAfter=8 * mm),
    ))

    # Subtitle
    if subtitle:
        safe_sub = subtitle.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        story.append(Paragraph(
            safe_sub,
            _sp("cvrsub",
                fontName="Helvetica", fontSize=12,
                textColor=Color(0.75, 0.86, 1.0),
                leading=18, spaceAfter=14 * mm),
        ))
    else:
        story.append(Spacer(1, 14 * mm))

    story.append(HRFlowable(width="100%", thickness=0.5, color=Color(1, 1, 1, 0.2), spaceAfter=10 * mm))

    # Metadata table
    def _ml(txt):
        return Paragraph(txt, _sp("ml",
            fontName="Helvetica-Bold", fontSize=6.5,
            textColor=Color(*_TAG_COL), letterSpacing=1.5))

    def _mv(txt):
        safe = txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return Paragraph(safe, _sp("mv",
            fontName="Helvetica", fontSize=9.5,
            textColor=Color(0.85, 0.92, 1.0), leading=14))

    meta_data = [
        [_ml("DATE"), _ml("AGENT"), _ml("SESSION")],
        [_mv(date_str), _mv(agent_name), _mv(session_short or "N/A")],
    ]
    meta_table = Table(meta_data, colWidths=[55 * mm, 75 * mm, 40 * mm])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), Color(0, 0, 0, 0)),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(meta_table)

    story.append(Spacer(1, 18 * mm))
    story.append(PageBreak())


# ── Content builder ───────────────────────────────────────────────────────────

def _build_content(story, blocks: list):
    from reportlab.platypus import (
        Paragraph, Spacer, Table, TableStyle, HRFlowable, Preformatted, KeepTogether
    )
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.colors import Color
    from reportlab.platypus.flowables import Flowable

    def _sp(name, **kw):
        return ParagraphStyle(name, **kw)

    # ── Style definitions ────────────────────────────────────────────────────
    h1_style = _sp("h1",
        fontName="Helvetica-Bold", fontSize=18,
        textColor=Color(*_NAVY),
        leading=24, spaceBefore=14 * mm, spaceAfter=2 * mm)

    h2_style = _sp("h2",
        fontName="Helvetica-Bold", fontSize=13,
        textColor=Color(*_NAVY),
        leading=18, spaceBefore=8 * mm, spaceAfter=2 * mm,
        leftIndent=10)

    h3_style = _sp("h3",
        fontName="Helvetica-Bold", fontSize=11,
        textColor=Color(*_SLATE),
        leading=16, spaceBefore=5 * mm, spaceAfter=1.5 * mm,
        leftIndent=10)

    para_style = _sp("para",
        fontName="Helvetica", fontSize=10,
        textColor=Color(*_SLATE),
        leading=16, spaceAfter=4 * mm,
        wordWrap="LTR", allowWidows=0, allowOrphans=0)

    bq_style = _sp("bq",
        fontName="Helvetica-Oblique", fontSize=10,
        textColor=Color(*_NAVY),
        leading=16, leftIndent=14, rightIndent=8,
        spaceAfter=5 * mm, spaceBefore=2 * mm,
        backColor=Color(*_BLUE_SOFT),
        borderPadding=(7, 10, 7, 10))

    bullet_style = _sp("bullet",
        fontName="Helvetica", fontSize=10,
        textColor=Color(*_SLATE),
        leading=15, leftIndent=16, firstLineIndent=0,
        spaceAfter=1.5 * mm)

    code_pre_style = _sp("code_pre",
        fontName="Courier", fontSize=8,
        textColor=Color(*_CODE_FG),
        leading=12, backColor=Color(*_DARK_BG),
        leftIndent=12, rightIndent=4,
        spaceBefore=2 * mm, spaceAfter=5 * mm,
        borderPadding=(8, 10, 8, 12))

    # Accent bar flowable for headings
    class AccentBar(Flowable):
        def __init__(self, color, height_mm=0.7, width_frac=1.0, space_after_mm=2):
            Flowable.__init__(self)
            self.color = color
            self.height_mm = height_mm
            self.width_frac = width_frac
            self.space_after_mm = space_after_mm

        def draw(self):
            from reportlab.lib.units import mm as _mm
            self.canv.setFillColor(self.color)
            self.canv.rect(0, 0, self.width * self.width_frac, self.height_mm * _mm,
                           fill=1, stroke=0)

        def wrap(self, aw, ah):
            self.width = aw
            return (aw, self.height_mm * mm + self.space_after_mm * mm)

    for block in blocks:
        btype = block["type"]

        if btype == "h1":
            story.append(Paragraph(_inline_style(block["text"]), h1_style))
            story.append(AccentBar(Color(*_BLUE), height_mm=1.8, space_after_mm=3))

        elif btype == "h2":
            # Small accent bar on left via left border trick
            class H2Bar(Flowable):
                def __init__(self, text_para, bar_color, bar_w=3.5):
                    Flowable.__init__(self)
                    self.para = text_para
                    self.bar_color = bar_color
                    self.bar_w = bar_w

                def draw(self):
                    from reportlab.lib.units import mm as _mm
                    self.canv.setFillColor(self.bar_color)
                    self.canv.rect(0, 1, self.bar_w * _mm, self._h - 2, fill=1, stroke=0)
                    self.para.drawOn(self.canv, (self.bar_w + 2) * _mm, 0)

                def wrap(self, aw, ah):
                    from reportlab.lib.units import mm as _mm
                    pw, ph = self.para.wrap(aw - (self.bar_w + 2) * _mm, ah)
                    self._h = ph
                    return (aw, ph)

            h2_para = Paragraph(_inline_style(block["text"]), h2_style)
            story.append(Spacer(1, 7 * mm))
            story.append(H2Bar(h2_para, Color(*_BLUE_MID)))
            story.append(Spacer(1, 2 * mm))

        elif btype == "h3":
            story.append(Paragraph(_inline_style(block["text"]), h3_style))

        elif btype == "paragraph":
            story.append(Paragraph(_inline_style(block["text"]), para_style))

        elif btype == "blockquote":
            story.append(Paragraph(_inline_style(block["text"]), bq_style))

        elif btype == "list":
            items_flowables = []
            for item in block["items"]:
                items_flowables.append(
                    Paragraph("•  " + _inline_style(item), bullet_style)
                )
            items_flowables.append(Spacer(1, 2 * mm))
            story.append(KeepTogether(items_flowables))

        elif btype == "hr":
            story.append(HRFlowable(
                width="100%", thickness=0.5,
                color=Color(*_BORDER),
                spaceBefore=4 * mm, spaceAfter=4 * mm))

        elif btype == "code":
            raw = block["text"]
            # Sanitise: escape XML, truncate long lines
            safe_lines = []
            for ln in raw.split("\n"):
                ln = ln.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                if len(ln) > 115:
                    ln = ln[:112] + "…"
                safe_lines.append(ln)
            story.append(Preformatted("\n".join(safe_lines), code_pre_style))

        elif btype == "table":
            rows = block["rows"]
            if not rows:
                continue

            max_cols = max(len(r) for r in rows)
            # Pad rows to equal width
            rows = [r + [""] * (max_cols - len(r)) for r in rows]

            # Convert cells to Paragraphs for word-wrap support
            header_style = _sp("th",
                fontName="Helvetica-Bold", fontSize=8.5,
                textColor=Color(*_WHITE), leading=12,
                wordWrap="LTR")
            cell_style = _sp("td",
                fontName="Helvetica", fontSize=8.5,
                textColor=Color(*_SLATE), leading=12,
                wordWrap="LTR")
            cell_alt_style = _sp("td_alt",
                fontName="Helvetica", fontSize=8.5,
                textColor=Color(*_SLATE), leading=12,
                wordWrap="LTR", backColor=Color(*_LIGHT_BG))

            para_rows = []
            for ri, row in enumerate(rows):
                style = header_style if ri == 0 else (cell_style if ri % 2 == 1 else cell_alt_style)
                para_rows.append([Paragraph(_inline_style(cell), style) for cell in row])

            usable_w = 170 * mm
            # Distribute column widths: try to give more width to wider columns by content
            col_max_chars = [0] * max_cols
            for row in rows:
                for ci, cell in enumerate(row):
                    col_max_chars[ci] = max(col_max_chars[ci], len(cell))
            total_chars = sum(col_max_chars) or 1
            col_widths = [max(18 * mm, usable_w * (c / total_chars)) for c in col_max_chars]
            # Re-scale to exactly usable_w
            scale = usable_w / sum(col_widths)
            col_widths = [cw * scale for cw in col_widths]

            tbl_style = [
                # Header row
                ("BACKGROUND",    (0, 0),  (-1, 0),   Color(*_NAVY)),
                ("TEXTCOLOR",     (0, 0),  (-1, 0),   Color(*_WHITE)),
                ("FONTNAME",      (0, 0),  (-1, 0),   "Helvetica-Bold"),
                ("FONTSIZE",      (0, 0),  (-1, 0),   8.5),
                # Data rows
                ("FONTNAME",      (0, 1),  (-1, -1),  "Helvetica"),
                ("FONTSIZE",      (0, 1),  (-1, -1),  8.5),
                ("TEXTCOLOR",     (0, 1),  (-1, -1),  Color(*_SLATE)),
                ("ROWBACKGROUNDS",(0, 1),  (-1, -1),  [Color(*_WHITE), Color(*_LIGHT_BG)]),
                # Padding
                ("LEFTPADDING",   (0, 0),  (-1, -1),  8),
                ("RIGHTPADDING",  (0, 0),  (-1, -1),  8),
                ("TOPPADDING",    (0, 0),  (-1, -1),  6),
                ("BOTTOMPADDING", (0, 0),  (-1, -1),  6),
                # Borders
                ("LINEBELOW",     (0, 0),  (-1, 0),   1.0, Color(*_BLUE_MID)),
                ("LINEBELOW",     (0, 1),  (-1, -2),  0.4, Color(*_BORDER)),
                ("LINEBELOW",     (0, -1), (-1, -1),  1.0, Color(*_NAVY)),
                ("BOX",           (0, 0),  (-1, -1),  0.5, Color(*_BORDER)),
                ("VALIGN",        (0, 0),  (-1, -1),  "TOP"),
            ]
            tbl = Table(para_rows, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
            tbl.setStyle(TableStyle(tbl_style))
            story.append(tbl)
            story.append(Spacer(1, 5 * mm))


# ── Public API ────────────────────────────────────────────────────────────────

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

    if not output_filename:
        report_id = str(uuid.uuid4())
        output_filename = f"rapport_{report_id}.pdf"

    output_path = _DATA_DIR / output_filename
    date_str = datetime.now().strftime("%d/%m/%Y à %H:%M")
    session_short = session_id[:8] if session_id else "N/A"

    page_template = _PageTemplate(title=title, agent_name=agent_name)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=22 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        onPage=page_template,
    )

    story = []
    _build_cover(story, title, subtitle, date_str, agent_name, session_short)

    story.append(Spacer(1, 4 * mm))
    blocks = _parse_markdown(markdown_content)
    _build_content(story, blocks)

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

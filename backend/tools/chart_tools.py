"""
Outils de génération de graphiques et présentations.

Outils disponibles :
  - create_chart         : matplotlib — bar, line, pie, scatter, heatmap
  - create_presentation  : python-pptx — nouvelle présentation
  - add_text_slide       : diapo titre + contenu texte
  - add_chart_slide      : diapo avec image d'un graphique déjà généré
  - add_table_slide      : diapo avec tableau de données
  - save_presentation    : finalise et retourne le chemin
  - list_generated_files : liste les fichiers créés dans la session

Prérequis :
  pip install matplotlib pandas python-pptx pillow
"""
import io
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CHARTS_DIR = Path("data/charts")
_PRESENTATIONS_DIR = Path("data/presentations")

# Per-session state: {session_id: {"prs": pptx.Presentation, "files": [...]}}
_session_state: Dict[str, Dict[str, Any]] = {}


def _get_session(session_id: str) -> Dict[str, Any]:
    if session_id not in _session_state:
        _session_state[session_id] = {"prs": None, "files": []}
    return _session_state[session_id]


def _parse_data_input(data_str: str) -> Optional[Any]:
    """Try to parse data_str as JSON, then CSV, then tabular text."""
    try:
        import pandas as pd
    except ImportError:
        return None

    data_str = data_str.strip()

    # Try JSON
    try:
        parsed = json.loads(data_str)
        if isinstance(parsed, list) and parsed:
            return pd.DataFrame(parsed)
        if isinstance(parsed, dict):
            return pd.DataFrame(parsed)
    except Exception:
        pass

    # Try CSV
    try:
        df = pd.read_csv(io.StringIO(data_str))
        if len(df.columns) >= 2:
            return df
    except Exception:
        pass

    # Try markdown table
    try:
        lines = [l.strip() for l in data_str.splitlines() if l.strip() and "|" in l]
        if len(lines) >= 2:
            rows = []
            for line in lines:
                cells = [c.strip() for c in line.strip("|").split("|")]
                rows.append(cells)
            # Filter out separator rows (----)
            data_rows = [r for r in rows if not all(re.match(r"^[\-:]+$", c) for c in r if c)]
            if len(data_rows) >= 2:
                headers = data_rows[0]
                df = pd.DataFrame(data_rows[1:], columns=headers)
                return df
    except Exception:
        pass

    return None


# ── Matplotlib chart colors palette ──────────────────────────────────────────
_PALETTE = [
    "#6366f1", "#22c55e", "#f59e0b", "#ef4444", "#3b82f6",
    "#ec4899", "#14b8a6", "#f97316", "#8b5cf6", "#84cc16",
]


def _apply_dark_style(fig, ax_or_axes):
    """Apply a clean dark theme to a matplotlib figure."""
    import matplotlib.pyplot as plt

    fig.patch.set_facecolor("#1e2130")
    axes = ax_or_axes if isinstance(ax_or_axes, (list, tuple)) else [ax_or_axes]
    for ax in axes:
        ax.set_facecolor("#252840")
        ax.tick_params(colors="#94a3b8", labelsize=9)
        ax.xaxis.label.set_color("#94a3b8")
        ax.yaxis.label.set_color("#94a3b8")
        ax.title.set_color("#e2e8f0")
        for spine in ax.spines.values():
            spine.set_edgecolor("#2d3148")
    return fig


def make_chart_tools(session_id: str) -> list:
    """Create and return a list of LangChain tools for chart + presentation generation."""
    from langchain_core.tools import tool

    _CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    _PRESENTATIONS_DIR.mkdir(parents=True, exist_ok=True)

    def _save_fig(fig, label: str) -> str:
        """Save a matplotlib figure to the charts directory and return the filename."""
        import matplotlib.pyplot as plt

        safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", label.lower())[:30] or "chart"
        filename = f"{safe}_{session_id[:8]}_{uuid.uuid4().hex[:6]}.png"
        path = _CHARTS_DIR / filename
        fig.savefig(str(path), bbox_inches="tight", dpi=150, facecolor=fig.get_facecolor())
        plt.close(fig)

        state = _get_session(session_id)
        state["files"].append({"type": "chart", "filename": filename, "path": str(path)})
        return filename

    @tool
    def create_bar_chart(
        labels: str,
        values: str,
        title: str = "Bar Chart",
        x_label: str = "",
        y_label: str = "",
        horizontal: bool = False,
        stacked_groups: str = "",
    ) -> str:
        """Create a bar chart.
        Args:
            labels: Comma-separated category labels (e.g., "Jan,Feb,Mar") or JSON array
            values: Comma-separated numeric values (e.g., "10,25,18") or JSON object/array for grouped bars
            title: Chart title
            x_label: X-axis label
            y_label: Y-axis label
            horizontal: If True, create a horizontal bar chart
            stacked_groups: JSON dict of {group_name: [values]} for grouped/stacked bars
        """
        try:
            import matplotlib.pyplot as plt
            import numpy as np

            # Parse labels
            try:
                label_list = json.loads(labels)
            except Exception:
                label_list = [l.strip() for l in labels.split(",") if l.strip()]

            # Parse grouped or simple values
            groups = {}
            if stacked_groups:
                try:
                    groups = json.loads(stacked_groups)
                except Exception:
                    pass

            if groups:
                x = np.arange(len(label_list))
                width = 0.8 / len(groups)
                fig, ax = plt.subplots(figsize=(max(10, len(label_list) * 0.8), 6))
                for i, (grp_name, grp_vals) in enumerate(groups.items()):
                    ax.bar(x + i * width, grp_vals[:len(label_list)], width,
                           label=grp_name, color=_PALETTE[i % len(_PALETTE)], alpha=0.85)
                ax.set_xticks(x + width * (len(groups) - 1) / 2)
                ax.set_xticklabels(label_list, rotation=30 if len(label_list) > 6 else 0, ha="right")
                ax.legend(facecolor="#252840", edgecolor="#2d3148", labelcolor="#e2e8f0")
            else:
                try:
                    val_list = json.loads(values)
                    if isinstance(val_list, dict):
                        label_list = list(val_list.keys())
                        val_list = list(val_list.values())
                except Exception:
                    val_list = [float(v.strip()) for v in values.split(",") if v.strip()]

                fig, ax = plt.subplots(figsize=(max(8, len(label_list) * 0.7), 6))
                colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(label_list))]
                if horizontal:
                    ax.barh(label_list, val_list, color=colors, alpha=0.85)
                else:
                    ax.bar(label_list, val_list, color=colors, alpha=0.85)
                    if len(label_list) > 5:
                        ax.set_xticklabels(label_list, rotation=30, ha="right")
                    # Value labels on bars
                    for i, v in enumerate(val_list):
                        ax.text(i if not horizontal else v, v if not horizontal else i,
                                f"{v:,.1f}" if isinstance(v, float) else str(v),
                                ha="center" if not horizontal else "left",
                                va="bottom" if not horizontal else "center",
                                color="#e2e8f0", fontsize=8, fontweight="bold")

            _apply_dark_style(fig, ax)
            ax.set_title(title, fontsize=14, fontweight="bold", pad=15)
            if x_label:
                ax.set_xlabel(x_label)
            if y_label:
                ax.set_ylabel(y_label)
            ax.grid(axis="y" if not horizontal else "x", alpha=0.3, color="#2d3148")

            filename = _save_fig(fig, title)
            return f"CHART_CREATED:{filename}\n✅ Bar chart '{title}' créé : {filename}"
        except Exception as e:
            logger.error("create_bar_chart error: %s", e, exc_info=True)
            return f"❌ Erreur création bar chart : {e}"

    @tool
    def create_line_chart(
        x_values: str,
        y_series: str,
        title: str = "Line Chart",
        x_label: str = "",
        y_label: str = "",
        smooth: bool = False,
    ) -> str:
        """Create a line chart (supports multiple series).
        Args:
            x_values: Comma-separated X-axis values or labels
            y_series: JSON dict of {series_name: [values]} or comma-separated values for single line
            title: Chart title
            x_label: X-axis label
            y_label: Y-axis label
            smooth: If True, apply Bezier smoothing to the lines
        """
        try:
            import matplotlib.pyplot as plt
            import numpy as np

            try:
                x_list = json.loads(x_values)
            except Exception:
                x_list = [v.strip() for v in x_values.split(",") if v.strip()]

            # Parse y_series
            try:
                series_dict = json.loads(y_series)
                if isinstance(series_dict, list):
                    series_dict = {"Série": series_dict}
            except Exception:
                vals = [float(v.strip()) for v in y_series.split(",") if v.strip()]
                series_dict = {"Valeurs": vals}

            fig, ax = plt.subplots(figsize=(max(10, len(x_list) * 0.5), 6))
            x_idx = range(len(x_list))

            for i, (name, vals) in enumerate(series_dict.items()):
                color = _PALETTE[i % len(_PALETTE)]
                y_vals = [float(v) if v is not None else 0 for v in vals[:len(x_list)]]

                if smooth and len(y_vals) > 3:
                    from scipy.interpolate import make_interp_spline
                    x_smooth = np.linspace(0, len(y_vals) - 1, 300)
                    spl = make_interp_spline(range(len(y_vals)), y_vals, k=3)
                    y_smooth = spl(x_smooth)
                    ax.plot(x_smooth, y_smooth, color=color, linewidth=2, label=name)
                    ax.scatter(range(len(y_vals)), y_vals, color=color, s=40, zorder=5)
                else:
                    ax.plot(range(len(y_vals)), y_vals, color=color, linewidth=2,
                            marker="o", markersize=5, label=name)
                ax.fill_between(range(len(y_vals)), y_vals, alpha=0.08, color=color)

            ax.set_xticks(range(len(x_list)))
            ax.set_xticklabels(x_list, rotation=30 if len(x_list) > 8 else 0, ha="right")

            if len(series_dict) > 1:
                ax.legend(facecolor="#252840", edgecolor="#2d3148", labelcolor="#e2e8f0")

            _apply_dark_style(fig, ax)
            ax.set_title(title, fontsize=14, fontweight="bold", pad=15)
            if x_label:
                ax.set_xlabel(x_label)
            if y_label:
                ax.set_ylabel(y_label)
            ax.grid(axis="y", alpha=0.3, color="#2d3148")

            filename = _save_fig(fig, title)
            return f"CHART_CREATED:{filename}\n✅ Line chart '{title}' créé : {filename}"
        except Exception as e:
            logger.error("create_line_chart error: %s", e, exc_info=True)
            return f"❌ Erreur création line chart : {e}"

    @tool
    def create_pie_chart(
        labels: str,
        values: str,
        title: str = "Pie Chart",
        donut: bool = False,
        show_percentages: bool = True,
    ) -> str:
        """Create a pie or donut chart.
        Args:
            labels: Comma-separated labels
            values: Comma-separated numeric values
            title: Chart title
            donut: If True, create a donut chart
            show_percentages: If True, show percentage labels on slices
        """
        try:
            import matplotlib.pyplot as plt

            try:
                label_list = json.loads(labels)
            except Exception:
                label_list = [l.strip() for l in labels.split(",") if l.strip()]
            try:
                val_list = json.loads(values)
            except Exception:
                val_list = [float(v.strip()) for v in values.split(",") if v.strip()]

            colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(label_list))]
            fig, ax = plt.subplots(figsize=(9, 7))

            wedges, texts, autotexts = ax.pie(
                val_list,
                labels=label_list,
                colors=colors,
                autopct="%1.1f%%" if show_percentages else None,
                startangle=90,
                pctdistance=0.8,
                wedgeprops={"edgecolor": "#1e2130", "linewidth": 1.5},
            )
            for t in texts:
                t.set_color("#e2e8f0")
                t.set_fontsize(10)
            if autotexts:
                for at in autotexts:
                    at.set_color("white")
                    at.set_fontsize(9)
                    at.set_fontweight("bold")

            if donut:
                centre = plt.Circle((0, 0), 0.55, color="#1e2130")
                ax.add_patch(centre)
                total = sum(val_list)
                ax.text(0, 0, f"Total\n{total:,.0f}", ha="center", va="center",
                        color="#e2e8f0", fontsize=12, fontweight="bold")

            _apply_dark_style(fig, ax)
            ax.set_title(title, fontsize=14, fontweight="bold", pad=20)

            filename = _save_fig(fig, title)
            chart_type = "Donut" if donut else "Pie"
            return f"CHART_CREATED:{filename}\n✅ {chart_type} chart '{title}' créé : {filename}"
        except Exception as e:
            return f"❌ Erreur création pie chart : {e}"

    @tool
    def create_scatter_plot(
        x_values: str,
        y_values: str,
        labels: str = "",
        title: str = "Scatter Plot",
        x_label: str = "",
        y_label: str = "",
        size_values: str = "",
        color_values: str = "",
    ) -> str:
        """Create a scatter plot.
        Args:
            x_values: Comma-separated X values or JSON array
            y_values: Comma-separated Y values or JSON array
            labels: Optional comma-separated point labels
            title: Chart title
            x_label: X-axis label
            y_label: Y-axis label
            size_values: Optional comma-separated values to control dot size
            color_values: Optional comma-separated numeric values for color gradient
        """
        try:
            import matplotlib.pyplot as plt
            import numpy as np

            def parse_nums(s):
                try:
                    return json.loads(s)
                except Exception:
                    return [float(v.strip()) for v in s.split(",") if v.strip()]

            x_list = parse_nums(x_values)
            y_list = parse_nums(y_values)
            sizes = parse_nums(size_values) if size_values else [60] * len(x_list)
            colors = parse_nums(color_values) if color_values else [_PALETTE[0]] * len(x_list)

            fig, ax = plt.subplots(figsize=(9, 7))
            scatter = ax.scatter(x_list, y_list, s=sizes, c=colors,
                                 cmap="viridis" if color_values else None,
                                 alpha=0.8, edgecolors="#2d3148", linewidths=0.5)

            if color_values:
                cbar = plt.colorbar(scatter, ax=ax)
                cbar.ax.tick_params(colors="#94a3b8")

            if labels:
                try:
                    lbl_list = json.loads(labels)
                except Exception:
                    lbl_list = [l.strip() for l in labels.split(",")]
                for i, (x, y, lbl) in enumerate(zip(x_list, y_list, lbl_list)):
                    ax.annotate(lbl, (x, y), textcoords="offset points",
                                xytext=(5, 5), fontsize=8, color="#94a3b8")

            # Trend line
            if len(x_list) >= 3:
                z = np.polyfit(x_list, y_list, 1)
                p = np.poly1d(z)
                x_trend = np.linspace(min(x_list), max(x_list), 100)
                ax.plot(x_trend, p(x_trend), "--", color="#ef4444", alpha=0.6, linewidth=1.5, label="Tendance")
                ax.legend(facecolor="#252840", edgecolor="#2d3148", labelcolor="#e2e8f0")

            _apply_dark_style(fig, ax)
            ax.set_title(title, fontsize=14, fontweight="bold", pad=15)
            if x_label:
                ax.set_xlabel(x_label)
            if y_label:
                ax.set_ylabel(y_label)
            ax.grid(alpha=0.2, color="#2d3148")

            filename = _save_fig(fig, title)
            return f"CHART_CREATED:{filename}\n✅ Scatter plot '{title}' créé : {filename}"
        except Exception as e:
            return f"❌ Erreur création scatter plot : {e}"

    @tool
    def create_heatmap(
        data: str,
        title: str = "Heatmap",
        x_label: str = "",
        y_label: str = "",
        colormap: str = "RdYlGn",
    ) -> str:
        """Create a heatmap from a 2D data matrix.
        Args:
            data: JSON 2D array [[row1_vals], [row2_vals]] or CSV string
            title: Chart title
            x_label: X-axis label (column label)
            y_label: Y-axis label (row label)
            colormap: Matplotlib colormap ('RdYlGn', 'Blues', 'RdBu', 'viridis', etc.)
        """
        try:
            import matplotlib.pyplot as plt
            import numpy as np

            parsed = _parse_data_input(data)
            if parsed is not None:
                import pandas as pd
                if isinstance(parsed, pd.DataFrame):
                    row_labels = parsed.index.tolist()
                    col_labels = parsed.columns.tolist()
                    matrix = parsed.values.astype(float)
                else:
                    matrix = np.array(parsed, dtype=float)
                    row_labels = [f"R{i+1}" for i in range(len(matrix))]
                    col_labels = [f"C{j+1}" for j in range(len(matrix[0]))]
            else:
                matrix = np.array(json.loads(data), dtype=float)
                row_labels = [f"R{i+1}" for i in range(len(matrix))]
                col_labels = [f"C{j+1}" for j in range(len(matrix[0]))]

            fig, ax = plt.subplots(figsize=(max(8, len(col_labels) * 0.8), max(6, len(row_labels) * 0.6)))
            im = ax.imshow(matrix, cmap=colormap, aspect="auto")
            cbar = plt.colorbar(im, ax=ax)
            cbar.ax.tick_params(colors="#94a3b8")

            ax.set_xticks(range(len(col_labels)))
            ax.set_yticks(range(len(row_labels)))
            ax.set_xticklabels(col_labels, rotation=30, ha="right", color="#94a3b8")
            ax.set_yticklabels(row_labels, color="#94a3b8")

            # Annotate cells
            for i in range(len(row_labels)):
                for j in range(len(col_labels)):
                    val = matrix[i, j]
                    txt = f"{val:.1f}" if abs(val) < 10000 else f"{val:,.0f}"
                    ax.text(j, i, txt, ha="center", va="center",
                            color="black" if 0.3 < (val - matrix.min()) / (matrix.max() - matrix.min() + 1e-8) < 0.7 else "white",
                            fontsize=8)

            _apply_dark_style(fig, ax)
            ax.set_title(title, fontsize=14, fontweight="bold", pad=15)
            if x_label:
                ax.set_xlabel(x_label, color="#94a3b8")
            if y_label:
                ax.set_ylabel(y_label, color="#94a3b8")

            filename = _save_fig(fig, title)
            return f"CHART_CREATED:{filename}\n✅ Heatmap '{title}' créée : {filename}"
        except Exception as e:
            return f"❌ Erreur création heatmap : {e}"

    @tool
    def create_multi_chart(
        data: str,
        chart_configs: str,
        title: str = "Tableau de bord",
        layout: str = "2x2",
    ) -> str:
        """Create a multi-panel chart dashboard from data and chart configurations.
        Args:
            data: JSON or CSV data with columns
            chart_configs: JSON array of chart config dicts: [{type, x_col, y_col, title, kind}]
                           kind: 'bar', 'line', 'pie', 'scatter'
            title: Overall dashboard title
            layout: Grid layout e.g. '2x2', '2x3', '1x4'
        """
        try:
            import matplotlib.pyplot as plt
            import matplotlib.gridspec as gridspec

            df = _parse_data_input(data)
            if df is None:
                return "❌ Impossible de parser les données. Utilisez JSON ou CSV."

            configs = json.loads(chart_configs)
            rows, cols = [int(x) for x in layout.split("x")]
            n = len(configs)

            fig = plt.figure(figsize=(cols * 7, rows * 5))
            fig.suptitle(title, fontsize=16, fontweight="bold", color="#e2e8f0", y=1.02)

            for i, cfg in enumerate(configs[:rows * cols]):
                ax = fig.add_subplot(rows, cols, i + 1)
                kind = cfg.get("kind", "bar")
                x_col = cfg.get("x_col", df.columns[0])
                y_col = cfg.get("y_col", df.columns[1] if len(df.columns) > 1 else df.columns[0])
                sub_title = cfg.get("title", f"Chart {i+1}")

                try:
                    if kind == "bar":
                        x = df[x_col].tolist()
                        y = [float(v) for v in df[y_col].tolist()]
                        colors = [_PALETTE[j % len(_PALETTE)] for j in range(len(x))]
                        ax.bar(x[:20], y[:20], color=colors, alpha=0.85)
                        if len(x) > 5:
                            ax.set_xticklabels(x[:20], rotation=25, ha="right", fontsize=8)
                    elif kind == "line":
                        y = [float(v) for v in df[y_col].tolist()]
                        ax.plot(df[x_col].tolist()[:50], y[:50], color=_PALETTE[i % len(_PALETTE)],
                                linewidth=2, marker="o", markersize=3)
                        ax.fill_between(range(len(y[:50])), y[:50], alpha=0.1, color=_PALETTE[i % len(_PALETTE)])
                        ax.set_xticks(range(0, min(len(df), 50), max(1, len(df) // 10)))
                    elif kind == "pie":
                        y = [float(v) for v in df[y_col].tolist()[:10]]
                        x = df[x_col].tolist()[:10]
                        colors = [_PALETTE[j % len(_PALETTE)] for j in range(len(x))]
                        ax.pie(y, labels=x, colors=colors, autopct="%1.0f%%",
                               textprops={"color": "#e2e8f0", "fontsize": 8})
                    elif kind == "scatter":
                        y_col2 = cfg.get("y2_col", y_col)
                        x_vals = [float(v) for v in df[x_col].tolist() if str(v).replace(".", "").replace("-", "").isdigit()]
                        y_vals = [float(v) for v in df[y_col].tolist()[:len(x_vals)]]
                        ax.scatter(x_vals, y_vals, color=_PALETTE[i % len(_PALETTE)], alpha=0.7, s=30)
                except Exception as e:
                    ax.text(0.5, 0.5, f"Erreur: {e}", transform=ax.transAxes,
                            ha="center", color="#ef4444", fontsize=8)

                ax.set_title(sub_title, fontsize=11, fontweight="bold")
                ax.grid(alpha=0.2, color="#2d3148")
                _apply_dark_style(fig, ax)

            fig.tight_layout(rect=[0, 0, 1, 0.98])

            filename = _save_fig(fig, title)
            return f"CHART_CREATED:{filename}\n✅ Dashboard '{title}' créé ({n} graphiques) : {filename}"
        except Exception as e:
            logger.error("create_multi_chart error: %s", e, exc_info=True)
            return f"❌ Erreur création dashboard : {e}"

    @tool
    def create_presentation(
        title: str = "Présentation",
        subtitle: str = "",
        theme: str = "dark",
    ) -> str:
        """Initialize a new PowerPoint presentation.
        Args:
            title: Presentation title (shown on cover slide)
            subtitle: Subtitle for the cover slide
            theme: 'dark' (dark professional) or 'light' (clean white)
        """
        try:
            from pptx import Presentation
            from pptx.util import Inches, Pt
            from pptx.dml.color import RGBColor

            prs = Presentation()
            prs.slide_width = Inches(13.33)
            prs.slide_height = Inches(7.5)

            state = _get_session(session_id)
            state["prs"] = prs
            state["prs_theme"] = theme
            state["prs_slide_count"] = 0
            state["prs_title"] = title

            # Cover slide
            slide_layout = prs.slide_layouts[6]  # blank
            slide = prs.slides.add_slide(slide_layout)

            # Background
            from pptx.util import Emu
            from pptx.enum.dml import MSO_THEME_COLOR

            bg = slide.shapes.add_shape(
                1, 0, 0, prs.slide_width, prs.slide_height
            )
            if theme == "dark":
                bg.fill.solid()
                bg.fill.fore_color.rgb = RGBColor(0x0F, 0x11, 0x17)
                bg.line.fill.background()
            else:
                bg.fill.solid()
                bg.fill.fore_color.rgb = RGBColor(0xF8, 0xFA, 0xFF)
                bg.line.fill.background()

            # Accent bar
            bar = slide.shapes.add_shape(
                1, 0, Inches(3.0), Inches(0.5), Inches(0.06)
            )
            bar.fill.solid()
            bar.fill.fore_color.rgb = RGBColor(0x63, 0x66, 0xF1)
            bar.line.fill.background()

            # Title text
            from pptx.util import Pt
            title_box = slide.shapes.add_textbox(Inches(0.8), Inches(2.3), Inches(11.5), Inches(0.8))
            tf = title_box.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            run = p.add_run()
            run.text = title
            run.font.size = Pt(44)
            run.font.bold = True
            run.font.color.rgb = RGBColor(0xE2, 0xE8, 0xF0) if theme == "dark" else RGBColor(0x1E, 0x21, 0x30)

            if subtitle:
                sub_box = slide.shapes.add_textbox(Inches(0.8), Inches(3.2), Inches(11.5), Inches(0.5))
                stf = sub_box.text_frame
                sp = stf.paragraphs[0]
                srun = sp.add_run()
                srun.text = subtitle
                srun.font.size = Pt(20)
                srun.font.color.rgb = RGBColor(0x94, 0xA3, 0xB8)

            state["prs_slide_count"] = 1
            return f"✅ Présentation '{title}' initialisée ({theme} theme). Ajoutez des diapositives avec add_text_slide ou add_chart_slide."
        except ImportError:
            return "❌ python-pptx non installé. Exécutez : pip install python-pptx"
        except Exception as e:
            return f"❌ Erreur création présentation : {e}"

    @tool
    def add_text_slide(
        slide_title: str,
        content: str,
        layout: str = "title_content",
        bullet_points: str = "",
    ) -> str:
        """Add a text slide to the current presentation.
        Args:
            slide_title: Slide title
            content: Main text content (supports markdown-like **bold**, - bullets)
            layout: 'title_content', 'two_column', 'title_only'
            bullet_points: JSON array of bullet point strings (overrides content if provided)
        """
        try:
            from pptx.util import Inches, Pt
            from pptx.dml.color import RGBColor

            state = _get_session(session_id)
            prs = state.get("prs")
            if not prs:
                return "❌ Aucune présentation active. Créez-en une avec create_presentation()."

            theme = state.get("prs_theme", "dark")
            is_dark = theme == "dark"
            bg_color = RGBColor(0x1E, 0x21, 0x30) if is_dark else RGBColor(0xFF, 0xFF, 0xFF)
            title_color = RGBColor(0xE2, 0xE8, 0xF0) if is_dark else RGBColor(0x1E, 0x21, 0x30)
            text_color = RGBColor(0x94, 0xA3, 0xB8) if is_dark else RGBColor(0x47, 0x55, 0x69)
            accent_color = RGBColor(0x63, 0x66, 0xF1)

            slide = prs.slides.add_slide(prs.slide_layouts[6])

            # Background
            bg = slide.shapes.add_shape(1, 0, 0, prs.slide_width, prs.slide_height)
            bg.fill.solid()
            bg.fill.fore_color.rgb = bg_color
            bg.line.fill.background()

            # Accent top bar
            bar = slide.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(0.06))
            bar.fill.solid()
            bar.fill.fore_color.rgb = accent_color
            bar.line.fill.background()

            # Title
            title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12.3), Inches(0.7))
            tf = title_box.text_frame
            p = tf.paragraphs[0]
            run = p.add_run()
            run.text = slide_title
            run.font.size = Pt(28)
            run.font.bold = True
            run.font.color.rgb = title_color

            # Content
            bullets = []
            if bullet_points:
                try:
                    bullets = json.loads(bullet_points)
                except Exception:
                    bullets = [b.strip() for b in bullet_points.split("\n") if b.strip()]
            elif content:
                bullets = [l.lstrip("-•* ").strip() for l in content.splitlines() if l.strip()]

            if bullets:
                body_box = slide.shapes.add_textbox(Inches(0.5), Inches(1.3), Inches(12.3), Inches(5.8))
                btf = body_box.text_frame
                btf.word_wrap = True
                for i, bullet in enumerate(bullets[:15]):
                    para = btf.add_paragraph() if i > 0 else btf.paragraphs[0]
                    is_bold = "**" in bullet
                    clean = bullet.replace("**", "")
                    run = para.add_run()
                    run.text = ("• " if not is_bold else "") + clean
                    run.font.size = Pt(16 if is_bold else 14)
                    run.font.bold = is_bold
                    run.font.color.rgb = title_color if is_bold else text_color
                    para.space_before = Pt(6 if is_bold else 2)

            state["prs_slide_count"] = state.get("prs_slide_count", 0) + 1
            n = state["prs_slide_count"]
            return f"✅ Diapositive texte '{slide_title}' ajoutée (diapo #{n})."
        except Exception as e:
            return f"❌ Erreur ajout diapositive texte : {e}"

    @tool
    def add_chart_slide(
        slide_title: str,
        chart_filename: str,
        caption: str = "",
        notes: str = "",
    ) -> str:
        """Add a chart image to a new slide in the current presentation.
        Args:
            slide_title: Slide title
            chart_filename: Filename of a chart created with create_bar_chart/etc.
            caption: Optional caption text below the chart
            notes: Optional speaker notes
        """
        try:
            from pptx.util import Inches, Pt
            from pptx.dml.color import RGBColor

            state = _get_session(session_id)
            prs = state.get("prs")
            if not prs:
                return "❌ Aucune présentation active."

            chart_path = _CHARTS_DIR / chart_filename
            if not chart_path.exists():
                return f"❌ Fichier graphique introuvable : {chart_filename}"

            theme = state.get("prs_theme", "dark")
            is_dark = theme == "dark"
            bg_color = RGBColor(0x1E, 0x21, 0x30) if is_dark else RGBColor(0xFF, 0xFF, 0xFF)
            title_color = RGBColor(0xE2, 0xE8, 0xF0) if is_dark else RGBColor(0x1E, 0x21, 0x30)

            slide = prs.slides.add_slide(prs.slide_layouts[6])

            bg = slide.shapes.add_shape(1, 0, 0, prs.slide_width, prs.slide_height)
            bg.fill.solid()
            bg.fill.fore_color.rgb = bg_color
            bg.line.fill.background()

            # Accent bar
            bar = slide.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(0.06))
            bar.fill.solid()
            bar.fill.fore_color.rgb = RGBColor(0x63, 0x66, 0xF1)
            bar.line.fill.background()

            # Title
            title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.15), Inches(12.3), Inches(0.6))
            tf = title_box.text_frame
            p = tf.paragraphs[0]
            run = p.add_run()
            run.text = slide_title
            run.font.size = Pt(24)
            run.font.bold = True
            run.font.color.rgb = title_color

            # Chart image
            img_top = Inches(0.9)
            img_height = Inches(5.6) if not caption else Inches(5.2)
            slide.shapes.add_picture(str(chart_path), Inches(0.3), img_top,
                                     width=Inches(12.7), height=img_height)

            # Caption
            if caption:
                cap_box = slide.shapes.add_textbox(Inches(0.5), Inches(6.3), Inches(12.3), Inches(0.5))
                ctf = cap_box.text_frame
                cp = ctf.paragraphs[0]
                crun = cp.add_run()
                crun.text = caption
                crun.font.size = Pt(11)
                crun.font.italic = True
                crun.font.color.rgb = RGBColor(0x64, 0x74, 0x8B)

            if notes:
                slide.notes_slide.notes_text_frame.text = notes

            state["prs_slide_count"] = state.get("prs_slide_count", 0) + 1
            n = state["prs_slide_count"]
            return f"✅ Diapositive graphique '{slide_title}' ajoutée (diapo #{n})."
        except Exception as e:
            return f"❌ Erreur ajout diapositive graphique : {e}"

    @tool
    def add_table_slide(
        slide_title: str,
        data: str,
        caption: str = "",
    ) -> str:
        """Add a data table to a new slide.
        Args:
            slide_title: Slide title
            data: JSON or CSV data to display as a table
            caption: Optional caption text below the table
        """
        try:
            from pptx.util import Inches, Pt
            from pptx.dml.color import RGBColor

            state = _get_session(session_id)
            prs = state.get("prs")
            if not prs:
                return "❌ Aucune présentation active."

            df = _parse_data_input(data)
            if df is None:
                return "❌ Impossible de parser les données."

            theme = state.get("prs_theme", "dark")
            is_dark = theme == "dark"
            bg_color = RGBColor(0x1E, 0x21, 0x30) if is_dark else RGBColor(0xFF, 0xFF, 0xFF)
            title_color = RGBColor(0xE2, 0xE8, 0xF0) if is_dark else RGBColor(0x1E, 0x21, 0x30)

            slide = prs.slides.add_slide(prs.slide_layouts[6])

            bg = slide.shapes.add_shape(1, 0, 0, prs.slide_width, prs.slide_height)
            bg.fill.solid()
            bg.fill.fore_color.rgb = bg_color
            bg.line.fill.background()

            bar = slide.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(0.06))
            bar.fill.solid()
            bar.fill.fore_color.rgb = RGBColor(0x63, 0x66, 0xF1)
            bar.line.fill.background()

            # Title
            title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.15), Inches(12.3), Inches(0.6))
            tf = title_box.text_frame
            p = tf.paragraphs[0]
            run = p.add_run()
            run.text = slide_title
            run.font.size = Pt(24)
            run.font.bold = True
            run.font.color.rgb = title_color

            # Table (cap at 20 rows)
            df_display = df.head(20)
            rows_count = len(df_display) + 1
            cols_count = len(df_display.columns)

            tbl = slide.shapes.add_table(rows_count, cols_count,
                                         Inches(0.3), Inches(0.9),
                                         Inches(12.7), Inches(5.8 if not caption else 5.4)).table

            # Header
            for j, col in enumerate(df_display.columns):
                cell = tbl.cell(0, j)
                cell.text = str(col)
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor(0x25, 0x28, 0x40) if is_dark else RGBColor(0x63, 0x66, 0xF1)
                cell.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor(0xE2, 0xE8, 0xF0)
                cell.text_frame.paragraphs[0].runs[0].font.size = Pt(10)
                cell.text_frame.paragraphs[0].runs[0].font.bold = True

            # Data rows
            for i, (_, row) in enumerate(df_display.iterrows(), 1):
                for j, val in enumerate(row):
                    cell = tbl.cell(i, j)
                    cell.text = str(val)
                    cell.fill.solid()
                    row_bg = RGBColor(0x1E, 0x21, 0x30) if (i % 2 == 0 or not is_dark) else RGBColor(0x1A, 0x1D, 0x27)
                    cell.fill.fore_color.rgb = row_bg
                    cell.text_frame.paragraphs[0].runs[0].font.color.rgb = (
                        RGBColor(0x94, 0xA3, 0xB8) if is_dark else RGBColor(0x47, 0x55, 0x69)
                    )
                    cell.text_frame.paragraphs[0].runs[0].font.size = Pt(9)

            state["prs_slide_count"] = state.get("prs_slide_count", 0) + 1
            n = state["prs_slide_count"]
            return f"✅ Diapositive tableau '{slide_title}' ajoutée ({len(df_display)} lignes × {cols_count} colonnes, diapo #{n})."
        except Exception as e:
            return f"❌ Erreur ajout diapositive tableau : {e}"

    @tool
    def save_presentation(filename: str = "presentation") -> str:
        """Save and finalize the current presentation as a .pptx file.
        Args:
            filename: Base filename (without .pptx extension)
        """
        try:
            state = _get_session(session_id)
            prs = state.get("prs")
            if not prs:
                return "❌ Aucune présentation active à sauvegarder."

            safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", filename)[:50] or "presentation"
            fname = f"{safe}_{session_id[:8]}_{uuid.uuid4().hex[:6]}.pptx"
            path = _PRESENTATIONS_DIR / fname
            prs.save(str(path))
            n_slides = state.get("prs_slide_count", 0)
            state["files"].append({"type": "presentation", "filename": fname, "path": str(path)})
            return (
                f"PRESENTATION_SAVED:{fname}\n"
                f"✅ Présentation sauvegardée : {fname}\n"
                f"📊 {n_slides} diapositive(s) | Taille : {path.stat().st_size // 1024}KB"
            )
        except Exception as e:
            return f"❌ Erreur sauvegarde présentation : {e}"

    @tool
    def list_generated_files() -> str:
        """List all charts and presentations generated in this session."""
        state = _get_session(session_id)
        files = state.get("files", [])
        if not files:
            return "Aucun fichier généré dans cette session."
        lines = [f"**{len(files)} fichier(s) générés :**"]
        for f in files:
            icon = "📊" if f["type"] == "chart" else "📑"
            lines.append(f"{icon} `{f['filename']}` ({f['type']})")
        return "\n".join(lines)

    @tool
    def parse_and_describe_data(data: str) -> str:
        """Parse input data (CSV/JSON/table) and return a statistical description.
        Args:
            data: Raw data as CSV, JSON array, or markdown table
        """
        try:
            df = _parse_data_input(data)
            if df is None:
                return "❌ Impossible de parser les données. Formats acceptés : JSON, CSV, tableau Markdown."

            lines = [
                f"**Données parsées** : {len(df)} lignes × {len(df.columns)} colonnes",
                f"**Colonnes** : {', '.join(df.columns.tolist())}",
                "",
                "**Aperçu (5 premières lignes)** :",
                df.head(5).to_markdown(index=False),
                "",
                "**Statistiques** :",
            ]
            try:
                desc = df.describe(include="all")
                lines.append(desc.to_markdown())
            except Exception:
                pass
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Erreur parsing données : {e}"

    return [
        create_bar_chart,
        create_line_chart,
        create_pie_chart,
        create_scatter_plot,
        create_heatmap,
        create_multi_chart,
        create_presentation,
        add_text_slide,
        add_chart_slide,
        add_table_slide,
        save_presentation,
        list_generated_files,
        parse_and_describe_data,
    ]

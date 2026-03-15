"""
Outils de gestion de fichiers pour l'agent File Manager.

Supporte : .txt, .md, .py, .sql, .json, .yaml, .yml, .csv, .xlsx, .docx, .parquet
et tout fichier texte générique.

Sécurité :
  - Toutes les opérations destructives (write, delete, move) utilisent un paramètre
    `confirmed=False`. Si confirmed=False, l'outil retourne une description de l'action
    et demande confirmation à l'utilisateur. L'agent ne doit exécuter l'opération
    qu'après avoir reçu la confirmation explicite de l'utilisateur, en appelant
    l'outil à nouveau avec confirmed=True.
  - base_path optionnel : restreint les opérations à un répertoire racine.

Usage pattern pour opérations destructives :
  1. Agent appelle write_file(path, content, confirmed=False) → retourne preview + demande de confirmation
  2. L'utilisateur répond "oui" / "confirme" / "go"
  3. Agent rappelle write_file(path, content, confirmed=True) → exécute l'opération
"""
import csv
import json
import logging
import os
import re
import shutil
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# Extensions supportées par catégorie
_TEXT_EXTS = {".txt", ".md", ".py", ".js", ".ts", ".sql", ".sh", ".yaml", ".yml",
              ".toml", ".ini", ".cfg", ".conf", ".log", ".html", ".css", ".xml",
              ".json", ".rst", ".tex", ".r", ".rb", ".go", ".java", ".c", ".cpp",
              ".h", ".hpp", ".cs", ".php", ".swift"}
_CSV_EXTS = {".csv", ".tsv"}
_EXCEL_EXTS = {".xlsx", ".xls"}
_WORD_EXTS = {".docx"}
_PARQUET_EXTS = {".parquet"}

_MAX_PREVIEW_CHARS = 3000  # max chars shown in previews / reads


def _resolve_path(path: str, base_path: Optional[str] = None) -> Path:
    """Résout le chemin et valide qu'il est dans base_path si fourni."""
    p = Path(path).expanduser().resolve()
    if base_path:
        base = Path(base_path).expanduser().resolve()
        try:
            p.relative_to(base)
        except ValueError:
            raise PermissionError(
                f"⛔ Accès refusé : `{p}` est hors du répertoire autorisé `{base}`. "
                f"Utilisez un chemin à l'intérieur de `{base}`."
            )
    return p


def _format_size(size: int) -> str:
    """Format file size human-readable."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _read_any(path: Path, max_chars: int = _MAX_PREVIEW_CHARS) -> str:
    """Lit un fichier selon son extension et retourne une représentation texte."""
    ext = path.suffix.lower()

    if ext in _TEXT_EXTS or ext == "":
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[:max_chars] + f"\n\n… (truncated — {len(text)} total chars)"
        return text

    if ext in _CSV_EXTS:
        delimiter = "\t" if ext == ".tsv" else ","
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        preview = "\n".join(lines[:50])
        if len(lines) > 50:
            preview += f"\n… ({len(lines)} total rows)"
        return preview

    if ext in _EXCEL_EXTS:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        parts = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= 50:
                    rows.append(f"… ({ws.max_row} total rows)")
                    break
                rows.append("\t".join(str(c) if c is not None else "" for c in row))
            parts.append(f"### Sheet: {sheet_name}\n" + "\n".join(rows))
        wb.close()
        return "\n\n".join(parts)

    if ext in _WORD_EXTS:
        from docx import Document
        doc = Document(path)
        paras = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n\n".join(paras)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n… (truncated)"
        return text

    if ext in _PARQUET_EXTS:
        import pyarrow.parquet as pq
        table = pq.read_table(path)
        df_str = table.to_pandas().head(50).to_string()
        schema_str = str(table.schema)
        return f"### Schema\n```\n{schema_str}\n```\n\n### First 50 rows\n```\n{df_str}\n```"

    # Fallback: essaie de lire comme texte
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[:max_chars] + "\n… (truncated)"
        return text
    except Exception as e:
        return f"(Binary file — cannot display as text. Size: {_format_size(path.stat().st_size)})"


# ── Factory principale ────────────────────────────────────────────────────────

def make_file_tools(base_path: Optional[str] = None) -> List[Any]:
    """
    Crée les outils de gestion de fichiers pour l'agent File Manager.

    Args:
        base_path: Répertoire racine autorisé. Si fourni, toutes les opérations
                   sont restreintes à ce répertoire (sécurité sandbox).

    Returns:
        Liste d'outils LangChain pour llm.bind_tools().
    """
    from langchain_core.tools import tool

    def rp(path: str) -> Path:
        return _resolve_path(path, base_path)

    # ── list_directory ────────────────────────────────────────────────────────

    @tool
    def list_directory(path: str = ".", show_hidden: bool = False) -> str:
        """Liste le contenu d'un répertoire.

        Args:
            path: Chemin du répertoire à lister (défaut: répertoire courant).
            show_hidden: Si True, inclut les fichiers cachés (commençant par .)

        Retourne la liste des fichiers et sous-dossiers avec taille et date."""
        try:
            p = rp(path)
            if not p.exists():
                return f"❌ Répertoire introuvable : `{path}`"
            if not p.is_dir():
                return f"❌ `{path}` n'est pas un répertoire."
            entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
            lines = [f"📁 **{p}**\n"]
            dirs, files = [], []
            for entry in entries:
                if not show_hidden and entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    dirs.append(f"  📂 {entry.name}/")
                else:
                    stat = entry.stat()
                    mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                    dirs_count = ""
                    files.append(f"  📄 {entry.name}  ({_format_size(stat.st_size)}, {mtime})")
            lines.extend(dirs)
            lines.extend(files)
            lines.append(f"\n{len(dirs)} dossier(s), {len(files)} fichier(s)")
            return "\n".join(lines)
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── get_file_info ─────────────────────────────────────────────────────────

    @tool
    def get_file_info(path: str) -> str:
        """Retourne les métadonnées d'un fichier ou répertoire (taille, dates, type, permissions).

        Args:
            path: Chemin du fichier ou répertoire."""
        try:
            p = rp(path)
            if not p.exists():
                return f"❌ Introuvable : `{path}`"
            stat = p.stat()
            kind = "Répertoire" if p.is_dir() else "Fichier"
            info = [
                f"**{kind} :** `{p}`",
                f"**Taille :** {_format_size(stat.st_size)}",
                f"**Créé :** {datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S')}",
                f"**Modifié :** {datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')}",
                f"**Extension :** {p.suffix or '(aucune)'}",
            ]
            if p.is_file():
                ext = p.suffix.lower()
                if ext in _EXCEL_EXTS:
                    info.append("**Type :** Tableur Excel")
                elif ext in _CSV_EXTS:
                    info.append("**Type :** CSV / TSV")
                elif ext in _WORD_EXTS:
                    info.append("**Type :** Document Word")
                elif ext in _PARQUET_EXTS:
                    info.append("**Type :** Parquet (données columnar)")
                elif ext in _TEXT_EXTS:
                    info.append("**Type :** Fichier texte")
            return "\n".join(info)
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── search_files ──────────────────────────────────────────────────────────

    @tool
    def search_files(pattern: str, path: str = ".", recursive: bool = True) -> str:
        """Recherche des fichiers par nom ou pattern glob dans un répertoire.

        Args:
            pattern: Pattern de recherche (ex: "*.csv", "rapport_*", "data*.xlsx").
            path: Répertoire de départ (défaut: répertoire courant).
            recursive: Si True, recherche dans tous les sous-dossiers.

        Retourne la liste des fichiers correspondants avec leur chemin complet."""
        try:
            p = rp(path)
            if not p.exists():
                return f"❌ Répertoire introuvable : `{path}`"
            method = p.rglob if recursive else p.glob
            matches = list(method(pattern))
            if not matches:
                return f"Aucun fichier ne correspond à `{pattern}` dans `{p}`"
            lines = [f"**{len(matches)} fichier(s) trouvé(s) pour `{pattern}` :**"]
            for m in sorted(matches)[:100]:
                stat = m.stat()
                lines.append(f"  - `{m}` ({_format_size(stat.st_size)})")
            if len(matches) > 100:
                lines.append(f"  … et {len(matches) - 100} autres.")
            return "\n".join(lines)
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── read_file ─────────────────────────────────────────────────────────────

    @tool
    def read_file(path: str, max_chars: int = 3000) -> str:
        """Lit et affiche le contenu d'un fichier.

        Supporte : .txt, .md, .py, .sql, .json, .yaml, .csv, .xlsx, .docx, .parquet
        et tout autre format texte.

        Args:
            path: Chemin du fichier à lire.
            max_chars: Nombre maximum de caractères à afficher (défaut: 3000).
                       Augmente cette valeur pour lire des fichiers plus grands."""
        try:
            p = rp(path)
            if not p.exists():
                return f"❌ Fichier introuvable : `{path}`"
            if not p.is_file():
                return f"❌ `{path}` est un répertoire, pas un fichier. Utilisez list_directory."
            content = _read_any(p, max_chars=max_chars)
            return f"**Fichier :** `{p}`\n\n```\n{content}\n```"
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur de lecture : {e}"

    # ── create_directory ──────────────────────────────────────────────────────

    @tool
    def create_directory(path: str) -> str:
        """Crée un répertoire (et tous les parents nécessaires).

        Args:
            path: Chemin du répertoire à créer."""
        try:
            p = rp(path)
            if p.exists():
                return f"ℹ️ Le répertoire `{p}` existe déjà."
            p.mkdir(parents=True, exist_ok=True)
            return f"✅ Répertoire créé : `{p}`"
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── create_file ───────────────────────────────────────────────────────────

    @tool
    def create_file(path: str, content: str = "") -> str:
        """Crée un NOUVEAU fichier texte avec le contenu fourni.
        Échoue si le fichier existe déjà (utilise write_file pour modifier un fichier existant).

        Args:
            path: Chemin du nouveau fichier.
            content: Contenu initial du fichier (texte brut)."""
        try:
            p = rp(path)
            if p.exists():
                return (
                    f"❌ Le fichier `{p}` existe déjà. "
                    f"Utilisez write_file pour le modifier."
                )
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return (
                f"✅ Fichier créé : `{p}`\n"
                f"Taille : {_format_size(p.stat().st_size)}"
            )
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── write_file ────────────────────────────────────────────────────────────

    @tool
    def write_file(path: str, content: str, confirmed: bool = False) -> str:
        """Écrit ou remplace le contenu d'un fichier existant.

        ⚠️ OPÉRATION DESTRUCTIVE — demande confirmation avant d'exécuter.

        Workflow OBLIGATOIRE :
          1. Appelle write_file(path, content, confirmed=False) pour afficher un aperçu
             et demander la confirmation de l'utilisateur.
          2. Attend que l'utilisateur réponde explicitement "oui", "confirme", "go"…
          3. Appelle write_file(path, content, confirmed=True) pour exécuter.

        Args:
            path: Chemin du fichier à écrire.
            content: Nouveau contenu complet (remplace l'ancien).
            confirmed: False = aperçu + demande confirmation. True = exécute."""
        try:
            p = rp(path)
            preview = content[:400] + ("…" if len(content) > 400 else "")
            if not confirmed:
                existing_size = ""
                if p.exists():
                    existing_size = f" (actuellement {_format_size(p.stat().st_size)})"
                return (
                    f"⚠️ **CONFIRMATION REQUISE**\n\n"
                    f"**Fichier :** `{p}`{existing_size}\n"
                    f"**Action :** Écrire {len(content)} caractères\n"
                    f"**Aperçu du nouveau contenu :**\n```\n{preview}\n```\n\n"
                    f"Confirmez-vous cette écriture ? (répondez 'oui' / 'confirme' / 'go')"
                )
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return (
                f"✅ Fichier écrit : `{p}`\n"
                f"Taille : {_format_size(p.stat().st_size)}"
            )
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── delete_file ───────────────────────────────────────────────────────────

    @tool
    def delete_file(path: str, confirmed: bool = False) -> str:
        """Supprime un fichier.

        ⚠️ OPÉRATION IRRÉVERSIBLE — demande confirmation avant d'exécuter.

        Workflow OBLIGATOIRE :
          1. Appelle delete_file(path, confirmed=False) → affiche les détails et demande confirmation.
          2. Attend que l'utilisateur réponde "oui" / "confirme".
          3. Appelle delete_file(path, confirmed=True) → supprime le fichier.

        Args:
            path: Chemin du fichier à supprimer.
            confirmed: False = description + demande confirmation. True = exécute."""
        try:
            p = rp(path)
            if not p.exists():
                return f"❌ Fichier introuvable : `{p}`"
            if not p.is_file():
                return f"❌ `{p}` est un répertoire. Utilisez delete_directory."
            stat = p.stat()
            if not confirmed:
                return (
                    f"⚠️ **CONFIRMATION REQUISE**\n\n"
                    f"**Fichier :** `{p}`\n"
                    f"**Taille :** {_format_size(stat.st_size)}\n"
                    f"**Modifié :** {datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')}\n\n"
                    f"🔴 Cette suppression est **irréversible**. Confirmez-vous ? (répondez 'oui' / 'confirme')"
                )
            p.unlink()
            return f"✅ Fichier supprimé : `{p}`"
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── delete_directory ──────────────────────────────────────────────────────

    @tool
    def delete_directory(path: str, confirmed: bool = False) -> str:
        """Supprime un répertoire et tout son contenu.

        ⚠️ OPÉRATION IRRÉVERSIBLE — demande confirmation avant d'exécuter.

        Args:
            path: Chemin du répertoire à supprimer.
            confirmed: False = description + demande confirmation. True = exécute."""
        try:
            p = rp(path)
            if not p.exists():
                return f"❌ Répertoire introuvable : `{p}`"
            if not p.is_dir():
                return f"❌ `{p}` est un fichier. Utilisez delete_file."
            # Compte le contenu
            all_items = list(p.rglob("*"))
            n_files = sum(1 for x in all_items if x.is_file())
            n_dirs = sum(1 for x in all_items if x.is_dir())
            total_size = sum(x.stat().st_size for x in all_items if x.is_file())
            if not confirmed:
                return (
                    f"⚠️ **CONFIRMATION REQUISE**\n\n"
                    f"**Répertoire :** `{p}`\n"
                    f"**Contenu :** {n_files} fichier(s), {n_dirs} sous-dossier(s), {_format_size(total_size)}\n\n"
                    f"🔴 Cette suppression est **irréversible**. Confirmez-vous ? (répondez 'oui' / 'confirme')"
                )
            shutil.rmtree(p)
            return f"✅ Répertoire supprimé : `{p}` ({n_files} fichiers)"
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── move_file ─────────────────────────────────────────────────────────────

    @tool
    def move_file(source: str, destination: str, confirmed: bool = False) -> str:
        """Déplace ou renomme un fichier ou répertoire.

        ⚠️ OPÉRATION DESTRUCTIVE si la destination existe déjà — demande confirmation.

        Args:
            source: Chemin source.
            destination: Chemin de destination.
            confirmed: False = description + demande confirmation. True = exécute."""
        try:
            src = rp(source)
            dst = rp(destination)
            if not src.exists():
                return f"❌ Source introuvable : `{src}`"
            warning = ""
            if dst.exists():
                warning = f"\n⚠️ La destination `{dst}` existe déjà et sera **remplacée**."
            if not confirmed:
                return (
                    f"⚠️ **CONFIRMATION REQUISE**\n\n"
                    f"**Action :** Déplacer / renommer\n"
                    f"**Source :** `{src}`\n"
                    f"**Destination :** `{dst}`{warning}\n\n"
                    f"Confirmez-vous ? (répondez 'oui' / 'confirme')"
                )
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            return f"✅ Déplacé : `{src}` → `{dst}`"
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── list_excel_sheets ─────────────────────────────────────────────────────

    @tool
    def list_excel_sheets(path: str) -> str:
        """Liste tous les onglets d'un fichier Excel (.xlsx/.xls) avec leurs dimensions.

        Args:
            path: Chemin du fichier Excel."""
        try:
            import openpyxl
            p = rp(path)
            if not p.exists():
                return f"❌ Fichier introuvable : `{p}`"
            if p.suffix.lower() not in _EXCEL_EXTS:
                return f"❌ `{p}` n'est pas un fichier Excel (.xlsx/.xls)."
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            lines = [f"**Fichier :** `{p}`\n**Onglets ({len(wb.sheetnames)}) :**"]
            for name in wb.sheetnames:
                ws = wb[name]
                lines.append(f"  - `{name}` — {ws.max_row} lignes × {ws.max_column} colonnes")
            wb.close()
            return "\n".join(lines)
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── read_excel_sheet ──────────────────────────────────────────────────────

    @tool
    def read_excel_sheet(path: str, sheet: str = "", start_row: int = 1,
                         max_rows: int = 50, columns: str = "") -> str:
        """Lit un onglet spécifique d'un fichier Excel avec options de filtrage.

        Args:
            path: Chemin du fichier Excel.
            sheet: Nom de l'onglet (vide = premier onglet).
            start_row: Numéro de la première ligne à lire (défaut: 1).
            max_rows: Nombre maximum de lignes à afficher (défaut: 50).
            columns: Liste de colonnes séparées par virgule, ex: "A,B,D" (vide = toutes)."""
        try:
            import openpyxl
            p = rp(path)
            if not p.exists():
                return f"❌ Fichier introuvable : `{p}`"
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            sheet_name = sheet if sheet and sheet in wb.sheetnames else wb.sheetnames[0]
            ws = wb[sheet_name]
            # Parse column filter (A, B, C → 1, 2, 3)
            col_filter = set()
            if columns.strip():
                for c in columns.split(","):
                    c = c.strip().upper()
                    if c:
                        from openpyxl.utils import column_index_from_string
                        try:
                            col_filter.add(column_index_from_string(c))
                        except Exception:
                            pass
            rows_data = []
            for i, row in enumerate(ws.iter_rows(min_row=start_row, values_only=True)):
                if i >= max_rows:
                    break
                if col_filter:
                    row = tuple(v for j, v in enumerate(row, 1) if j in col_filter)
                rows_data.append("\t".join(str(c) if c is not None else "" for c in row))
            wb.close()
            header = (
                f"**Fichier :** `{p}`\n"
                f"**Onglet :** `{sheet_name}` (lignes {start_row}–{start_row + len(rows_data) - 1})\n\n"
            )
            total = ws.max_row
            footer = f"\n… ({total} lignes totales dans cet onglet)" if total > start_row + max_rows - 1 else ""
            return header + "```\n" + "\n".join(rows_data) + "\n```" + footer
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── create_excel_file ─────────────────────────────────────────────────────

    @tool
    def create_excel_file(path: str, sheet_name: str = "Feuille1",
                          csv_data: str = "") -> str:
        """Crée un nouveau fichier Excel (.xlsx) avec un onglet initial.

        Échoue si le fichier existe déjà (utilisez write_excel_sheet pour modifier).

        Args:
            path: Chemin du nouveau fichier Excel (doit finir par .xlsx).
            sheet_name: Nom du premier onglet (défaut: Feuille1).
            csv_data: Données initiales au format CSV (colonnes séparées par virgule,
                      lignes séparées par saut de ligne). Peut être vide."""
        try:
            import openpyxl
            p = rp(path)
            if p.exists():
                return f"❌ `{p}` existe déjà. Utilisez write_excel_sheet pour modifier."
            if p.suffix.lower() not in (".xlsx",):
                return "❌ Le fichier doit avoir l'extension .xlsx"
            p.parent.mkdir(parents=True, exist_ok=True)
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = sheet_name
            if csv_data.strip():
                for line in csv_data.strip().splitlines():
                    row = [c.strip() for c in line.split(",")]
                    ws.append(row)
            wb.save(p)
            wb.close()
            return (
                f"✅ Fichier Excel créé : `{p}`\n"
                f"Onglet : `{sheet_name}` | {ws.max_row} ligne(s) écrite(s)"
            )
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── write_excel_sheet ─────────────────────────────────────────────────────

    @tool
    def write_excel_sheet(path: str, sheet: str, csv_data: str,
                          confirmed: bool = False) -> str:
        """Écrit ou remplace un onglet Excel avec des données au format CSV.

        ⚠️ OPÉRATION DESTRUCTIVE — demande confirmation.

        Args:
            path: Chemin du fichier Excel.
            sheet: Nom de l'onglet à écrire (créé s'il n'existe pas).
            csv_data: Données au format CSV (colonnes séparées par virgule,
                      lignes séparées par saut de ligne).
            confirmed: False = aperçu + confirmation. True = exécute."""
        try:
            import openpyxl
            p = rp(path)
            lines = csv_data.strip().splitlines()
            preview_lines = "\n".join(lines[:5]) + (f"\n… ({len(lines)} lignes)" if len(lines) > 5 else "")
            if not confirmed:
                action = "remplacé" if p.exists() else "créé"
                return (
                    f"⚠️ **CONFIRMATION REQUISE**\n\n"
                    f"**Fichier :** `{p}` (sera {action})\n"
                    f"**Onglet :** `{sheet}`\n"
                    f"**Données :** {len(lines)} ligne(s)\n"
                    f"**Aperçu :**\n```\n{preview_lines}\n```\n\n"
                    f"Confirmez-vous ? (répondez 'oui' / 'confirme')"
                )
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                wb = openpyxl.load_workbook(p)
            else:
                wb = openpyxl.Workbook()
                if "Sheet" in wb.sheetnames:
                    del wb["Sheet"]
            if sheet in wb.sheetnames:
                del wb[sheet]
            ws = wb.create_sheet(sheet)
            for line in lines:
                row = [c.strip() for c in line.split(",")]
                ws.append(row)
            wb.save(p)
            wb.close()
            return f"✅ Onglet `{sheet}` écrit dans `{p}` ({len(lines)} lignes)"
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── edit_excel_cells ──────────────────────────────────────────────────────

    @tool
    def edit_excel_cells(path: str, sheet: str, updates: str,
                         confirmed: bool = False) -> str:
        """Modifie des cellules spécifiques dans un onglet Excel.

        ⚠️ OPÉRATION DESTRUCTIVE — demande confirmation.

        Args:
            path: Chemin du fichier Excel.
            sheet: Nom de l'onglet à modifier.
            updates: Modifications au format "A1=valeur,B2=autre valeur" (séparées par |).
                     Exemples :
                       "A1=Titre|B1=Montant|C1=Date"
                       "A5=1500|B5=Dupont"
            confirmed: False = aperçu + confirmation. True = exécute."""
        try:
            import openpyxl
            p = rp(path)
            if not p.exists():
                return f"❌ Fichier introuvable : `{p}`"
            # Parse updates: "A1=val|B2=other"
            pairs = []
            for part in updates.split("|"):
                part = part.strip()
                if "=" in part:
                    cell_ref, _, value = part.partition("=")
                    pairs.append((cell_ref.strip().upper(), value.strip()))
            if not pairs:
                return "❌ Format invalide. Utilisez 'A1=valeur|B2=autre'."
            if not confirmed:
                preview = "\n".join(f"  `{c}` ← `{v}`" for c, v in pairs)
                return (
                    f"⚠️ **CONFIRMATION REQUISE**\n\n"
                    f"**Fichier :** `{p}`\n"
                    f"**Onglet :** `{sheet}`\n"
                    f"**Modifications ({len(pairs)}) :**\n{preview}\n\n"
                    f"Confirmez-vous ? (répondez 'oui' / 'confirme')"
                )
            wb = openpyxl.load_workbook(p)
            if sheet not in wb.sheetnames:
                wb.close()
                return f"❌ Onglet `{sheet}` introuvable. Onglets disponibles : {', '.join(wb.sheetnames)}"
            ws = wb[sheet]
            for cell_ref, value in pairs:
                # Try numeric conversion
                try:
                    value = int(value)
                except ValueError:
                    try:
                        value = float(value)
                    except ValueError:
                        pass
                ws[cell_ref] = value
            wb.save(p)
            wb.close()
            return f"✅ {len(pairs)} cellule(s) modifiée(s) dans `{sheet}` de `{p}`"
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── append_excel_rows ─────────────────────────────────────────────────────

    @tool
    def append_excel_rows(path: str, sheet: str, csv_rows: str) -> str:
        """Ajoute des lignes à la fin d'un onglet Excel existant.

        N'efface pas les données existantes — ajoute après la dernière ligne.

        Args:
            path: Chemin du fichier Excel.
            sheet: Nom de l'onglet.
            csv_rows: Lignes à ajouter au format CSV (colonnes séparées par virgule,
                      lignes par saut de ligne)."""
        try:
            import openpyxl
            p = rp(path)
            if not p.exists():
                return f"❌ Fichier introuvable : `{p}`"
            wb = openpyxl.load_workbook(p)
            if sheet not in wb.sheetnames:
                wb.close()
                return f"❌ Onglet `{sheet}` introuvable. Disponibles : {', '.join(wb.sheetnames)}"
            ws = wb[sheet]
            old_rows = ws.max_row
            lines = [l for l in csv_rows.strip().splitlines() if l.strip()]
            for line in lines:
                row = [c.strip() for c in line.split(",")]
                ws.append(row)
            wb.save(p)
            wb.close()
            return (
                f"✅ {len(lines)} ligne(s) ajoutée(s) à `{sheet}` dans `{p}`\n"
                f"Lignes avant : {old_rows} → après : {old_rows + len(lines)}"
            )
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── add_excel_sheet ───────────────────────────────────────────────────────

    @tool
    def add_excel_sheet(path: str, sheet_name: str) -> str:
        """Ajoute un nouvel onglet vide à un fichier Excel existant.

        Args:
            path: Chemin du fichier Excel.
            sheet_name: Nom du nouvel onglet."""
        try:
            import openpyxl
            p = rp(path)
            if not p.exists():
                return f"❌ Fichier introuvable : `{p}`"
            wb = openpyxl.load_workbook(p)
            if sheet_name in wb.sheetnames:
                wb.close()
                return f"❌ L'onglet `{sheet_name}` existe déjà."
            wb.create_sheet(sheet_name)
            wb.save(p)
            wb.close()
            return f"✅ Onglet `{sheet_name}` créé dans `{p}`"
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── delete_excel_sheet ────────────────────────────────────────────────────

    @tool
    def delete_excel_sheet(path: str, sheet_name: str, confirmed: bool = False) -> str:
        """Supprime un onglet d'un fichier Excel.

        ⚠️ OPÉRATION IRRÉVERSIBLE — demande confirmation.

        Args:
            path: Chemin du fichier Excel.
            sheet_name: Nom de l'onglet à supprimer.
            confirmed: False = demande confirmation. True = exécute."""
        try:
            import openpyxl
            p = rp(path)
            if not p.exists():
                return f"❌ Fichier introuvable : `{p}`"
            wb = openpyxl.load_workbook(p)
            if sheet_name not in wb.sheetnames:
                wb.close()
                return f"❌ Onglet `{sheet_name}` introuvable. Disponibles : {', '.join(wb.sheetnames)}"
            ws = wb[sheet_name]
            n_rows = ws.max_row
            if not confirmed:
                wb.close()
                return (
                    f"⚠️ **CONFIRMATION REQUISE**\n\n"
                    f"**Fichier :** `{p}`\n"
                    f"**Onglet à supprimer :** `{sheet_name}` ({n_rows} lignes)\n\n"
                    f"🔴 Cette suppression est **irréversible**. Confirmez-vous ? (répondez 'oui' / 'confirme')"
                )
            del wb[sheet_name]
            wb.save(p)
            wb.close()
            return f"✅ Onglet `{sheet_name}` supprimé de `{p}`"
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── rename_excel_sheet ────────────────────────────────────────────────────

    @tool
    def rename_excel_sheet(path: str, old_name: str, new_name: str) -> str:
        """Renomme un onglet dans un fichier Excel.

        Args:
            path: Chemin du fichier Excel.
            old_name: Nom actuel de l'onglet.
            new_name: Nouveau nom de l'onglet."""
        try:
            import openpyxl
            p = rp(path)
            if not p.exists():
                return f"❌ Fichier introuvable : `{p}`"
            wb = openpyxl.load_workbook(p)
            if old_name not in wb.sheetnames:
                wb.close()
                return f"❌ Onglet `{old_name}` introuvable. Disponibles : {', '.join(wb.sheetnames)}"
            if new_name in wb.sheetnames:
                wb.close()
                return f"❌ Un onglet `{new_name}` existe déjà."
            wb[old_name].title = new_name
            wb.save(p)
            wb.close()
            return f"✅ Onglet renommé : `{old_name}` → `{new_name}` dans `{p}`"
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── read_csv_summary ──────────────────────────────────────────────────────

    @tool
    def read_csv_summary(path: str, rows: int = 10) -> str:
        """Lit un fichier CSV/TSV et retourne un résumé : colonnes, types et premières lignes.

        Plus informative que read_file pour les données tabulaires.

        Args:
            path: Chemin du fichier CSV ou TSV.
            rows: Nombre de lignes à afficher (défaut: 10)."""
        try:
            import pandas as pd
            p = rp(path)
            if not p.exists():
                return f"❌ Fichier introuvable : `{p}`"
            sep = "\t" if p.suffix.lower() == ".tsv" else ","
            df = pd.read_csv(p, sep=sep, nrows=200, low_memory=False)
            total_rows = len(pd.read_csv(p, sep=sep, usecols=[0]))
            summary = [
                f"**Fichier :** `{p}`",
                f"**Dimensions :** {total_rows} lignes × {len(df.columns)} colonnes",
                f"\n**Colonnes et types :**",
            ]
            for col, dtype in df.dtypes.items():
                summary.append(f"  - `{col}` : {dtype}")
            summary.append(f"\n**Premières {min(rows, len(df))} lignes :**\n```")
            summary.append(df.head(rows).to_string(index=False))
            summary.append("```")
            return "\n".join(summary)
        except ImportError:
            return "❌ pandas non installé. Utilisez read_file pour lire le CSV brut."
        except PermissionError as e:
            return str(e)
        except Exception as e:
            return f"❌ Erreur : {e}"

    return [
        list_directory,
        get_file_info,
        search_files,
        read_file,
        create_directory,
        create_file,
        write_file,
        delete_file,
        delete_directory,
        move_file,
        read_csv_summary,
        # Excel tools
        list_excel_sheets,
        read_excel_sheet,
        create_excel_file,
        write_excel_sheet,
        edit_excel_cells,
        append_excel_rows,
        add_excel_sheet,
        delete_excel_sheet,
        rename_excel_sheet,
    ]

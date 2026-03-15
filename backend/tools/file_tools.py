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
    ]

"""
Outils Playwright pour l'agent Power BI Analyst.

Architecture :
  - make_powerbi_tools(session_id, ...) → Liste d'outils LangChain
  - Chaque appel d'outil réutilise le même contexte navigateur (_PlaywrightSession)
    identifié par session_id.
  - Les captures d'écran sont sauvegardées dans screenshots_dir.
  - Les sessions navigateur persistent entre les appels d'outils d'une conversation
    (cookies Power BI conservés = pas de reconnexion à chaque appel).

Prérequis :
  pip install playwright
  playwright install chromium

Authentification Power BI :
  1. Lancer l'agent avec headless=False (via extra_config).
  2. Se connecter manuellement dans le navigateur visible.
  3. Appeler save_browser_session() pour sauvegarder les cookies dans state.json.
  4. Repasser headless=True pour les prochaines sessions.
"""
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Registry des sessions navigateur actives : session_id → _PlaywrightSession
_active_sessions: Dict[str, Any] = {}


class _PlaywrightSession:
    """Contexte Playwright persistant pour une conversation."""

    def __init__(
        self,
        headless: bool = True,
        session_file: Optional[str] = None,
        screenshots_dir: str = "data/powerbi_screenshots",
    ):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=headless)
        self.screenshots_dir = Path(screenshots_dir)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.session_file = session_file

        ctx_kwargs: Dict[str, Any] = {}
        if session_file:
            sp = Path(session_file)
            if sp.exists():
                ctx_kwargs["storage_state"] = str(sp)
                logger.info("PowerBI: chargement session depuis %s", sp)

        self.context = self.browser.new_context(**ctx_kwargs)
        self.page = self.context.new_page()
        logger.info("PowerBI Playwright session démarrée (headless=%s)", headless)

    def close(self):
        try:
            self.browser.close()
        except Exception:
            pass
        try:
            self._pw.stop()
        except Exception:
            pass


def _get_session(
    session_id: str,
    headless: bool = True,
    session_file: Optional[str] = None,
    screenshots_dir: str = "data/powerbi_screenshots",
) -> _PlaywrightSession:
    """Retourne ou crée la session Playwright pour cette conversation."""
    if session_id not in _active_sessions:
        _active_sessions[session_id] = _PlaywrightSession(
            headless=headless,
            session_file=session_file,
            screenshots_dir=screenshots_dir,
        )
    return _active_sessions[session_id]


def close_session(session_id: str):
    """Ferme et nettoie la session navigateur."""
    sess = _active_sessions.pop(session_id, None)
    if sess:
        sess.close()


# ── Factory principale ────────────────────────────────────────────────────────

def make_powerbi_tools(
    session_id: str,
    headless: bool = True,
    session_file: Optional[str] = None,
    screenshots_dir: str = "data/powerbi_screenshots",
) -> List[Any]:
    """
    Crée les outils Playwright pour l'agent Power BI Analyst.

    Args:
        session_id:      ID de la session (isole les navigateurs entre conversations).
        headless:        Si False, affiche le navigateur (utile pour l'auth manuelle).
        session_file:    Chemin vers state.json pour persister les cookies.
        screenshots_dir: Répertoire de sauvegarde des captures d'écran.

    Returns:
        Liste d'outils LangChain compatibles avec llm.bind_tools().
    """
    from langchain_core.tools import tool

    def sess() -> _PlaywrightSession:
        return _get_session(session_id, headless, session_file, screenshots_dir)

    # ── navigate_to_report ────────────────────────────────────────────────────

    @tool
    def navigate_to_report(url: str) -> str:
        """Navigue vers un rapport Power BI via son URL complète.

        Args:
            url: URL du rapport (ex: https://app.powerbi.com/groups/me/reports/REPORT_ID).

        Retourne le titre de la page chargée ou un message d'erreur."""
        try:
            s = sess()
            s.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            title = s.page.title()
            current_url = s.page.url
            return (
                f"✅ Navigué vers : {url}\n"
                f"**Titre :** {title}\n"
                f"**URL actuelle :** {current_url}"
            )
        except Exception as e:
            return (
                f"❌ Erreur de navigation vers '{url}' : {e}\n"
                "Vérifiez que l'URL est correcte et que le navigateur est authentifié.\n"
                "Si une connexion est requise, configurez headless=false et utilisez save_browser_session."
            )

    # ── wait_for_visuals ──────────────────────────────────────────────────────

    @tool
    def wait_for_visuals(timeout_seconds: int = 30) -> str:
        """Attend que les visuels Power BI soient rendus dans la page.

        Cherche les sélecteurs .visualCard / .visual-container pour confirmer
        que le dashboard est complètement chargé.

        Args:
            timeout_seconds: Délai maximum d'attente en secondes (défaut: 30).

        Retourne le nombre de visuels détectés ou un message d'erreur/suggestion."""
        try:
            s = sess()
            s.page.wait_for_selector(
                ".visualCard, .visual-container, [data-testid='visual-container']",
                timeout=timeout_seconds * 1000,
            )
            count = s.page.locator(".visualCard, .visual-container").count()
            title = s.page.title()
            return (
                f"✅ Dashboard chargé — {count} visuel(s) détecté(s).\n"
                f"Page : {title}"
            )
        except Exception as e:
            return (
                f"⚠️ Délai d'attente dépassé ({timeout_seconds}s) : {e}\n"
                "**Suggestions :**\n"
                "• Authentification manquante → utilisez save_browser_session après connexion manuelle.\n"
                "• URL incorrecte → vérifiez l'ID du rapport.\n"
                "• Utilisez capture_screenshot pour voir l'état actuel de la page."
            )

    # ── navigate_to_tab ───────────────────────────────────────────────────────

    @tool
    def navigate_to_tab(tab_name: str) -> str:
        """Clique sur un onglet (page) du rapport Power BI.

        Args:
            tab_name: Nom de l'onglet affiché en bas du rapport
                      (ex: 'Analyse Mensuelle', 'Vue Globale', 'KPIs').

        Retourne une confirmation ou une erreur avec suggestions."""
        try:
            s = sess()
            try:
                s.page.click(f"text='{tab_name}'", timeout=5_000)
            except Exception:
                try:
                    s.page.click(f"[aria-label='{tab_name}']", timeout=5_000)
                except Exception:
                    s.page.get_by_role("tab", name=tab_name).click(timeout=5_000)
            time.sleep(2)
            return f"✅ Onglet sélectionné : '{tab_name}'. Visuels en cours de chargement."
        except Exception as e:
            return (
                f"❌ Onglet '{tab_name}' introuvable : {e}\n"
                "Conseil : utilisez get_page_elements('.tabLabel') pour lister les onglets disponibles.\n"
                "Le nom est sensible à la casse et aux espaces."
            )

    # ── capture_screenshot ────────────────────────────────────────────────────

    @tool
    def capture_screenshot(label: str = "dashboard") -> str:
        """Prend une capture d'écran de l'état actuel du rapport Power BI.

        La capture est sauvegardée dans le dossier screenshots configuré.
        Utilisez cette capture pour analyser visuellement les KPIs, tendances et anomalies.

        Args:
            label: Nom descriptif pour identifier la capture
                   (ex: 'kpi_overview', 'monthly_trend', 'region_filter').

        Retourne le nom du fichier et une description de la page capturée."""
        try:
            s = sess()
            safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
            filename = f"{safe_label}_{uuid.uuid4().hex[:8]}.png"
            filepath = s.screenshots_dir / filename
            s.page.screenshot(path=str(filepath), full_page=False)
            size_kb = filepath.stat().st_size / 1024
            title = s.page.title()
            current_url = s.page.url
            return (
                f"SCREENSHOT_CAPTURED: {filename}\n"
                f"Taille : {size_kb:.1f} KB\n"
                f"Page capturée : {title}\n"
                f"URL : {current_url}\n"
                f"La capture d'écran est disponible pour affichage. "
                f"Utilisez extract_visual_data() pour analyser le contenu textuel du dashboard."
            )
        except Exception as e:
            return f"❌ Erreur de capture : {e}"

    # ── extract_visual_data ───────────────────────────────────────────────────

    @tool
    def extract_visual_data() -> str:
        """Extrait les données textuelles visibles sur la page Power BI.

        Récupère les titres, valeurs KPI, labels et textes des visuels
        directement depuis le DOM — utile pour l'analyse sans vision IA.

        Retourne un résumé structuré des données extraites."""
        try:
            s = sess()
            sections = []

            # Titre de la page
            title = s.page.title()
            sections.append(f"**Titre de la page :** {title}")

            # Onglets disponibles
            tabs = []
            for sel in [".tabLabel", "[role='tab']", ".reportPagesContainer button"]:
                for el in s.page.locator(sel).all()[:20]:
                    try:
                        txt = el.inner_text().strip()
                        if txt and txt not in tabs:
                            tabs.append(txt)
                    except Exception:
                        continue
            if tabs:
                sections.append(f"**Onglets disponibles :** {', '.join(tabs)}")

            # Titres des visuels
            vis_titles = []
            for sel in [
                "[data-testid='visual-title']",
                ".visualTitle",
                ".titleText",
                ".title",
                "[class*='visualTitle']",
            ]:
                for el in s.page.locator(sel).all()[:20]:
                    try:
                        txt = el.inner_text().strip()
                        if txt and txt not in vis_titles:
                            vis_titles.append(txt)
                    except Exception:
                        continue
            if vis_titles:
                sections.append("**Titres des visuels :**\n" + "\n".join(f"  - {t}" for t in vis_titles))

            # Valeurs KPI
            kpi_values = []
            for sel in [
                ".kpiValue",
                ".kpiStatus",
                "[class*='kpi']",
                ".cardValue",
                ".labelValue",
            ]:
                for el in s.page.locator(sel).all()[:20]:
                    try:
                        txt = el.inner_text().strip()
                        if txt and txt not in kpi_values:
                            kpi_values.append(txt)
                    except Exception:
                        continue
            if kpi_values:
                sections.append("**Valeurs KPI :**\n" + "\n".join(f"  - {v}" for v in kpi_values))

            # Contenu général des visuels (fallback)
            visual_texts = []
            for el in s.page.locator(".visualCard, .visual-container").all()[:8]:
                try:
                    txt = el.inner_text().strip()[:300]
                    if txt and txt not in visual_texts:
                        visual_texts.append(txt)
                except Exception:
                    continue
            if visual_texts:
                sections.append(
                    "**Contenu des visuels :**\n"
                    + "\n---\n".join(f"  {t}" for t in visual_texts)
                )

            if len(sections) <= 1:
                return (
                    "⚠️ Aucune donnée textuelle extraite. "
                    "La page n'est peut-être pas encore chargée ou nécessite une authentification. "
                    "Essayez d'abord navigate_to_report() puis wait_for_visuals()."
                )

            return "\n\n".join(sections)
        except Exception as e:
            return f"❌ Erreur d'extraction : {e}"

    # ── get_page_elements ─────────────────────────────────────────────────────

    @tool
    def get_page_elements(selector: str = ".tabLabel") -> str:
        """Liste les éléments visibles correspondant à un sélecteur CSS.

        Utile pour découvrir les onglets, filtres, slicers et valeurs disponibles.

        Args:
            selector: Sélecteur CSS (ex: '.tabLabel' pour onglets,
                      '.slicerText' pour filtres, '.kpiValue' pour KPIs,
                      '[role=\"button\"]' pour tous les boutons).

        Retourne la liste des textes des éléments trouvés (max 30)."""
        try:
            s = sess()
            elements = s.page.locator(selector).all()
            texts = []
            for el in elements[:30]:
                try:
                    txt = el.inner_text().strip()
                    if txt:
                        texts.append(txt)
                except Exception:
                    continue
            if not texts:
                return (
                    f"Aucun élément trouvé pour `{selector}`.\n"
                    "Essayez '.tabLabel', '[role=\"tab\"]', '.visualCard', '.slicerText', "
                    "'.kpiValue', '[aria-label]'."
                )
            return (
                f"**{len(texts)} élément(s) trouvé(s) pour `{selector}` :**\n"
                + "\n".join(f"  - {t}" for t in texts)
            )
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── click_element ─────────────────────────────────────────────────────────

    @tool
    def click_element(selector: str) -> str:
        """Clique sur un élément de la page par sélecteur CSS ou texte.

        Utilisé pour interagir avec des filtres, boutons, menus déroulants.

        Args:
            selector: Sélecteur CSS ou texte
                      (ex: "text='2024'", ".filterButton", "[aria-label='Filtrer']").

        Retourne une confirmation ou un message d'erreur."""
        try:
            s = sess()
            s.page.click(selector, timeout=10_000)
            time.sleep(1)
            return f"✅ Clic effectué sur : `{selector}`"
        except Exception as e:
            return (
                f"❌ Impossible de cliquer sur `{selector}` : {e}\n"
                "Conseil : utilisez get_page_elements pour trouver le bon sélecteur."
            )

    # ── apply_slicer ──────────────────────────────────────────────────────────

    @tool
    def apply_slicer(slicer_label: str, value: str) -> str:
        """Applique une valeur à un filtre (slicer) Power BI.

        Args:
            slicer_label: Label du slicer (ex: 'Année', 'Région', 'Produit', 'Catégorie').
            value:        Valeur à sélectionner (ex: '2024', 'Europe', 'Q1').

        Retourne une confirmation ou un message d'erreur avec suggestions."""
        try:
            s = sess()
            try:
                # Chercher le conteneur du slicer puis cliquer sur la valeur
                slicer = s.page.locator(f":text('{slicer_label}')").first
                slicer.wait_for(timeout=5_000)
                # Cliquer sur la valeur dans le même conteneur parent
                parent = slicer.locator("xpath=ancestor::div[3]")
                parent.locator(f"text='{value}'").click(timeout=5_000)
            except Exception:
                # Fallback direct
                s.page.click(f"text='{value}'", timeout=8_000)
            time.sleep(2)
            return f"✅ Filtre appliqué : **{slicer_label}** = '{value}'"
        except Exception as e:
            return (
                f"❌ Erreur lors de l'application du filtre '{slicer_label}' = '{value}' : {e}\n"
                "Conseil : utilisez get_page_elements('.slicerText') pour lister les filtres disponibles."
            )

    # ── get_current_url ───────────────────────────────────────────────────────

    @tool
    def get_current_url() -> str:
        """Retourne l'URL actuelle et le titre de la page.

        Utile pour vérifier la navigation et l'état du rapport."""
        try:
            s = sess()
            return (
                f"**URL actuelle :** {s.page.url}\n"
                f"**Titre :** {s.page.title()}"
            )
        except Exception as e:
            return f"❌ Erreur : {e}"

    # ── save_browser_session ──────────────────────────────────────────────────

    @tool
    def save_browser_session() -> str:
        """Sauvegarde l'état de la session navigateur (cookies, localStorage).

        À utiliser après une connexion manuelle pour éviter le MFA à chaque session.
        Le fichier state.json sera réutilisé automatiquement à la prochaine conversation.

        Retourne le chemin du fichier de session sauvegardé."""
        try:
            s = sess()
            if not s.session_file:
                return (
                    "⚠️ Aucun fichier de session configuré.\n"
                    "Définissez 'session_file' dans extra_config de l'agent "
                    "(ex: 'data/powerbi_session.json')."
                )
            Path(s.session_file).parent.mkdir(parents=True, exist_ok=True)
            s.context.storage_state(path=s.session_file)
            return (
                f"✅ Session sauvegardée : `{s.session_file}`\n"
                "La prochaine connexion réutilisera ces cookies automatiquement."
            )
        except Exception as e:
            return f"❌ Erreur de sauvegarde : {e}"

    # ── close_browser ─────────────────────────────────────────────────────────

    @tool
    def close_browser() -> str:
        """Ferme le navigateur et libère les ressources.

        Le navigateur sera relancé automatiquement au prochain appel d'outil.
        Utile pour résoudre des problèmes ou forcer un rechargement complet."""
        try:
            close_session(session_id)
            return "✅ Navigateur fermé. Il sera relancé automatiquement au prochain appel d'outil."
        except Exception as e:
            return f"❌ Erreur lors de la fermeture : {e}"

    return [
        navigate_to_report,
        wait_for_visuals,
        navigate_to_tab,
        capture_screenshot,
        extract_visual_data,
        get_page_elements,
        click_element,
        apply_slicer,
        get_current_url,
        save_browser_session,
        close_browser,
    ]

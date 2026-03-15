"""
Outils Playwright pour l'agent Web Scraper.

Architecture :
  - make_web_scraper_tools(agent_id, session_id, ...) → Liste d'outils LangChain
  - Chaque session réutilise le même contexte navigateur (_BrowserSession)
  - Les captures sont sauvegardées dans screenshots_dir
  - URL validation contre allowed_urls de la config agent

Prérequis :
  pip install playwright beautifulsoup4
  playwright install chromium
"""
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_DATA_DIR = Path("data/web_scraper")
_SCREENSHOTS_DIR = Path("data/web_scraper_screenshots")

# Registry of active browser sessions
_sessions: Dict[str, Any] = {}


class _BrowserSession:
    """Persistent Playwright context for one conversation."""

    def __init__(self, headless: bool = True, screenshots_dir: str = "data/web_scraper_screenshots"):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        self.context = self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        self.page = self.context.new_page()
        self.screenshots_dir = Path(screenshots_dir)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        logger.info("WebScraper browser session démarrée (headless=%s)", headless)

    def close(self):
        try:
            self.page.close()
            self.context.close()
            self.browser.close()
            self._pw.stop()
        except Exception:
            pass


def _get_session(session_id: str, headless: bool = True, screenshots_dir: str = "data/web_scraper_screenshots") -> "_BrowserSession":
    if session_id not in _sessions:
        _sessions[session_id] = _BrowserSession(headless=headless, screenshots_dir=screenshots_dir)
    return _sessions[session_id]


def _is_url_allowed(url: str, allowed_patterns: List[str]) -> bool:
    """Check if URL matches any allowed pattern (supports wildcards and domains)."""
    if not allowed_patterns:
        return True  # No restrictions configured
    url_lower = url.lower()
    parsed = urlparse(url_lower)
    for pattern in allowed_patterns:
        p = pattern.strip().lower()
        if not p:
            continue
        # Wildcard: *.example.com or example.com/*
        if "*" in p:
            regex = re.escape(p).replace(r"\*", ".*")
            if re.match(regex, url_lower):
                return True
        # Domain only: example.com
        elif "://" not in p:
            if parsed.netloc == p or parsed.netloc.endswith("." + p):
                return True
        # Full URL prefix: https://example.com/path
        else:
            if url_lower.startswith(p):
                return True
    return False


def _clean_html_to_text(html: str) -> str:
    """Convert HTML to clean text using BeautifulSoup."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        # Remove script, style, nav, footer, header noise
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        # Collapse multiple blank lines
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return "\n".join(lines[:500])  # cap at 500 lines
    except Exception:
        return html[:3000]


def _extract_table_html(html: str) -> str:
    """Extract all HTML tables as markdown tables."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        if not tables:
            return "Aucun tableau trouvé sur la page."

        result_parts = []
        for idx, table in enumerate(tables, 1):
            rows = table.find_all("tr")
            if not rows:
                continue

            md_rows = []
            header_done = False
            for row in rows:
                cells = row.find_all(["th", "td"])
                cell_texts = [c.get_text(strip=True).replace("|", "\\|") for c in cells]
                if not cell_texts:
                    continue
                md_rows.append("| " + " | ".join(cell_texts) + " |")
                if not header_done:
                    md_rows.append("| " + " | ".join(["---"] * len(cell_texts)) + " |")
                    header_done = True

            if md_rows:
                result_parts.append(f"**Tableau {idx}**\n" + "\n".join(md_rows))

        return "\n\n".join(result_parts) if result_parts else "Aucun tableau avec données trouvé."
    except Exception as e:
        return f"Erreur extraction tableaux: {e}"


def make_web_scraper_tools(
    agent_id: str,
    session_id: str,
    allowed_urls: Optional[List[str]] = None,
    headless: bool = True,
    screenshots_dir: str = "data/web_scraper_screenshots",
) -> list:
    """Create and return a list of LangChain tools for web scraping."""
    from langchain_core.tools import tool

    _allowed = allowed_urls or []

    def _sess() -> "_BrowserSession":
        return _get_session(session_id, headless=headless, screenshots_dir=screenshots_dir)

    def _check_url(url: str) -> Optional[str]:
        if _allowed and not _is_url_allowed(url, _allowed):
            return (
                f"❌ URL non autorisée : {url}\n"
                f"URLs autorisées : {', '.join(_allowed)}\n"
                "Configurez les URLs autorisées dans les options de l'agent."
            )
        return None

    @tool
    def navigate_to_url(url: str) -> str:
        """Navigate to a URL in the browser.
        Args:
            url: The URL to navigate to (must be in allowed_urls if configured)
        """
        err = _check_url(url)
        if err:
            return err
        try:
            sess = _sess()
            sess.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(1)
            title = sess.page.title()
            current = sess.page.url
            return f"✅ Navigation vers : {current}\nTitre : {title}"
        except Exception as e:
            return f"❌ Erreur navigation : {e}"

    @tool
    def get_page_text(max_chars: int = 5000) -> str:
        """Get the text content of the current page (clean, without HTML tags).
        Args:
            max_chars: Maximum number of characters to return (default 5000)
        """
        try:
            sess = _sess()
            html = sess.page.content()
            text = _clean_html_to_text(html)
            url = sess.page.url
            title = sess.page.title()
            return f"**URL** : {url}\n**Titre** : {title}\n\n{text[:max_chars]}"
        except Exception as e:
            return f"❌ Erreur récupération texte : {e}"

    @tool
    def get_page_html(selector: str = "body", max_chars: int = 8000) -> str:
        """Get the raw HTML of a specific element on the current page.
        Args:
            selector: CSS selector (default 'body')
            max_chars: Maximum number of characters to return
        """
        try:
            sess = _sess()
            element = sess.page.query_selector(selector)
            if not element:
                return f"❌ Aucun élément trouvé pour le sélecteur : {selector}"
            html = element.inner_html()
            return html[:max_chars]
        except Exception as e:
            return f"❌ Erreur récupération HTML : {e}"

    @tool
    def extract_elements(selector: str, attribute: str = "text") -> str:
        """Extract all elements matching a CSS selector from the current page.
        Args:
            selector: CSS selector (e.g., 'h1', '.product-name', 'table tr td:first-child')
            attribute: 'text' for text content, or any HTML attribute name (e.g., 'href', 'src', 'data-id')
        """
        try:
            sess = _sess()
            elements = sess.page.query_selector_all(selector)
            if not elements:
                return f"Aucun élément trouvé pour : {selector}"

            results = []
            for el in elements[:100]:  # cap at 100
                if attribute == "text":
                    val = el.inner_text().strip()
                else:
                    val = el.get_attribute(attribute) or ""
                if val:
                    results.append(val)

            if not results:
                return f"Éléments trouvés ({len(elements)}) mais aucun contenu '{attribute}'."
            return (
                f"**{len(results)} résultat(s)** pour `{selector}` [attr={attribute}] :\n"
                + "\n".join(f"- {r}" for r in results[:50])
            )
        except Exception as e:
            return f"❌ Erreur extraction éléments : {e}"

    @tool
    def get_page_links(filter_text: str = "") -> str:
        """Get all links on the current page.
        Args:
            filter_text: Optional text to filter links by href or anchor text (case-insensitive)
        """
        try:
            sess = _sess()
            anchors = sess.page.query_selector_all("a[href]")
            links = []
            base_url = sess.page.url
            for a in anchors[:200]:
                href = a.get_attribute("href") or ""
                text = a.inner_text().strip()[:80]
                # Resolve relative URLs
                if href.startswith("/"):
                    parsed = urlparse(base_url)
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"
                if not href.startswith("http"):
                    continue
                if filter_text and filter_text.lower() not in href.lower() and filter_text.lower() not in text.lower():
                    continue
                err = _check_url(href)
                allowed_mark = "" if not _allowed else (" ✅" if not err else " 🚫")
                links.append(f"- [{text or href}]({href}){allowed_mark}")

            if not links:
                return "Aucun lien trouvé" + (f" correspondant à '{filter_text}'" if filter_text else "") + "."
            return f"**{len(links)} lien(s)** :\n" + "\n".join(links[:50])
        except Exception as e:
            return f"❌ Erreur récupération liens : {e}"

    @tool
    def extract_tables() -> str:
        """Extract all HTML tables from the current page as Markdown tables."""
        try:
            sess = _sess()
            html = sess.page.content()
            return _extract_table_html(html)
        except Exception as e:
            return f"❌ Erreur extraction tableaux : {e}"

    @tool
    def take_screenshot(label: str = "capture") -> str:
        """Take a screenshot of the current page.
        Args:
            label: A short descriptive label for the screenshot
        """
        try:
            sess = _sess()
            filename = f"scraper_{session_id[:8]}_{label.replace(' ', '_')}_{uuid.uuid4().hex[:6]}.png"
            path = sess.screenshots_dir / filename
            sess.page.screenshot(path=str(path), full_page=False)
            logger.info("WebScraper screenshot: %s", filename)
            return f"SCREENSHOT_CAPTURED:{filename}\n✅ Screenshot '{label}' sauvegardé : {filename}"
        except Exception as e:
            return f"❌ Erreur screenshot : {e}"

    @tool
    def click_element(selector: str) -> str:
        """Click an element on the current page.
        Args:
            selector: CSS selector of the element to click
        """
        try:
            sess = _sess()
            sess.page.click(selector, timeout=5000)
            time.sleep(0.8)
            return f"✅ Clic sur '{selector}' effectué. URL actuelle : {sess.page.url}"
        except Exception as e:
            return f"❌ Erreur clic : {e}"

    @tool
    def fill_input(selector: str, value: str) -> str:
        """Fill an input field on the current page.
        Args:
            selector: CSS selector of the input field
            value: Text to type into the field
        """
        try:
            sess = _sess()
            sess.page.fill(selector, value, timeout=5000)
            return f"✅ Champ '{selector}' rempli avec : {value[:50]}"
        except Exception as e:
            return f"❌ Erreur remplissage champ : {e}"

    @tool
    def scroll_page(direction: str = "down", amount: int = 500) -> str:
        """Scroll the current page.
        Args:
            direction: 'down', 'up', 'bottom', or 'top'
            amount: Pixels to scroll (for 'down'/'up')
        """
        try:
            sess = _sess()
            if direction == "bottom":
                sess.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            elif direction == "top":
                sess.page.evaluate("window.scrollTo(0, 0)")
            elif direction == "down":
                sess.page.evaluate(f"window.scrollBy(0, {amount})")
            else:
                sess.page.evaluate(f"window.scrollBy(0, -{amount})")
            time.sleep(0.5)
            return f"✅ Scroll '{direction}' ({amount}px) effectué."
        except Exception as e:
            return f"❌ Erreur scroll : {e}"

    @tool
    def wait_for_element(selector: str, timeout_ms: int = 5000) -> str:
        """Wait for an element to appear on the page.
        Args:
            selector: CSS selector to wait for
            timeout_ms: Maximum wait time in milliseconds
        """
        try:
            sess = _sess()
            sess.page.wait_for_selector(selector, timeout=timeout_ms)
            return f"✅ Élément '{selector}' trouvé."
        except Exception as e:
            return f"❌ Timeout ou élément absent '{selector}' : {e}"

    @tool
    def get_current_url() -> str:
        """Get the current URL and page title."""
        try:
            sess = _sess()
            return f"URL : {sess.page.url}\nTitre : {sess.page.title()}"
        except Exception as e:
            return f"❌ Erreur : {e}"

    @tool
    def save_scraped_data(content: str, filename: str, format: str = "json") -> str:
        """Save scraped data to a file.
        Args:
            content: The data content to save (JSON string, CSV string, or plain text)
            filename: Base filename (without extension)
            format: 'json', 'csv', or 'txt'
        """
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", filename)
            ext = {"json": ".json", "csv": ".csv", "txt": ".txt"}.get(format, ".txt")
            filepath = _DATA_DIR / f"{safe_name}{ext}"
            filepath.write_text(content, encoding="utf-8")
            return f"✅ Données sauvegardées : {filepath}\nTaille : {len(content)} caractères"
        except Exception as e:
            return f"❌ Erreur sauvegarde : {e}"

    return [
        navigate_to_url,
        get_page_text,
        get_page_html,
        extract_elements,
        get_page_links,
        extract_tables,
        take_screenshot,
        click_element,
        fill_input,
        scroll_page,
        wait_for_element,
        get_current_url,
        save_scraped_data,
    ]


def close_session(session_id: str):
    """Close and remove a browser session."""
    sess = _sessions.pop(session_id, None)
    if sess:
        sess.close()

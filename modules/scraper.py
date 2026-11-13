"""Descargador web: login headless y descarga del Excel de la rueda de bonos de tasa fija.

Funciona igual en local y en GitHub Actions (headless=True, sin pantalla).
Los selectores tienen valores por defecto razonables y se pueden ajustar por variables de entorno
(WEB_USER_SELECTOR, WEB_PASS_SELECTOR, WEB_SUBMIT_SELECTOR, WEB_BONOS_URL, WEB_BONOS_TEXT,
WEB_DOWNLOAD_SELECTOR) sin tocar el código. Si algo falla se guarda una captura y el HTML de la
página en ./data/ para ver qué pasó.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import config
from config import Settings

log = logging.getLogger(__name__)


class ScraperError(RuntimeError):
    """Falló el login, la navegación o la descarga."""


def _visible(page, selector: str):
    """Primer elemento VISIBLE que cumple el selector (ignora coincidencias ocultas de otros paneles)."""
    return page.locator(f"{selector} >> visible=true").first


def _login(page, s: Settings) -> None:
    url = s.web_login_url or s.web_url
    log.info("Abriendo %s ...", url)
    page.goto(url, wait_until="domcontentloaded")

    campo_user = _visible(page, s.selector_user)
    campo_pass = _visible(page, s.selector_pass)
    campo_user.wait_for(state="visible")
    campo_user.fill(s.web_user)
    campo_pass.fill(s.web_pass)

    boton = _visible(page, s.selector_submit)
    if page.locator(f"{s.selector_submit} >> visible=true").count():
        boton.click()
    else:
        campo_pass.press("Enter")

    try:  # login correcto = el formulario desaparece (en una SPA la URL no cambia)
        campo_pass.wait_for(state="hidden", timeout=min(s.timeout_ms, 20_000))
    except Exception:
        raise ScraperError("El formulario de login sigue visible después de enviarlo: ¿usuario o clave incorrectos?") from None
    log.info("Login correcto.")


def _cerrar_popup(page, s: Settings, espera_s: float = 4.0) -> None:
    """Cierra el cartel emergente inicial si aparece (no es un error que no esté)."""
    if not s.selector_popup:
        return
    boton = _visible(page, s.selector_popup)
    try:
        boton.wait_for(state="visible", timeout=int(espera_s * 1000))
        boton.click(timeout=3000)
        log.info("Cartel emergente cerrado.")
    except Exception:
        page.keyboard.press("Escape")  # por si el cartel se cierra con Esc; si no hay cartel no pasa nada
        log.info("No se detectó cartel emergente (o se cerró con Esc).")


def _ir_a_bonos(page, s: Settings) -> None:
    if s.web_bonos_url:
        log.info("Navegando a %s ...", s.web_bonos_url)
        page.goto(s.web_bonos_url, wait_until="domcontentloaded")
    elif s.web_bonos_text:
        log.info("Navegando a la sección '%s' ...", s.web_bonos_text)
        page.get_by_text(s.web_bonos_text, exact=False).first.click()
    else:
        log.info("Abriendo la pestaña de bonos (%s) ...", s.selector_bonos)
        _visible(page, s.selector_bonos).click()
    if s.selector_listo:  # esperar a que la tabla tenga datos; si tarda, se sigue igual
        try:
            _visible(page, s.selector_listo).wait_for(state="visible", timeout=min(s.timeout_ms, 10_000))
        except Exception:
            log.warning("No vi filas en la tabla (%s); intento descargar igual.", s.selector_listo)


def _descargar(page, s: Settings, carpeta: Path) -> Path:
    boton = _visible(page, s.selector_descarga)
    boton.wait_for(state="visible")
    with page.expect_download(timeout=s.timeout_ms) as info:
        boton.click()
    descarga = info.value
    nombre = descarga.suggested_filename or ""
    if not nombre.lower().endswith((".xlsx", ".xlsm")):
        raise ScraperError(f"La descarga no es un Excel (.xlsx): '{nombre}'. Revisá WEB_DOWNLOAD_SELECTOR.")
    destino = carpeta / f"rueda_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    descarga.save_as(destino)
    log.info("Excel descargado: %s (%d KB)", destino.name, destino.stat().st_size // 1024)
    return destino


def _guardar_debug(page, carpeta: Path) -> None:
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        page.screenshot(path=str(carpeta / f"debug_{sello}.png"), full_page=True)
        (carpeta / f"debug_{sello}.html").write_text(page.content(), encoding="utf-8")
        log.error("Guardé captura y HTML de depuración en %s (debug_%s.*).", carpeta, sello)
    except Exception:  # la depuración nunca debe tapar el error original
        log.error("No pude guardar los archivos de depuración.")


def limpiar_antiguos(carpeta: Path, conservar: int = 5) -> None:
    """Deja solo los `conservar` Excel más recientes en la carpeta de datos."""
    archivos = sorted(carpeta.glob("rueda_*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    for viejo in archivos[conservar:]:
        viejo.unlink(missing_ok=True)


def descargar_excel(settings: Settings, carpeta: Optional[Path] = None, headless: bool = True) -> Path:
    """Inicia sesión, navega a bonos de tasa fija y descarga el .xlsx en ./data/. Devuelve su ruta."""
    from playwright.sync_api import sync_playwright  # import perezoso: el resto del pipeline no lo necesita

    carpeta = Path(carpeta or config.DATA_DIR)
    carpeta.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(accept_downloads=True, viewport={"width": 1366, "height": 900})
        page = context.new_page()
        page.set_default_timeout(settings.timeout_ms)
        try:
            _login(page, settings)
            _cerrar_popup(page, settings)
            _ir_a_bonos(page, settings)
            ruta = _descargar(page, settings, carpeta)
        except ScraperError:
            _guardar_debug(page, carpeta)
            raise
        except Exception as e:
            _guardar_debug(page, carpeta)
            msg = config.ocultar_secretos(f"{type(e).__name__}: {e}", settings)
            raise ScraperError(f"Falló la descarga web ({msg.splitlines()[0]}). Revisá los selectores en .env.") from None
        finally:
            context.close()
            browser.close()

    limpiar_antiguos(carpeta)
    return ruta

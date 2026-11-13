"""Configuración global y lectura segura de variables de entorno.

- Los secretos se leen SOLO con os.getenv() (vía python-dotenv en local, GitHub Secrets en la nube).
- Si falta alguna variable requerida, `cargar_settings()` falla de forma explícita listando TODAS las
  que faltan, en vez de romper más adelante con un error confuso.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

load_dotenv(BASE_DIR / ".env")  # en GitHub Actions no existe .env: se usan las variables del runner

log = logging.getLogger(__name__)

# Comisiones de compra (sobre el precio base)
COMISION_LECAP = 0.00101    # 0.101%  -> tickers que empiezan con "S"
COMISION_OTROS = 0.005025   # 0.5025% -> BONCAPs y el resto

VARIABLES_REQUERIDAS = {
    "WEB_URL": "URL de la plataforma web",
    "WEB_USER": "usuario de la plataforma web",
    "WEB_PASS": "contraseña de la plataforma web",
    "TELEGRAM_BOT_TOKEN": "token del bot de Telegram (BotFather)",
    "TELEGRAM_CHAT_ID": "ID del grupo/chat de Telegram",
    "GEMINI_API_KEY": "API key de Gemini (Google AI Studio)",
    "GOOGLE_SHEETS_CREDENTIALS_JSON": "JSON de la cuenta de servicio (contenido completo o ruta al archivo)",
    "GOOGLE_SHEET_ID": "ID de la planilla de Google Sheets (texto entre /d/ y /edit en la URL)",
}

# Selectores por defecto: los de https://mesaifa.netlify.app/ (página de una sola pantalla; la URL no cambia).
# Se pueden pisar por variable de entorno si la web cambia.
SELECTOR_USER = "#ag-email"
SELECTOR_PASS = "#ag-pass"                      # OJO: en esa web es type="text", no "password"
SELECTOR_SUBMIT = 'button[onclick*="cpLogin"]'  # si no existe, se envía el formulario con Enter
SELECTOR_BONOS = "#tab-tasafija"                # pestaña "Tasa Fija"
SELECTOR_LISTO = "#p-tasafija tbody tr"         # filas de la tabla ya cargadas (espera no fatal)
SELECTOR_DESCARGA = 'button.xbtn[onclick*="TASAFIJA"]'  # "Exportar Excel" de la pestaña Tasa Fija
SELECTOR_POPUP = (                              # cierre del cartel emergente inicial (heurístico)
    '[class*="modal" i] [class*="close" i], [class*="popup" i] [class*="close" i], '
    '[class*="overlay" i] [class*="close" i], button[aria-label*="errar" i], '
    'button:has-text("Cerrar"), button:has-text("Entendido"), button:has-text("Aceptar"), '
    'button:has-text("×"), button:has-text("✕")'
)

# Selectores genéricos para una web típica (formulario clásico con enlaces): útiles como punto de partida.
SELECTORES_GENERICOS = {
    "WEB_USER_SELECTOR": (
        'input[type="email"], input[name*="user" i], input[id*="user" i], input[name*="usuario" i], '
        'input[id*="usuario" i], input[name*="mail" i], input[name*="login" i], input[name*="dni" i], '
        'input[type="text"], input:not([type])'
    ),
    "WEB_PASS_SELECTOR": 'input[type="password"]',
    "WEB_SUBMIT_SELECTOR": (
        'button[type="submit"], input[type="submit"], button:has-text("Ingresar"), '
        'button:has-text("Iniciar"), button:has-text("Login")'
    ),
    "WEB_DOWNLOAD_SELECTOR": (
        'a:has-text("Excel"), button:has-text("Excel"), a:has-text("Descargar"), '
        'button:has-text("Descargar"), a[href$=".xlsx"]'
    ),
}


class ConfigError(RuntimeError):
    """Falta configuración obligatoria."""


@dataclass(frozen=True)
class Settings:
    # --- obligatorias ---
    web_url: str
    web_user: str
    web_pass: str
    telegram_bot_token: str
    telegram_chat_id: str
    gemini_api_key: str
    google_credentials: str
    google_sheet_id: str
    # --- opcionales ---
    gemini_model: str = "gemini-3.8-flash"
    worksheet_name: str = "Bonos"
    web_login_url: str = ""
    web_bonos_url: str = ""
    web_bonos_text: str = ""
    selector_user: str = SELECTOR_USER
    selector_pass: str = SELECTOR_PASS
    selector_submit: str = SELECTOR_SUBMIT
    selector_descarga: str = SELECTOR_DESCARGA
    selector_bonos: str = SELECTOR_BONOS
    selector_listo: str = SELECTOR_LISTO
    selector_popup: str = SELECTOR_POPUP
    timeout_ms: int = 60_000
    precio_modo: str = "auto"
    comision_lecap: float = COMISION_LECAP
    comision_otros: float = COMISION_OTROS
    familias: tuple = ("LECAPS", "BONCAPS")  # vacío = no filtrar por familia

    def secretos(self) -> list:
        return [s for s in (self.web_pass, self.telegram_bot_token, self.gemini_api_key, self.google_credentials) if s]


def _opt(env: Mapping[str, str], nombre: str, defecto: str) -> str:
    return (env.get(nombre) or "").strip() or defecto


def _opt_float(env: Mapping[str, str], nombre: str, defecto: float) -> float:
    try:
        return float(_opt(env, nombre, str(defecto)))
    except ValueError:
        raise ConfigError(f"La variable {nombre} debe ser un número (valor actual: {env.get(nombre)!r}).")


def cargar_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    """Lee y valida la configuración. Lanza ConfigError con la lista completa de faltantes."""
    env = os.environ if env is None else env
    faltan = [f"  - {k}: {desc}" for k, desc in VARIABLES_REQUERIDAS.items() if not (env.get(k) or "").strip()]
    if faltan:
        msg = "Faltan variables de entorno obligatorias:\n" + "\n".join(faltan)
        msg += "\nCompletalas en el archivo .env (local) o en GitHub → Settings → Secrets and variables → Actions."
        raise ConfigError(msg)

    precio_modo = _opt(env, "PRECIO_MODO", "auto").lower()
    if precio_modo not in ("auto", "texto", "numero"):
        raise ConfigError("PRECIO_MODO debe ser 'auto', 'texto' o 'numero'.")

    return Settings(
        web_url=env["WEB_URL"].strip(),
        web_user=env["WEB_USER"].strip(),
        web_pass=env["WEB_PASS"],  # sin strip: una contraseña puede tener espacios
        telegram_bot_token=env["TELEGRAM_BOT_TOKEN"].strip(),
        telegram_chat_id=env["TELEGRAM_CHAT_ID"].strip(),
        gemini_api_key=env["GEMINI_API_KEY"].strip(),
        google_credentials=env["GOOGLE_SHEETS_CREDENTIALS_JSON"].strip(),
        google_sheet_id=env["GOOGLE_SHEET_ID"].strip(),
        gemini_model=_opt(env, "GEMINI_MODEL", "gemini-3.8-flash"),
        worksheet_name=_opt(env, "WORKSHEET_NAME", "Bonos"),
        web_login_url=_opt(env, "WEB_LOGIN_URL", ""),
        web_bonos_url=_opt(env, "WEB_BONOS_URL", ""),
        web_bonos_text=_opt(env, "WEB_BONOS_TEXT", ""),
        selector_user=_opt(env, "WEB_USER_SELECTOR", SELECTOR_USER),
        selector_pass=_opt(env, "WEB_PASS_SELECTOR", SELECTOR_PASS),
        selector_submit=_opt(env, "WEB_SUBMIT_SELECTOR", SELECTOR_SUBMIT),
        selector_descarga=_opt(env, "WEB_DOWNLOAD_SELECTOR", SELECTOR_DESCARGA),
        selector_bonos=_opt(env, "WEB_BONOS_SELECTOR", SELECTOR_BONOS),
        selector_listo=_opt(env, "WEB_READY_SELECTOR", SELECTOR_LISTO),
        selector_popup=_opt(env, "WEB_POPUP_SELECTOR", SELECTOR_POPUP),
        timeout_ms=int(_opt_float(env, "WEB_TIMEOUT_S", 60) * 1000),
        precio_modo=precio_modo,
        comision_lecap=_opt_float(env, "COMISION_LECAP", COMISION_LECAP),
        comision_otros=_opt_float(env, "COMISION_OTROS", COMISION_OTROS),
        familias=tuple(f.strip().upper() for f in _opt(env, "FAMILIAS", "LECAPS,BONCAPS").split(",") if f.strip()),
    )


def ocultar_secretos(texto: str, settings: Optional[Settings]) -> str:
    """Reemplaza cualquier secreto presente en un texto (p. ej. un mensaje de error) por ***."""
    if settings is None:
        return texto
    for s in settings.secretos():
        texto = texto.replace(s, "***")
    return texto

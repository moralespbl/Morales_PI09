from pathlib import Path

import pandas as pd
import pytest

import config

ENV_COMPLETO = {
    "WEB_URL": "http://localhost:0",
    "WEB_USER": "usuario",
    "WEB_PASS": "clave-secreta",
    "TELEGRAM_BOT_TOKEN": "123:token-falso",
    "TELEGRAM_CHAT_ID": "-100",
    "GEMINI_API_KEY": "gem-key-falsa",
    "GOOGLE_SHEETS_CREDENTIALS_JSON": '{"type": "service_account"}',
    "GOOGLE_SHEET_ID": "sheet-id-falso",
}


@pytest.fixture
def settings():
    return config.cargar_settings(ENV_COMPLETO)


FILAS_EJEMPLO = [
    # Ticker, Días, Precio, Vencimiento, Valor Final
    ["S30N6", "45d", "$ 1.234,56", "18/12/26", "1.300,00"],
    ["T15E7", " 120 D", "10.500", "2027-03-15", 1.4e2 * 10],
    ["S15D6", "10 d", "9.876", pd.Timestamp("2026-11-21"), "1000"],
    ["", "5d", "1", "01/01/27", "1"],                 # sin ticker -> se ignora
    ["XXXX", "abc", "1.000", "01/01/27", "1000"],     # días inválidos -> se ignora
]


def hacer_excel(path: Path, filas=None, filas_previas: int = 3) -> Path:
    """Excel como los de la plataforma: título y filas vacías antes del encabezado."""
    filas = FILAS_EJEMPLO if filas is None else filas
    previas = [["Rueda diaria - Letras y Bonos de corto plazo", None, None, None, None]] + [[None] * 5] * (filas_previas - 1)
    encabezado = [["Ticker", "Días", "Precio", "Vencimiento", "Valor Final"]]
    pd.DataFrame(previas + encabezado + filas).to_excel(path, header=False, index=False)
    return path

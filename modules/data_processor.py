"""Procesador cuantitativo: reemplazo vectorizado de la macro VBA.

Entrada : el .xlsx de la rueda diaria (LECAPs / BONCAPs).
Salida  : DataFrame limpio, con comisiones y tasas, ordenado por días al vencimiento (ascendente).

Supuestos sobre el Excel (configurables abajo en ALIAS si tus encabezados son distintos):
  - Hay una fila de encabezados (no necesariamente la primera) con Ticker, Días, Precio,
    Vencimiento y Valor Final.
  - `Precio` en texto: se aplica la regla de la macro (solo dígitos / 100, o sea 2 decimales
    implícitos). Si la celda ya es un número real, se usa tal cual (PRECIO_MODO=auto).
  - `Valor Final` es el valor al vencimiento por cada 100 nominales (mismo criterio que Precio).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

import config

log = logging.getLogger(__name__)

# Nombres canónicos -> posibles encabezados en el Excel (se comparan sin tildes, en minúscula)
ALIAS: Dict[str, set] = {
    "ticker": {"ticker", "especie", "simbolo", "instrumento", "bono", "letra"},
    "dias": {"dias", "dias a vencimiento", "dias al vencimiento", "dias vto", "dias vencimiento", "plazo", "dias a vto"},
    "precio": {"precio", "ultimo", "px", "cotizacion", "precio compra", "precio ult", "ultimo precio", "precio de compra"},
    "vencimiento": {"vencimiento", "fecha vencimiento", "fecha de vencimiento", "vto", "fecha vto", "fecha de vto", "vencim"},
    "valor_final": {"valor final", "pago final", "valor al vencimiento", "valor vto", "flujo final", "valor de rescate", "vf", "v final"},
}

# Columnas opcionales (si el Excel las trae se conservan; si no, quedan vacías)
OPCIONALES: Dict[str, set] = {
    "familia": {"familia", "tipo", "clase"},
    "var_pct": {"var", "var %", "variacion", "variacion diaria"},
}

COLUMNAS_SALIDA = [
    "Ticker", "Familia", "Precio", "Var %", "Vencimiento", "Días", "Meses", "Valor Final",
    "TEM", "TNA", "TEA", "Rend. Directo", "Comisión", "Precio Compra",
]
# Orden y columnas que se publican en Google Sheets (mismo layout que la planilla de la macro)
COLUMNAS_PUBLICADAS = COLUMNAS_SALIDA[:12]


def leyenda_tasas(comision_lecap: float = config.COMISION_LECAP, comision_otros: float = config.COMISION_OTROS) -> str:
    """Aclaración de cómo se calculan las tasas (para el mensaje de Telegram y el subtítulo de la hoja)."""
    return (
        "Tasas calculadas sobre el precio de compra: precio + comisiones del broker y derechos de mercado "
        f"({comision_lecap * 100:.4g}% LECAPs, {comision_otros * 100:.4g}% BONCAPs), operaciones excluidas de IVA. "
        "Precios al momento de la descarga."
    )


class DataError(ValueError):
    """El Excel no tiene la estructura esperada."""


def _norm(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


_ALIAS_NORM = {k: {_norm(a) for a in v} for k, v in ALIAS.items()}


# ----------------------------------------------------------------------------
# Detección de encabezados y lectura
# ----------------------------------------------------------------------------
def detectar_encabezado(df_raw: pd.DataFrame, max_filas: int = 25) -> Optional[int]:
    """Fila (0-based) con más coincidencias de encabezados conocidos; None si hay menos de 4."""
    mejor_fila, mejor_hits = None, 0
    for i in range(min(max_filas, len(df_raw))):
        celdas = {_norm(c) for c in df_raw.iloc[i].tolist() if pd.notna(c)}
        hits = sum(1 for alias in _ALIAS_NORM.values() if celdas & alias)
        if hits > mejor_hits:
            mejor_fila, mejor_hits = i, hits
    return mejor_fila if mejor_hits >= 4 else None


def _mapear_columnas(columnas) -> Dict[str, str]:
    mapa: Dict[str, str] = {}
    for col in columnas:
        n = _norm(col)
        for canon, alias in _ALIAS_NORM.items():
            if n in alias and canon not in mapa:
                mapa[canon] = col
    faltan = [k for k in ALIAS if k not in mapa]
    if faltan:
        raise DataError(
            f"No encontré las columnas {faltan} en el Excel. Encabezados detectados: {list(columnas)}. "
            "Ajustá ALIAS en modules/data_processor.py."
        )
    return mapa


def leer_excel(path: Path) -> pd.DataFrame:
    """Lee la hoja activa buscando la fila de encabezados; devuelve las columnas canónicas en crudo."""
    raw = pd.read_excel(path, header=None, dtype=object, engine="openpyxl")
    idx = detectar_encabezado(raw)
    if idx is None:
        raise DataError(
            f"No pude identificar la fila de encabezados en {Path(path).name}. "
            "Esperaba columnas tipo Ticker, Días, Precio, Vencimiento y Valor Final."
        )
    df = raw.iloc[idx + 1 :].copy()
    df.columns = raw.iloc[idx].tolist()
    mapa = _mapear_columnas(df.columns)
    for canon, alias in ((k, {_norm(a) for a in v}) for k, v in OPCIONALES.items()):
        for col in df.columns:
            if _norm(col) in alias and canon not in mapa:
                mapa[canon] = col
    usar = [k for k in [*ALIAS, *OPCIONALES] if k in mapa]
    out = df[[mapa[k] for k in usar]].copy()
    out.columns = usar
    for k in OPCIONALES:
        if k not in out:
            out[k] = ""
    return out.reset_index(drop=True)


# ----------------------------------------------------------------------------
# Limpieza (vectorizada)
# ----------------------------------------------------------------------------
def _es_numerico(s: pd.Series) -> pd.Series:
    return s.map(lambda v: isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool))


def _txt(s: pd.Series) -> pd.Series:
    """Texto seguro: las celdas vacías/NaN pasan a '' (según la versión de pandas, astype(str) las deja como NaN)."""
    return s.astype("string").fillna("").astype(object)


def limpiar_dias(s: pd.Series) -> pd.Series:
    """'45d', ' 120 D' -> 45, 120 (Int64; <NA> si no se puede)."""
    es_num = _es_numerico(s)
    num = pd.to_numeric(s.where(es_num), errors="coerce")
    txt = _txt(s.where(~es_num)).str.replace(r"[^\d\-]", "", regex=True).replace({"": np.nan})
    return num.fillna(pd.to_numeric(txt, errors="coerce")).round().astype("Int64")


def limpiar_precio(s: pd.Series, modo: str = "auto") -> pd.Series:
    """Regla de la macro: CDbl(soloDigitos) / 100.

    auto  : celdas numéricas se respetan; las de texto se limpian (solo dígitos) y se dividen por 100.
    texto : todo se trata como texto (solo dígitos / 100).
    numero: todo se interpreta como número (formato argentino o internacional), sin dividir.
    """
    if modo == "numero":
        return parsear_numero(s)
    es_num = _es_numerico(s) if modo == "auto" else pd.Series(False, index=s.index)
    num = pd.to_numeric(s.where(es_num), errors="coerce")
    digitos = _txt(s.where(~es_num)).str.replace(r"\D", "", regex=True).replace({"": np.nan})
    return num.fillna(pd.to_numeric(digitos, errors="coerce") / 100.0)


def parsear_numero(s: pd.Series) -> pd.Series:
    """Número en formato argentino ('1.234,56'), internacional ('1,234.56') o simple, a float."""
    es_num = _es_numerico(s)
    num = pd.to_numeric(s.where(es_num), errors="coerce")
    t = _txt(s.where(~es_num)).str.strip().str.replace(r"[^\d.,\-]", "", regex=True)
    punto = t.str.contains(".", regex=False).to_numpy(dtype=bool)
    coma = t.str.contains(",", regex=False).to_numpy(dtype=bool)
    ultimo_es_coma = (t.str.rfind(",") > t.str.rfind(".")).to_numpy(dtype=bool)
    miles_punto = t.str.match(r"^-?\d{1,3}(\.\d{3})+$").to_numpy(dtype=bool)
    ambos_ar = t.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)   # 1.234,56
    ambos_us = t.str.replace(",", "", regex=False)                                       # 1,234.56
    resultado = np.select(
        [punto & coma & ultimo_es_coma, punto & coma, coma, punto & miles_punto],
        [
            ambos_ar.to_numpy(dtype=object),
            ambos_us.to_numpy(dtype=object),
            t.str.replace(",", ".", regex=False).to_numpy(dtype=object),
            t.str.replace(".", "", regex=False).to_numpy(dtype=object),
        ],
        default=t.to_numpy(dtype=object),
    )
    parsed = pd.to_numeric(pd.Series(resultado, index=s.index).replace({"": np.nan}), errors="coerce")
    return num.fillna(parsed)


def limpiar_vencimiento(s: pd.Series) -> pd.Series:
    """Fechas de Excel (datetime, serial o texto dd/mm/aa) -> datetime64."""
    es_num = _es_numerico(s)
    serial = pd.to_datetime(pd.to_numeric(s.where(es_num), errors="coerce"), unit="D", origin="1899-12-30", errors="coerce")
    resto = pd.to_datetime(s.where(~es_num), errors="coerce", dayfirst=True, format="mixed")
    return resto.fillna(serial)


def formatear_vencimiento(s: pd.Series) -> pd.Series:
    """datetime -> texto DD/MM/YY."""
    return s.dt.strftime("%d/%m/%y")


# ----------------------------------------------------------------------------
# Cálculo
# ----------------------------------------------------------------------------
def procesar_dataframe(
    df_raw: pd.DataFrame,
    comision_lecap: float = config.COMISION_LECAP,
    comision_otros: float = config.COMISION_OTROS,
    precio_modo: str = "auto",
    familias: tuple = (),
) -> pd.DataFrame:
    """Limpia, calcula comisiones y tasas, filtra filas inválidas y ordena por días (ascendente)."""
    ticker = _txt(df_raw["ticker"]).str.strip().str.upper()
    dias = limpiar_dias(df_raw["dias"]).astype("float64")
    precio = limpiar_precio(df_raw["precio"], precio_modo).astype("float64")
    valor_final = parsear_numero(df_raw["valor_final"]).astype("float64")
    vencimiento = limpiar_vencimiento(df_raw["vencimiento"])

    # Comisión diferencial: tickers que empiezan con "S" (LECAP) vs. resto (BONCAP, etc.)
    comision = np.where(ticker.str.startswith("S"), comision_lecap, comision_otros)
    precio_compra = precio * (1.0 + comision)

    dias_ok = dias.where(dias > 0)
    ratio = valor_final / precio_compra
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        rend = ratio - 1.0
        tea = ratio ** (365.0 / dias_ok) - 1.0
        tem = ratio ** (30.0 / dias_ok) - 1.0
    tna = tem * 12.0

    out = pd.DataFrame(
        {
            "Ticker": ticker,
            "Familia": _txt(df_raw["familia"]).str.strip().str.upper() if "familia" in df_raw else "",
            "Precio": precio,
            "Var %": _txt(df_raw["var_pct"]).str.strip() if "var_pct" in df_raw else "",
            "Vencimiento": vencimiento,
            "Días": dias,
            "Meses": (dias / 30.0).round(1),
            "Valor Final": valor_final,
            "TEM": tem,
            "TNA": tna,
            "TEA": tea,
            "Rend. Directo": rend,
            "Comisión": comision,
            "Precio Compra": precio_compra,
        }
    )

    validas = (
        ticker.ne("") & ticker.ne("NAN") & ticker.ne("NONE")
        & dias.gt(0) & precio.gt(0) & valor_final.gt(0) & vencimiento.notna()
        & np.isfinite(out[["Rend. Directo", "TEA", "TEM", "TNA"]]).all(axis=1)
    )
    if familias and out["Familia"].ne("").any():  # solo si el Excel trae la columna Familia
        validas &= out["Familia"].isin([f.upper() for f in familias])
    descartadas = int((~validas).sum())
    if descartadas:
        log.info("Se ignoraron %d filas inválidas o vacías de %d.", descartadas, len(out))
    out = out[validas].sort_values("Días", kind="mergesort").reset_index(drop=True)
    out["Días"] = out["Días"].astype(int)
    return out[COLUMNAS_SALIDA]


def procesar_excel(path: Path, **kwargs) -> pd.DataFrame:
    """Lee el Excel de la rueda y devuelve la tabla procesada."""
    df = procesar_dataframe(leer_excel(path), **kwargs)
    if df.empty:
        raise DataError(f"{Path(path).name}: después de limpiar no quedó ninguna fila válida.")
    log.info("Excel procesado: %d instrumentos (días %d a %d).", len(df), df["Días"].min(), df["Días"].max())
    return df


def buscar_ultimo_excel(carpeta: Path = config.DATA_DIR) -> Path:
    archivos = sorted(Path(carpeta).glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not archivos:
        raise FileNotFoundError(f"No hay archivos .xlsx en {carpeta}.")
    return archivos[0]

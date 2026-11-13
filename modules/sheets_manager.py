"""Carga y formato profesional de la tabla en Google Sheets (gspread + cuenta de servicio)."""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

from config import Settings
from modules.data_processor import COLUMNAS_PUBLICADAS, leyenda_tasas

log = logging.getLogger(__name__)

COLOR_ENCABEZADO = {"red": 0.09, "green": 0.22, "blue": 0.42}  # azul institucional
BLANCO = {"red": 1.0, "green": 1.0, "blue": 1.0}

FORMATOS_COLUMNA: Dict[str, Dict[str, Any]] = {
    "Vencimiento": {"numberFormat": {"type": "DATE", "pattern": "dd/mm/yy"}, "horizontalAlignment": "CENTER"},
    "Días": {"numberFormat": {"type": "NUMBER", "pattern": "0"}, "horizontalAlignment": "CENTER"},
    "Precio": {"numberFormat": {"type": "CURRENCY", "pattern": '"$"#,##0.00'}},
    "Precio Compra": {"numberFormat": {"type": "CURRENCY", "pattern": '"$"#,##0.00'}},
    "Valor Final": {"numberFormat": {"type": "CURRENCY", "pattern": '"$"#,##0.00'}},
    "Comisión": {"numberFormat": {"type": "PERCENT", "pattern": "0.000%"}},
    "Rend. Directo": {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}},
    "TEA": {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}},
    "TEM": {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}},
    "TNA": {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}},
}


def cargar_credenciales(valor: str) -> Dict[str, Any]:
    """Acepta el JSON completo de la cuenta de servicio (ideal para GitHub Secrets) o la ruta a un archivo."""
    valor = valor.strip()
    if valor.startswith("{"):
        try:
            return json.loads(valor)
        except json.JSONDecodeError as e:
            raise ValueError(
                "GOOGLE_SHEETS_CREDENTIALS_JSON empieza con '{' pero no es un JSON válido. "
                "Pegá el contenido completo del archivo de credenciales."
            ) from e
    ruta = Path(valor)
    if not ruta.is_file():
        raise ValueError(f"GOOGLE_SHEETS_CREDENTIALS_JSON no es un JSON ni una ruta existente ({ruta.name}).")
    try:
        return json.loads(ruta.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise ValueError(f"El archivo de credenciales {ruta.name} está vacío o no es un JSON válido.") from e


def _serial_fecha(ts: pd.Timestamp) -> float:
    """Fecha -> número de serie de Sheets/Excel (independiente del idioma de la planilla)."""
    return float((ts.normalize() - pd.Timestamp("1899-12-30")).days)


def _celda(valor: Any) -> Any:
    if valor is None or (isinstance(valor, float) and (math.isnan(valor) or math.isinf(valor))) or valor is pd.NaT:
        return ""
    if isinstance(valor, pd.Timestamp):
        return _serial_fecha(valor)
    if hasattr(valor, "item"):
        return valor.item()
    return valor


def preparar_matriz(df: pd.DataFrame) -> List[List[Any]]:
    """Encabezados + filas con números como números y fechas como número de serie."""
    filas = [[_celda(v) for v in fila] for fila in df.itertuples(index=False, name=None)]
    return [list(map(str, df.columns))] + filas


def _letra(n: int) -> str:
    """1 -> A, 27 -> AA."""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


FILA_ENCABEZADO = 4  # 1: título, 2: subtítulo, 3: vacía, 4: encabezados (igual que la macro)
AZUL = {"red": 31 / 255, "green": 78 / 255, "blue": 121 / 255}
CELESTE = {"red": 238 / 255, "green": 243 / 255, "blue": 250 / 255}
GRIS = {"red": 89 / 255, "green": 89 / 255, "blue": 89 / 255}
VERDE = {"red": 0.0, "green": 128 / 255, "blue": 0.0}
COLUMNAS_TASAS = ("TEM", "TNA", "TEA", "Rend. Directo")
ENCABEZADOS_VISTOS = {"Vencimiento": "Vencim.", "Valor Final": "V. Final", "Rend. Directo": "Rend. Dir."}
FORMATOS_COLUMNA["Precio"] = {"numberFormat": {"type": "CURRENCY", "pattern": '"$"#,##0.00'}}
FORMATOS_COLUMNA["Meses"] = {"numberFormat": {"type": "NUMBER", "pattern": "0.0"}}


def construir_formatos(df: pd.DataFrame) -> List[Tuple[str, Dict[str, Any]]]:
    """Lista de (rango A1, formato): título, subtítulo, encabezado azul, cuerpo centrado, tasas en verde."""
    n_cols, n_filas = len(df.columns), len(df)
    h = FILA_ENCABEZADO
    ultima = _letra(n_cols)
    formatos: List[Tuple[str, Dict[str, Any]]] = [
        ("A1", {"textFormat": {"fontFamily": "Calibri", "fontSize": 15, "bold": True, "foregroundColor": AZUL}}),
        ("A2", {"textFormat": {"fontFamily": "Calibri", "fontSize": 11, "italic": True, "foregroundColor": GRIS}}),
        (
            f"A{h}:{ultima}{h}",
            {
                "backgroundColor": AZUL,
                "textFormat": {"fontFamily": "Calibri", "fontSize": 10, "foregroundColor": BLANCO, "bold": True},
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
            },
        ),
    ]
    if not n_filas:
        return formatos
    a, z = h + 1, h + n_filas
    formatos.append(
        (
            f"A{a}:{ultima}{z}",
            {
                "textFormat": {"fontFamily": "Calibri", "fontSize": 10},
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
                "borders": {k: {"style": "SOLID", "color": {"red": 0.85, "green": 0.85, "blue": 0.85}} for k in ("top", "bottom", "left", "right")},
            },
        )
    )
    for i, col in enumerate(df.columns, start=1):
        letra = _letra(i)
        fmt = dict(FORMATOS_COLUMNA.get(col, {}))
        if col == "Ticker":
            fmt["textFormat"] = {"fontFamily": "Calibri", "fontSize": 10, "bold": True, "foregroundColor": AZUL}
        elif col in COLUMNAS_TASAS:
            fmt["textFormat"] = {"fontFamily": "Calibri", "fontSize": 10, "foregroundColor": VERDE}
        if fmt:
            formatos.append((f"{letra}{a}:{letra}{z}", fmt))
    return formatos


def _franjas(n_cols: int, n_filas: int) -> List[Dict[str, Any]]:
    """Filas pares en celeste suave (cebra) mediante una regla de formato condicional."""
    return [
        {
            "ranges": [{"sheetId": None, "startRowIndex": FILA_ENCABEZADO, "endRowIndex": FILA_ENCABEZADO + n_filas,
                        "startColumnIndex": 0, "endColumnIndex": n_cols}],
            "booleanRule": {
                "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=ISEVEN(ROW())"}]},
                "format": {"backgroundColor": CELESTE},
            },
        }
    ]


def publicar(df: pd.DataFrame, settings: Settings) -> None:
    """Reemplaza el contenido de la hoja con la tabla procesada y le aplica el formato de la macro original."""
    import gspread  # import perezoso

    cols = [c for c in COLUMNAS_PUBLICADAS if c in df.columns]
    df = df[cols]
    cliente = gspread.service_account_from_dict(cargar_credenciales(settings.google_credentials))
    libro = cliente.open_by_key(settings.google_sheet_id)
    try:
        hoja = libro.worksheet(settings.worksheet_name)
    except gspread.exceptions.WorksheetNotFound:
        hoja = libro.add_worksheet(title=settings.worksheet_name, rows="50", cols=str(len(cols) + 2))

    matriz = preparar_matriz(df)
    matriz[0] = [ENCABEZADOS_VISTOS.get(c, c) for c in matriz[0]]
    hoja.clear()
    try:  # limpia formatos/reglas de corridas anteriores
        hoja.clear_basic_filter()
        libro.batch_update({"requests": [{"deleteConditionalFormatRule": {"sheetId": hoja.id, "index": 0}}] * 20})
    except Exception:  # noqa: BLE001 - si no hay reglas previas la API responde error: es esperado
        pass
    hoja.resize(rows=max(len(matriz) + FILA_ENCABEZADO + 5, 20), cols=len(cols) + 2)
    hoja.update(values=matriz, range_name=f"A{FILA_ENCABEZADO}")

    hoja.format(f"A{FILA_ENCABEZADO}:{_letra(len(cols))}{FILA_ENCABEZADO + len(df)}", {"backgroundColor": BLANCO})
    for rango, formato in construir_formatos(df):
        hoja.format(rango, formato)

    reglas = _franjas(len(cols), len(df))
    reglas[0]["ranges"][0]["sheetId"] = hoja.id
    libro.batch_update(
        {
            "requests": [
                {"addConditionalFormatRule": {"rule": reglas[0], "index": 0}},
                {"updateSheetProperties": {"properties": {"sheetId": hoja.id, "gridProperties": {"hideGridlines": True, "frozenRowCount": FILA_ENCABEZADO}},
                                           "fields": "gridProperties.hideGridlines,gridProperties.frozenRowCount"}},
                {"updateDimensionProperties": {"range": {"sheetId": hoja.id, "dimension": "ROWS", "startIndex": FILA_ENCABEZADO - 1, "endIndex": FILA_ENCABEZADO},
                                               "properties": {"pixelSize": 26}, "fields": "pixelSize"}},
                {"updateDimensionProperties": {"range": {"sheetId": hoja.id, "dimension": "ROWS", "startIndex": FILA_ENCABEZADO, "endIndex": FILA_ENCABEZADO + len(df)},
                                               "properties": {"pixelSize": 23}, "fields": "pixelSize"}},
                {"autoResizeDimensions": {"dimensions": {"sheetId": hoja.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": len(cols)}}},
            ]
        }
    )
    # Título y subtítulo al final: así no influyen en el ancho de la columna Ticker (se desbordan hacia la derecha)
    hoja.update(
        values=[["Simulador de Inversiones a Corto Plazo en Pesos"],
                [f"Al {datetime.now():%d-%m-%y}. " + leyenda_tasas(settings.comision_lecap, settings.comision_otros)]],
        range_name="A1",
    )
    log.info("Hoja '%s' actualizada: %d filas.", settings.worksheet_name, len(df))

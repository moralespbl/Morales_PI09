"""Asesor de IA: reduce el contexto (tokens) y consulta a Gemini (google-genai).

Nunca se envía el DataFrame completo: solo las 5 mejores oportunidades de la curva (la de mayor TEM
de cada tramo de días) y 1 línea de estadísticas. Si Gemini está saturado (503) se reintenta con espera;
si igual falla, `generar_recomendacion` devuelve None y el pipeline sigue sin comentario de IA.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import pandas as pd

from config import Settings

log = logging.getLogger(__name__)

# Tramos de días a vencimiento (inclusive) para elegir la mejor oportunidad de cada uno
TRAMOS_DIAS = [(0, 30), (31, 60), (61, 120), (121, 240), (241, 100_000)]
CODIGOS_TRANSITORIOS = {500, 502, 503, 504}
TIMEOUT_GEMINI_S = 30  # máximo de espera por llamada: si Gemini no responde, cuenta como fallo y el pipeline sigue


def seleccionar_oportunidades(df: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    """Las `n` mejores por TEM: una por tramo de días y, si faltan, se completa con las siguientes mejores."""
    elegidas = []
    for lo, hi in TRAMOS_DIAS:
        tramo = df[(df["Días"] >= lo) & (df["Días"] <= hi)]
        if not tramo.empty:
            elegidas.append(tramo["TEM"].idxmax())
    top = df.loc[elegidas].sort_values("TEM", ascending=False).head(n)
    if len(top) < n:
        resto = df.drop(index=top.index).sort_values("TEM", ascending=False).head(n - len(top))
        top = pd.concat([top, resto])
    return top.sort_values("Días")


def resumen_compacto(df: pd.DataFrame, top: pd.DataFrame) -> str:
    """Texto mínimo para el LLM: 1 línea de curva + 1 línea por oportunidad."""
    tna = df["TNA"] * 100
    lineas = [
        f"Curva: {len(df)} instrumentos, días {int(df['Días'].min())}-{int(df['Días'].max())}, "
        f"TNA mín/mediana/máx: {tna.min():.1f}/{tna.median():.1f}/{tna.max():.1f}%"
    ]
    lineas.append("Ticker|Días|PxCompra|TEM%|TNA%|TEA%")
    for _, r in top.iterrows():
        lineas.append(
            f"{r['Ticker']}|{int(r['Días'])}|{r['Precio Compra']:.2f}|{r['TEM'] * 100:.2f}|{r['TNA'] * 100:.1f}|{r['TEA'] * 100:.1f}"
        )
    return "\n".join(lineas)


def construir_prompt(resumen: str) -> str:
    return (
        "Sos un analista de renta fija argentina. Con estos datos de LECAPs/BONCAPs (precios con comisión incluida) "
        "escribí un análisis técnico-ejecutivo BREVE (máximo 900 caracteres) en español, con 3 sugerencias de "
        "inversión de corto plazo y por qué. Usá solo los números del dato, sin inventar. Sin tablas ni LaTeX; "
        "viñetas con '-'. Cerrá aclarando que no es asesoramiento financiero.\n\n" + resumen
    )


def _llamar_gemini(prompt: str, api_key: str, modelo: str) -> str:
    from google import genai  # import perezoso
    from google.genai import types

    cliente = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_GEMINI_S * 1000))
    respuesta = cliente.models.generate_content(model=modelo, contents=prompt)
    return respuesta.text or ""


def _es_transitorio(exc: Exception) -> bool:
    codigo = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(codigo, int):
        return codigo in CODIGOS_TRANSITORIOS
    # timeouts de red (p. ej. httpx.ReadTimeout) y cortes de conexión se tratan como transitorios
    return isinstance(exc, (TimeoutError, ConnectionError)) or "timeout" in type(exc).__name__.lower()


def consultar_gemini(
    prompt: str,
    settings: Settings,
    intentos: int = 3,
    espera_s: float = 5.0,
    llamar: Callable[[str, str, str], str] = _llamar_gemini,
) -> Optional[str]:
    """Consulta con reintentos ante errores transitorios (503 'alta demanda'). None si no se logra."""
    for i in range(1, intentos + 1):
        try:
            texto = llamar(prompt, settings.gemini_api_key, settings.gemini_model).strip()
            if texto:
                return texto
            log.warning("Gemini devolvió una respuesta vacía.")
            return None
        except Exception as e:  # noqa: BLE001 - cualquier error del SDK se maneja igual
            transitorio = _es_transitorio(e)
            detalle = f"{type(e).__name__} {getattr(e, 'code', '')}".strip()
            if transitorio and i < intentos:
                log.warning("Gemini no disponible (%s). Reintento %d/%d en %.0fs.", detalle, i, intentos - 1, espera_s * 2 ** (i - 1))
                time.sleep(espera_s * 2 ** (i - 1))
                continue
            log.error("Gemini falló (%s): %s", detalle, str(e)[:200].replace(settings.gemini_api_key, "***"))
            return None
    return None


def generar_recomendacion(df: pd.DataFrame, settings: Settings) -> Optional[str]:
    """Pipeline completo del asesor: selección -> resumen mínimo -> Gemini. None si la IA no responde."""
    top = seleccionar_oportunidades(df)
    prompt = construir_prompt(resumen_compacto(df, top))
    log.info("Consultando a Gemini (%s) con un contexto de %d caracteres...", settings.gemini_model, len(prompt))
    return consultar_gemini(prompt, settings)

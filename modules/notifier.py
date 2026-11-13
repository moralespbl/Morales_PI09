"""Notificador de Telegram (Bot API vía requests): resúmenes y alertas."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import List, Optional

import pandas as pd
import requests

import config
from config import Settings
from modules import data_processor

log = logging.getLogger(__name__)

LIMITE = 4000  # Telegram admite 4096 caracteres por mensaje


def limpiar_markdown(texto: str) -> str:
    """Texto plano apto para Telegram sin parse_mode (evita rechazos por caracteres especiales)."""
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", texto)
    t = re.sub(r"^\s*#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[\*\-]\s+", "• ", t, flags=re.M)
    return t.replace("`", "")


def dividir_mensaje(texto: str, max_len: int = LIMITE) -> List[str]:
    """Parte un texto largo por líneas sin superar max_len (corte duro si una sola línea lo excede)."""
    if len(texto) <= max_len:
        return [texto]
    partes, actual = [], ""
    for linea in texto.split("\n"):
        trozos = [linea[i : i + max_len] for i in range(0, max(len(linea), 1), max_len)]
        for trozo in trozos:
            candidato = f"{actual}\n{trozo}" if actual else trozo
            if len(candidato) > max_len:
                partes.append(actual)
                actual = trozo
            else:
                actual = candidato
    if actual:
        partes.append(actual)
    return partes


def enviar_mensaje(settings: Settings, texto: str) -> bool:
    """Envía un texto (dividido si es largo). Devuelve True si todas las partes salieron."""
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    partes = dividir_mensaje(texto)
    ok = True
    for i, parte in enumerate(partes, 1):
        payload = {"chat_id": settings.telegram_chat_id, "text": parte, "disable_web_page_preview": True}
        try:
            r = requests.post(url, json=payload, timeout=15)
            if r.status_code == 429:  # límite de envío: respetar retry_after
                try:
                    espera = float(r.json().get("parameters", {}).get("retry_after", 3))
                except (ValueError, AttributeError):
                    espera = 3.0
                time.sleep(min(espera, 30))
                r = requests.post(url, json=payload, timeout=15)
            if r.status_code == 200:
                log.info("Telegram: parte %d/%d enviada.", i, len(partes))
            else:
                log.error("Telegram: parte %d falló (HTTP %s): %s", i, r.status_code, r.text[:200])
                ok = False
        except requests.exceptions.RequestException as e:
            # No se loguea el mensaje de la excepción: incluye la URL con el token del bot.
            log.error("Telegram: error de red en la parte %d (%s).", i, type(e).__name__)
            ok = False
    return ok


def construir_mensaje(
    top: pd.DataFrame,
    recomendacion: Optional[str],
    comision_lecap: float = config.COMISION_LECAP,
    comision_otros: float = config.COMISION_OTROS,
) -> str:
    """Resumen ejecutivo: oportunidades con precio, tasas y rendimiento + comentario de IA (o aviso)."""
    lineas = [f"📈 LECAPs / BONCAPs · rueda {datetime.now():%d/%m/%y}", "", "Mejores oportunidades:", ""]
    for _, r in top.iterrows():
        lineas += [
            f"• {r['Ticker']} · {int(r['Días'])}d (vto {r['Vencimiento']:%d/%m/%y})",
            f"  Precio ${r['Precio']:,.2f} → compra c/comisión ${r['Precio Compra']:,.2f} · Valor final ${r['Valor Final']:,.2f}",
            f"  TEM {r['TEM'] * 100:.2f}% · TNA {r['TNA'] * 100:.1f}% · Rend. dir. {r['Rend. Directo'] * 100:.2f}%",
            "",
        ]
    if recomendacion:
        lineas += ["🧠 Análisis:", limpiar_markdown(recomendacion)]
    else:
        lineas.append("ℹ️ El análisis de IA no estuvo disponible en esta corrida (se envía solo el ranking).")
    lineas += ["", data_processor.leyenda_tasas(comision_lecap, comision_otros) + " No es asesoramiento financiero."]
    return "\n".join(lineas)


def enviar_alerta(settings: Settings, titulo: str, detalle: str = "") -> bool:
    """Alerta operativa (p. ej. si el pipeline falló). Oculta secretos y recorta el detalle."""
    texto = f"⚠️ {titulo}"
    if detalle:
        texto += "\n" + config.ocultar_secretos(detalle, settings)[:1000]
    return enviar_mensaje(settings, texto)

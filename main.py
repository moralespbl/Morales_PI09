"""Orquestador del pipeline Morales_PI09.

    Web (login + Excel)  ->  Procesamiento  ->  Google Sheets  ->  IA (Gemini)  ->  Telegram

Uso local:
    python main.py                       # corrida completa
    python main.py --dry-run             # procesa y muestra, sin enviar a Sheets ni a Telegram
    python main.py --excel data/x.xlsx   # usa un Excel ya descargado (sin entrar a la web)
    python main.py --skip-download       # usa el último Excel de ./data/

El mismo código corre en GitHub Actions: las variables vienen de los Secrets en vez del .env.
Códigos de salida: 0 = ok, 1 = falló un paso crítico (descarga o procesamiento), 2 = configuración incompleta.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

import config
from config import ConfigError, Settings
from modules import ai_advisor, data_processor, notifier, scraper, sheets_manager

log = logging.getLogger("main")


def ejecutar(settings: Settings, excel: Optional[Path] = None, skip_download: bool = False, dry_run: bool = False) -> int:
    # 1) Obtener el Excel
    try:
        if excel is not None:
            ruta = Path(excel)
        elif skip_download:
            ruta = data_processor.buscar_ultimo_excel()
        else:
            ruta = scraper.descargar_excel(settings)
    except Exception as e:  # noqa: BLE001
        return _fallo(settings, "No se pudo obtener el Excel de la plataforma", e, dry_run)

    # 2) Procesar (reemplazo de la macro VBA)
    try:
        df = data_processor.procesar_excel(
            ruta, comision_lecap=settings.comision_lecap, comision_otros=settings.comision_otros, precio_modo=settings.precio_modo,
            familias=settings.familias,
        )
    except Exception as e:  # noqa: BLE001
        return _fallo(settings, f"No se pudo procesar {ruta.name}", e, dry_run)

    print(df.assign(Vencimiento=data_processor.formatear_vencimiento(df["Vencimiento"]))[data_processor.COLUMNAS_PUBLICADAS].to_string(index=False))

    # 3) Google Sheets (no crítico: si falla se informa y se sigue)
    if dry_run:
        log.info("--dry-run: se omite Google Sheets.")
    else:
        try:
            sheets_manager.publicar(df, settings)
        except Exception as e:  # noqa: BLE001
            log.error("Google Sheets falló: %s", config.ocultar_secretos(f"{type(e).__name__}: {e}", settings)[:300])

    # 4) IA con contexto mínimo (no crítico: si Gemini no responde se envía solo el ranking)
    recomendacion = None
    try:
        recomendacion = ai_advisor.generar_recomendacion(df, settings)
    except Exception as e:  # noqa: BLE001
        log.error("El asesor de IA falló: %s", config.ocultar_secretos(f"{type(e).__name__}: {e}", settings)[:300])

    # 5) Telegram
    top = ai_advisor.seleccionar_oportunidades(df)
    mensaje = notifier.construir_mensaje(top, recomendacion, settings.comision_lecap, settings.comision_otros)
    if dry_run:
        print("\n--- Mensaje que se enviaría a Telegram ---\n" + mensaje)
        return 0
    notifier.enviar_mensaje(settings, mensaje)
    return 0


def _fallo(settings: Settings, titulo: str, error: Exception, dry_run: bool) -> int:
    detalle = config.ocultar_secretos(f"{type(error).__name__}: {error}", settings)
    log.error("%s: %s", titulo, detalle)
    if not dry_run:
        notifier.enviar_alerta(settings, titulo, detalle)
    return 1


def main(argv: Optional[list] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Pipeline Morales_PI09 (LECAPs/BONCAPs)")
    parser.add_argument("--excel", type=Path, help="procesar este Excel en vez de descargarlo")
    parser.add_argument("--skip-download", action="store_true", help="usar el último Excel de ./data/")
    parser.add_argument("--dry-run", action="store_true", help="no enviar a Google Sheets ni a Telegram")
    args = parser.parse_args(argv)

    try:
        settings = config.cargar_settings()
    except ConfigError as e:
        log.error("%s", e)
        return 2
    return ejecutar(settings, excel=args.excel, skip_download=args.skip_download, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

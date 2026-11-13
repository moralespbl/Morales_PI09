# Morales_PI09 · LECAPs / BONCAPs

Pipeline: **Web (login + Excel) → pandas (reemplaza el VBA) → Google Sheets → Gemini → Telegram**.

## Probar en local

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env                                   # Windows: copy .env.example .env  (sin .txt)
# completá .env con tus datos
pytest                                                 # 49 tests, no tocan servicios reales
python main.py --excel data/mi_excel.xlsx --dry-run    # procesa un Excel que ya tengas, sin enviar nada
python main.py --dry-run                               # descarga de la web, procesa, muestra; no envía
python main.py                                         # corrida completa
```

Si falta una variable, el programa lista cuáles y termina con código 2.
Si falla la web, queda una captura `data/debug_*.png` y el HTML para ajustar selectores.

## Cómo recorre la web
mesaifa.netlify.app es de una sola pantalla (la URL no cambia): login (`#ag-email`, `#ag-pass`) → cierra el cartel emergente → clic en la pestaña `#tab-tasafija` → clic en *Exportar Excel* y captura la descarga. Todos los selectores se pueden pisar en `.env` (ver `.env.example`); si algo falla se guarda captura + HTML en `data/debug_*`.

## Activar GitHub Actions
1. Subí el proyecto a un repo (el `.gitignore` excluye `.env` y credenciales).
2. Repo → **Settings → Secrets and variables → Actions → New repository secret**. Creá uno por cada nombre:
   `WEB_USER`, `WEB_PASS`, `WEB_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`,
   `GOOGLE_SHEETS_CREDENTIALS_JSON` (pegá el contenido completo del JSON), `GOOGLE_SHEET_ID`.
   Los opcionales (`WEB_BONOS_TEXT`, etc.) también como secrets; agregalos en el bloque `env:` del workflow.
3. Compartí la planilla con el `client_email` de la cuenta de servicio (permiso Editor).
4. **Actions → Morales_PI09 → Run workflow** para probar a mano. Luego corre solo de lunes a viernes 17:30 (ART).

## Seguridad
Nunca pegues tokens ni claves en chats, código o commits. Si ya se expusieron, regeneralos
(BotFather → `/revoke`, nueva clave de Gemini, nueva clave de la cuenta de servicio).

![Mensaje de Telegram con las mejores oportunidades](imagenes\telegram_renta_fija.png)  
  
![Informacion enviada a Google Sheets](imagenes\shet_renta_fija.png)

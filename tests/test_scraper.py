"""Prueba de punta a punta del scraper contra un sitio local simulado (login + navegación + descarga)."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import pytest

pytest.importorskip("playwright.sync_api")

import config
from modules import data_processor as dp
from modules import scraper
from tests.conftest import ENV_COMPLETO, hacer_excel

USUARIO, CLAVE = "usuario", "clave-secreta"


def _handler(xlsx_bytes: bytes):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _logueado(self):
            return "sesion=ok" in (self.headers.get("Cookie") or "")

        def _html(self, cuerpo, codigo=200):
            self.send_response(codigo)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(cuerpo.encode())

        def do_GET(self):
            if self.path == "/":
                self._html('<form method="post" action="/login"><input name="usuario"><input type="password" name="clave">'
                           '<button type="submit">Ingresar</button></form>')
            elif self.path == "/home" and self._logueado():
                self._html('<a href="/bonos">Tasa fija</a>')
            elif self.path == "/bonos" and self._logueado():
                self._html('<h1>Bonos</h1><a href="/descarga/rueda.xlsx">Descargar Excel</a>'
                           '<a href="/descarga/no-excel.csv">Descargar CSV</a>')
            elif self.path == "/descarga/rueda.xlsx" and self._logueado():
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                self.send_header("Content-Disposition", 'attachment; filename="rueda.xlsx"')
                self.end_headers()
                self.wfile.write(xlsx_bytes)
            elif self.path == "/descarga/no-excel.csv":
                self.send_response(200)
                self.send_header("Content-Disposition", 'attachment; filename="x.csv"')
                self.end_headers()
                self.wfile.write(b"a,b")
            else:
                self._html("no autorizado", 401)

        def do_POST(self):
            datos = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            if datos.get("usuario") == [USUARIO] and datos.get("clave") == [CLAVE]:
                self.send_response(302)
                self.send_header("Set-Cookie", "sesion=ok; Path=/")
                self.send_header("Location", "/home")
                self.end_headers()
            else:
                self._html('<form method="post" action="/login"><input name="usuario"><input type="password" name="clave">'
                           '<button type="submit">Ingresar</button></form><p>Credenciales inválidas</p>', 200)

    return H


@pytest.fixture
def sitio(tmp_path):
    ruta = hacer_excel(tmp_path / "origen.xlsx")
    server = HTTPServer(("127.0.0.1", 0), _handler(ruta.read_bytes()))
    hilo = threading.Thread(target=server.serve_forever, daemon=True)
    hilo.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _settings(url, **extra):
    env = {**ENV_COMPLETO, "WEB_URL": url, "WEB_USER": USUARIO, "WEB_PASS": CLAVE, "WEB_TIMEOUT_S": "15",
           "WEB_READY_SELECTOR": "body", "WEB_POPUP_SELECTOR": "#sin-popup", **config.SELECTORES_GENERICOS, **extra}
    return config.cargar_settings(env)


def test_login_navegacion_y_descarga_con_selectores_por_defecto(sitio, tmp_path):
    s = _settings(sitio, WEB_BONOS_TEXT="Tasa fija")
    ruta = scraper.descargar_excel(s, carpeta=tmp_path / "data")
    assert ruta.exists() and ruta.suffix == ".xlsx"
    # el archivo descargado es procesable de punta a punta
    assert dp.procesar_excel(ruta)["Días"].tolist() == [10, 45, 120]


def test_navegacion_por_url_directa(sitio, tmp_path):
    s = _settings(sitio, WEB_BONOS_URL=f"{sitio}/bonos")
    assert scraper.descargar_excel(s, carpeta=tmp_path).exists()


def test_clave_incorrecta_da_error_claro_y_guarda_depuracion(sitio, tmp_path):
    s = _settings(sitio, WEB_PASS="mala", WEB_TIMEOUT_S="8")
    with pytest.raises(scraper.ScraperError, match="usuario o clave"):
        scraper.descargar_excel(s, carpeta=tmp_path)
    assert list(tmp_path.glob("debug_*.png")) and list(tmp_path.glob("debug_*.html"))


def test_descarga_que_no_es_excel_se_rechaza(sitio, tmp_path):
    s = _settings(sitio, WEB_BONOS_URL=f"{sitio}/bonos", WEB_DOWNLOAD_SELECTOR='a[href$="no-excel.csv"]')
    with pytest.raises(scraper.ScraperError, match="no es un Excel") as e:
        scraper.descargar_excel(s, carpeta=tmp_path)
    assert CLAVE not in str(e.value)


def test_selector_inexistente_falla_sin_exponer_secretos(sitio, tmp_path):
    s = _settings(sitio, WEB_BONOS_URL=f"{sitio}/bonos", WEB_DOWNLOAD_SELECTOR="#no-existe", WEB_TIMEOUT_S="3")
    with pytest.raises(scraper.ScraperError, match="selectores") as e:
        scraper.descargar_excel(s, carpeta=tmp_path)
    assert CLAVE not in str(e.value)


def test_limpiar_antiguos_conserva_los_ultimos(tmp_path):
    import os, time

    for i in range(7):
        f = tmp_path / f"rueda_{i}.xlsx"
        f.write_bytes(b"x")
        os.utime(f, (time.time() - 100 + i, time.time() - 100 + i))
    scraper.limpiar_antiguos(tmp_path, conservar=3)
    assert sorted(p.name for p in tmp_path.glob("rueda_*.xlsx")) == ["rueda_4.xlsx", "rueda_5.xlsx", "rueda_6.xlsx"]


# ---------- SPA como mesaifa.netlify.app: una sola URL, clave type="text", cartel, pestaña y exportación por JS ----------
PAGINA_SPA = """<!doctype html><html><body>
<div id="auth-gate"><input id="ag-email" type="email" placeholder="Tu Mail">
<input id="ag-pass" type="text" placeholder="Tu contraseña"><button onclick="cpLogin()">Entrar</button></div>
<div id="app" style="display:none">
  <div id="aviso" style="position:fixed;inset:0;background:#000a"><button class="cerrar-modal" onclick="this.parentNode.remove()">Cerrar</button></div>
  <button class="tab" id="tab-otra" onclick="sw('otra')">Otra</button>
  <button class="tab" id="tab-tasafija" onclick="sw('tasafija')">Tasa Fija</button>
  <div id="p-otra"><button class="xbtn" onclick="alert('no')">Exportar Excel</button></div>
  <div id="p-tasafija" style="display:none"><table><tbody id="tb"></tbody></table>
    <button class="xbtn" onclick="exportXLSLetras('tasafija','TASAFIJA')">Exportar Excel</button></div>
</div>
<script>
async function cpLogin(){
  if(document.getElementById('ag-email').value!=='%(u)s'||document.getElementById('ag-pass').value!=='%(p)s')return;
  document.getElementById('auth-gate').style.display='none';document.getElementById('app').style.display='block';}
function sw(n){document.getElementById('p-tasafija').style.display=n==='tasafija'?'block':'none';
  setTimeout(()=>{document.getElementById('tb').innerHTML='<tr><td>S30N6</td></tr>'},300);}
async function exportXLSLetras(a,b){
  const buf=await (await fetch('/datos.xlsx')).blob();
  const l=document.createElement('a');l.href=URL.createObjectURL(buf);l.download='TasaFija_11-11-2026.xlsx';
  document.body.appendChild(l);l.click();}
</script></body></html>"""


@pytest.fixture
def sitio_spa(tmp_path):
    datos = hacer_excel(tmp_path / "origen.xlsx").read_bytes()
    html = (PAGINA_SPA % {"u": USUARIO, "p": CLAVE}).encode()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            cuerpo, tipo = (datos, "application/octet-stream") if self.path == "/datos.xlsx" else (html, "text/html; charset=utf-8")
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.end_headers()
            self.wfile.write(cuerpo)

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_spa_real_con_selectores_por_defecto(sitio_spa, tmp_path):
    env = {**ENV_COMPLETO, "WEB_URL": sitio_spa, "WEB_USER": USUARIO, "WEB_PASS": CLAVE, "WEB_TIMEOUT_S": "20",
           "WEB_POPUP_SELECTOR": "button.cerrar-modal"}  # el selector real del cartel se define en .env
    ruta = scraper.descargar_excel(config.cargar_settings(env), carpeta=tmp_path / "data")
    assert ruta.suffix == ".xlsx" and dp.procesar_excel(ruta)["Días"].tolist() == [10, 45, 120]

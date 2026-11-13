"""Tests de sheets_manager, ai_advisor, notifier y del orquestador (con servicios externos simulados)."""
import json

import pandas as pd
import pytest

import config
import main
from modules import ai_advisor, data_processor as dp, notifier, sheets_manager
from tests.conftest import ENV_COMPLETO, hacer_excel


@pytest.fixture
def df(tmp_path):
    return dp.procesar_excel(hacer_excel(tmp_path / "r.xlsx"))


# ---------- sheets_manager ----------
def test_matriz_fechas_como_serial_y_sin_nan(df):
    m = sheets_manager.preparar_matriz(df[dp.COLUMNAS_PUBLICADAS])
    assert m[0] == dp.COLUMNAS_PUBLICADAS
    fila = dict(zip(m[0], m[1]))
    assert isinstance(fila["Vencimiento"], float) and fila["Vencimiento"] > 40000
    assert all(not (isinstance(v, float) and v != v) for f in m[1:] for v in f)
    json.dumps(m)  # serializable para la API


def test_formatos_cubren_columnas(df):
    rangos = dict(sheets_manager.construir_formatos(df[dp.COLUMNAS_PUBLICADAS]))
    assert "A4:L4" in rangos and "A1" in rangos
    assert any(f.get("numberFormat", {}).get("pattern") == "0.00%" for f in rangos.values())


def test_credenciales_json_y_archivo(tmp_path):
    assert sheets_manager.cargar_credenciales('{"a": 1}') == {"a": 1}
    p = tmp_path / "c.json"
    p.write_text('{"b": 2}')
    assert sheets_manager.cargar_credenciales(str(p)) == {"b": 2}
    with pytest.raises(ValueError):
        sheets_manager.cargar_credenciales("{roto")
    with pytest.raises(ValueError):
        sheets_manager.cargar_credenciales(str(tmp_path / "no.json"))


def test_letras():
    assert [sheets_manager._letra(n) for n in (1, 26, 27)] == ["A", "Z", "AA"]


# ---------- ai_advisor ----------
def _curva(n=40):
    return pd.DataFrame(
        {
            "Ticker": [f"T{i}" for i in range(n)],
            "Días": [10 + i * 12 for i in range(n)],
            "Precio Compra": [100.0] * n,
            "TEM": [0.02 + (i % 7) * 0.001 for i in range(n)],
            "TNA": [0.24 + (i % 7) * 0.012 for i in range(n)],
            "TEA": [0.27] * n,
        }
    )


def test_seleccion_5_oportunidades_y_resumen_corto():
    d = _curva()
    top = ai_advisor.seleccionar_oportunidades(d)
    assert len(top) == 5 and top["Días"].is_monotonic_increasing
    assert len(ai_advisor.resumen_compacto(d, top)) < 600  # contexto mínimo


def test_seleccion_con_pocas_filas():
    assert len(ai_advisor.seleccionar_oportunidades(_curva(3))) == 3


class ErrorApi(Exception):
    def __init__(self, code):
        self.code = code


def test_gemini_reintenta_503_y_luego_responde(settings, monkeypatch):
    monkeypatch.setattr(ai_advisor.time, "sleep", lambda s: None)
    llamadas = []

    def falso(p, k, m):
        llamadas.append(1)
        if len(llamadas) < 3:
            raise ErrorApi(503)
        return " ok "

    assert ai_advisor.consultar_gemini("x", settings, llamar=falso) == "ok"
    assert len(llamadas) == 3


def test_gemini_503_persistente_devuelve_none(settings, monkeypatch):
    monkeypatch.setattr(ai_advisor.time, "sleep", lambda s: None)

    def falso(p, k, m):
        raise ErrorApi(503)

    assert ai_advisor.consultar_gemini("x", settings, llamar=falso) is None


def test_gemini_error_no_transitorio_no_reintenta(settings):
    n = []

    def falso(p, k, m):
        n.append(1)
        raise ErrorApi(401)

    assert ai_advisor.consultar_gemini("x", settings, llamar=falso) is None and len(n) == 1


# ---------- notifier ----------
def test_dividir_mensaje_respeta_limite():
    texto = "\n".join(["x" * 90] * 200)
    partes = notifier.dividir_mensaje(texto)
    assert len(partes) > 1 and all(len(p) <= notifier.LIMITE for p in partes)
    assert notifier.dividir_mensaje("y" * 9000)[0] == "y" * notifier.LIMITE


def test_mensaje_con_y_sin_ia(df):
    top = ai_advisor.seleccionar_oportunidades(df)
    con = notifier.construir_mensaje(top, "**Hola**\n- punto")
    assert "Hola" in con and "**" not in con and "• punto" in con
    assert "no estuvo disponible" in notifier.construir_mensaje(top, None)


class Resp:
    def __init__(self, code=200, body=None):
        self.status_code, self._b, self.text = code, body or {}, "x"

    def json(self):
        return self._b


def test_enviar_mensaje_reintenta_429(settings, monkeypatch):
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)
    respuestas = iter([Resp(429, {"parameters": {"retry_after": 1}}), Resp(200)])
    monkeypatch.setattr(notifier.requests, "post", lambda *a, **k: next(respuestas))
    assert notifier.enviar_mensaje(settings, "hola") is True


def test_error_de_red_no_filtra_token(settings, monkeypatch, caplog):
    def boom(*a, **k):
        raise notifier.requests.exceptions.ConnectionError(f"https://api.telegram.org/bot{settings.telegram_bot_token}/x")

    monkeypatch.setattr(notifier.requests, "post", boom)
    assert notifier.enviar_mensaje(settings, "hola") is False
    assert settings.telegram_bot_token not in caplog.text


# ---------- main ----------
@pytest.fixture
def espias(monkeypatch):
    env = {"enviados": [], "sheets": []}
    monkeypatch.setattr(main.notifier, "enviar_mensaje", lambda s, t: env["enviados"].append(t) or True)
    monkeypatch.setattr(main.sheets_manager, "publicar", lambda d, s: env["sheets"].append(len(d)))
    return env


def test_pipeline_completo_con_ia(tmp_path, settings, espias, monkeypatch):
    xl = hacer_excel(tmp_path / "r.xlsx")
    monkeypatch.setattr(main.ai_advisor, "generar_recomendacion", lambda d, s: "Análisis IA")
    assert main.ejecutar(settings, excel=xl) == 0
    assert espias["sheets"] == [3] and "Análisis IA" in espias["enviados"][0]


def test_pipeline_sigue_si_gemini_y_sheets_fallan(tmp_path, settings, espias, monkeypatch):
    xl = hacer_excel(tmp_path / "r.xlsx")
    monkeypatch.setattr(main.ai_advisor, "generar_recomendacion", lambda d, s: None)
    monkeypatch.setattr(main.sheets_manager, "publicar", lambda d, s: (_ for _ in ()).throw(RuntimeError("caído")))
    assert main.ejecutar(settings, excel=xl) == 0
    assert "no estuvo disponible" in espias["enviados"][0]


def test_excel_invalido_alerta_y_devuelve_1(tmp_path, settings, espias):
    malo = tmp_path / "malo.xlsx"
    pd.DataFrame([["nada"]]).to_excel(malo, header=False, index=False)
    assert main.ejecutar(settings, excel=malo) == 1
    assert espias["enviados"][0].startswith("⚠️")


def test_dry_run_no_envia(tmp_path, settings, espias, monkeypatch):
    monkeypatch.setattr(main.ai_advisor, "generar_recomendacion", lambda d, s: None)
    assert main.ejecutar(settings, excel=hacer_excel(tmp_path / "r.xlsx"), dry_run=True) == 0
    assert espias["enviados"] == [] and espias["sheets"] == []


def test_main_sin_variables_sale_con_2(monkeypatch):
    for k in ENV_COMPLETO:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None, raising=False)
    assert main.main([]) == 2


def test_publicar_llama_a_gspread_con_el_layout_de_la_macro(df, settings, monkeypatch):
    import sys
    import types

    llamadas = {"update": [], "format": [], "batch": []}

    class Hoja:
        id = 7

        def clear(self): pass
        def clear_basic_filter(self): pass
        def resize(self, **k): pass
        def update(self, values, range_name): llamadas["update"].append((range_name, values))
        def format(self, rango, fmt): llamadas["format"].append(rango)

    class Libro:
        def worksheet(self, n): return Hoja()
        def batch_update(self, body): llamadas["batch"].append(body)

    fake = types.SimpleNamespace(
        service_account_from_dict=lambda c: types.SimpleNamespace(open_by_key=lambda i: Libro()),
        exceptions=types.SimpleNamespace(WorksheetNotFound=KeyError),
    )
    monkeypatch.setitem(sys.modules, "gspread", fake)
    sheets_manager.publicar(df, settings)
    rangos = dict(llamadas["update"])
    assert rangos["A1"][0][0].startswith("Simulador") and "A4" in rangos
    assert rangos["A4"][0][:6] == ["Ticker", "Familia", "Precio", "Var %", "Vencim.", "Días"]
    assert "A4:L4" in llamadas["format"]
    assert any("addConditionalFormatRule" in r for b in llamadas["batch"] for r in b["requests"])

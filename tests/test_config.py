import pytest

import config
from tests.conftest import ENV_COMPLETO


def test_configuracion_completa_ok(settings):
    assert settings.web_user == "usuario"
    assert settings.comision_lecap == 0.00101 and settings.comision_otros == 0.005025
    assert settings.timeout_ms == 60_000


def test_falla_explicitamente_y_lista_todo_lo_que_falta():
    env = dict(ENV_COMPLETO)
    del env["WEB_USER"]
    env["GEMINI_API_KEY"] = "   "  # solo espacios = faltante
    with pytest.raises(config.ConfigError) as e:
        config.cargar_settings(env)
    msg = str(e.value)
    assert "WEB_USER" in msg and "GEMINI_API_KEY" in msg
    assert "WEB_PASS" not in msg


def test_precio_modo_invalido():
    with pytest.raises(config.ConfigError):
        config.cargar_settings({**ENV_COMPLETO, "PRECIO_MODO": "raro"})


def test_opcionales_se_pisan_por_entorno():
    s = config.cargar_settings({**ENV_COMPLETO, "WORKSHEET_NAME": "Rueda", "WEB_TIMEOUT_S": "10", "WEB_BONOS_TEXT": "Tasa fija"})
    assert (s.worksheet_name, s.timeout_ms, s.web_bonos_text) == ("Rueda", 10_000, "Tasa fija")


def test_ocultar_secretos(settings):
    txt = f"error con {settings.telegram_bot_token} y {settings.web_pass}"
    limpio = config.ocultar_secretos(txt, settings)
    assert settings.telegram_bot_token not in limpio and settings.web_pass not in limpio

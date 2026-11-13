import numpy as np
import pandas as pd
import pytest

from modules import data_processor as dp
from tests.conftest import FILAS_EJEMPLO, hacer_excel


# ---- limpieza -----------------------------------------------------------------
def test_limpiar_dias():
    s = pd.Series(["45d", " 120 D", "10 d", 30, 7.0, "abc", None, ""], dtype=object)
    r = dp.limpiar_dias(s)
    assert r.iloc[:5].tolist() == [45, 120, 10, 30, 7]
    assert r.iloc[5:].isna().all()


def test_limpiar_precio_texto_solo_digitos_sobre_100():
    s = pd.Series(["$ 1.234,56", "123456", "10.500", "S/D"], dtype=object)
    r = dp.limpiar_precio(s)
    assert r.iloc[0] == pytest.approx(1234.56)
    assert r.iloc[1] == pytest.approx(1234.56)
    assert r.iloc[2] == pytest.approx(105.00)
    assert np.isnan(r.iloc[3])


def test_precio_numerico_real_no_se_divide_por_100():
    s = pd.Series([123.45, "12345"], dtype=object)
    r = dp.limpiar_precio(s, "auto")
    assert r.tolist() == [pytest.approx(123.45), pytest.approx(123.45)]


def test_precio_modo_numero_interpreta_formato_argentino():
    r = dp.limpiar_precio(pd.Series(["1.234,56", "98,5"], dtype=object), "numero")
    assert r.tolist() == [pytest.approx(1234.56), pytest.approx(98.5)]


@pytest.mark.parametrize(
    "texto,esperado",
    [("1.234,56", 1234.56), ("1,234.56", 1234.56), ("123,45", 123.45), ("123.45", 123.45), ("1.300", 1300.0), ("$ 1000", 1000.0), (110, 110.0)],
)
def test_parsear_numero(texto, esperado):
    assert dp.parsear_numero(pd.Series([texto], dtype=object)).iloc[0] == pytest.approx(esperado)


def test_vencimiento_acepta_texto_fecha_y_serial_de_excel():
    s = pd.Series(["18/12/26", pd.Timestamp("2027-03-15"), 46387, "basura"], dtype=object)  # 46387 = 2026-12-31
    r = dp.limpiar_vencimiento(s)
    assert r.iloc[0] == pd.Timestamp("2026-12-18")
    assert r.iloc[1] == pd.Timestamp("2027-03-15")
    assert r.iloc[2] == pd.Timestamp("2026-12-31")
    assert pd.isna(r.iloc[3])
    assert dp.formatear_vencimiento(r.iloc[:2]).tolist() == ["18/12/26", "15/03/27"]


# ---- cálculo ------------------------------------------------------------------
def _raw(filas):
    df = pd.DataFrame(filas, columns=["ticker", "dias", "precio", "vencimiento", "valor_final"])
    return df.astype(object)


def test_formulas_y_comision_diferencial():
    df = dp.procesar_dataframe(_raw([["S30N6", "30d", "10000", "18/12/26", "110"], ["T15E7", "30d", "10000", "18/12/26", "110"]]))
    lecap = df[df["Ticker"] == "S30N6"].iloc[0]
    boncap = df[df["Ticker"] == "T15E7"].iloc[0]

    assert lecap["Comisión"] == 0.00101 and boncap["Comisión"] == 0.005025
    pc = 100.0 * (1 + 0.00101)
    assert lecap["Precio"] == pytest.approx(100.0)
    assert lecap["Precio Compra"] == pytest.approx(pc)
    assert lecap["Rend. Directo"] == pytest.approx(110 / pc - 1)
    assert lecap["TEA"] == pytest.approx((110 / pc) ** (365 / 30) - 1)
    assert lecap["TEM"] == pytest.approx((110 / pc) ** (30 / 30) - 1)
    assert lecap["TNA"] == pytest.approx(lecap["TEM"] * 12)
    assert boncap["Precio Compra"] == pytest.approx(100.0 * 1.005025)
    assert boncap["TEM"] < lecap["TEM"]  # misma curva, mayor comisión -> menor rendimiento


def test_orden_por_dias_ascendente_y_filas_invalidas_ignoradas():
    df = dp.procesar_dataframe(_raw(FILAS_EJEMPLO))
    assert df["Días"].tolist() == [10, 45, 120]
    assert df["Ticker"].tolist() == ["S15D6", "S30N6", "T15E7"]
    assert df["Días"].dtype.kind == "i"
    assert np.isfinite(df[["Rend. Directo", "TEA", "TEM", "TNA"]].to_numpy()).all()


def test_celdas_vacias_de_excel_no_rompen_ni_se_cuelan():
    """Regresión: una celda vacía llega como NaN; ticker/valor final vacíos deben descartar la fila."""
    filas = [["S30N6", "30d", "10000", "18/12/26", "110"], [None, "5d", "10000", "18/12/26", "110"],
             ["S99", "5d", "10000", "18/12/26", None], ["S98", None, "10000", "18/12/26", "110"], ["S97", "5d", None, "18/12/26", "110"]]
    df = dp.procesar_dataframe(_raw(filas))
    assert df["Ticker"].tolist() == ["S30N6"]


def test_no_acepta_dias_cero_ni_precio_cero():
    df = dp.procesar_dataframe(_raw([["S1", "0d", "10000", "18/12/26", "110"], ["S2", "10d", "0", "18/12/26", "110"], ["S3", "10d", "10000", "18/12/26", "110"]]))
    assert df["Ticker"].tolist() == ["S3"]


# ---- Excel de punta a punta -----------------------------------------------------
def test_procesar_excel_detecta_encabezado_con_filas_previas(tmp_path):
    ruta = hacer_excel(tmp_path / "rueda.xlsx", filas_previas=4)
    df = dp.procesar_excel(ruta)
    assert list(df.columns) == dp.COLUMNAS_SALIDA
    assert df["Días"].tolist() == [10, 45, 120]


def test_encabezados_con_otros_nombres_y_tildes(tmp_path):
    ruta = tmp_path / "otro.xlsx"
    pd.DataFrame(
        [["Especie", "Días a vencimiento", "Último", "Fecha de vencimiento", "Pago final"], ["S30N6", "20d", "10000", "18/12/26", "105"]]
    ).to_excel(ruta, header=False, index=False)
    assert dp.procesar_excel(ruta)["Ticker"].tolist() == ["S30N6"]


def test_excel_sin_columnas_esperadas_da_error_claro(tmp_path):
    ruta = tmp_path / "malo.xlsx"
    pd.DataFrame([["a", "b", "c"], [1, 2, 3]]).to_excel(ruta, header=False, index=False)
    with pytest.raises(dp.DataError, match="encabezados"):
        dp.procesar_excel(ruta)


def test_buscar_ultimo_excel(tmp_path):
    import os, time

    a = hacer_excel(tmp_path / "a.xlsx")
    b = hacer_excel(tmp_path / "b.xlsx")
    os.utime(a, (time.time() - 100, time.time() - 100))
    assert dp.buscar_ultimo_excel(tmp_path) == b
    with pytest.raises(FileNotFoundError):
        dp.buscar_ultimo_excel(tmp_path / "vacia")


def test_excel_real_de_la_plataforma(tmp_path):
    """Layout real: encabezado en la fila 1, 'Vencim.', 'V. Final', precio '$ 109,58', días '18d', Familia y Var %."""
    import pandas as pd

    filas = [
        ["Ticker", "Familia", "Precio", "Var %", "Vencim.", "Días", "Meses", "V. Final"],
        ["S30N6", "LECAPS", "$ 128,36", "▼ -0.04%", "29/11/26", "18d", 0.6, 129.89],
        ["T15E7", "BONCAPS", "$ 154,55", "▲ +0.09%", "14/01/27", "64d", 2.1, 161.1],
        ["TTD26 F", "DUAL TF/T", "$ 176,83", "▲ +0.11%", "14/12/26", "33d", 1.1, 161.14],
    ]
    p = tmp_path / "TasaFija.xlsx"
    pd.DataFrame(filas).to_excel(p, header=False, index=False)
    d = dp.procesar_excel(p, familias=("LECAPS", "BONCAPS"))
    assert d["Ticker"].tolist() == ["S30N6", "T15E7"]          # el bono dual queda fuera
    assert d["Precio"].tolist() == [128.36, 154.55]
    assert d["Meses"].tolist() == [0.6, 2.1]
    assert d["Var %"].iloc[0] == "▼ -0.04%"
    # misma cuenta que la macro: TEM = (VF/(P*(1+0.101%)))^(30/18)-1
    assert abs(d["TEM"].iloc[0] - ((129.89 / (128.36 * 1.00101)) ** (30 / 18) - 1)) < 1e-12
    assert abs(d["TNA"].iloc[0] - d["TEM"].iloc[0] * 12) < 1e-12

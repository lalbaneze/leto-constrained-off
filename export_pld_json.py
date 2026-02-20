# export_pld_json.py
import json
import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(BASE_DIR, "pld_ccee", "data", "pld_ccee.sqlite")

OUT_DIR = os.path.join(BASE_DIR, "dashboard", "data")
TEST_MONTHLY = os.path.join(OUT_DIR, "pld_monthly_avg_test.json")
TEST_META = os.path.join(OUT_DIR, "pld_meta_test.json")

OFF_MONTHLY = os.path.join(OUT_DIR, "pld_monthly_avg.json")
OFF_META = os.path.join(OUT_DIR, "pld_meta.json")


def read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
            return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def write_json(path: str, obj: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"DB não encontrado: {DB_PATH}")

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # pega o último dia disponível
    rmax = con.execute("""
      SELECT MAX(DIA) as max_dia
      FROM pld_diario_medio
      WHERE length(DIA)=10
    """).fetchone()

    max_dia = (rmax["max_dia"] if rmax else None)
    if not max_dia:
        con.close()
        raise SystemExit("❌ max_dia veio None (sem dados em pld_diario_medio). Abortando export.")

    ym = max_dia[:7]  # mês atual (do dado mais recente)

    # calcula a média mensal SOMENTE do mês atual (ym)
    r = con.execute("""
      SELECT AVG(PLD_MEDIO) as pld_medio_mensal
      FROM pld_diario_medio
      WHERE length(DIA)=10
        AND substr(DIA,1,7)=?
    """, (ym,)).fetchone()

    con.close()

    val = r["pld_medio_mensal"] if r else None
    if val is None:
        raise SystemExit(f"❌ Não consegui calcular média mensal para {ym} (val None).")

    # ✅ carrega histórico oficial e atualiza SÓ o mês atual
    monthly_off = read_json(OFF_MONTHLY)
    monthly_new = dict(monthly_off)
    monthly_new[ym] = float(val)

    # escreve TEST
    write_json(TEST_MONTHLY, monthly_new)

    # meta TEST
    meta_test = {
        "max_dia": max_dia,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mes_atualizado": ym
    }
    write_json(TEST_META, meta_test)

    print("✅ Gerados (somente mês atual):")
    print(" -", TEST_MONTHLY)
    print(" -", TEST_META)
    print("max_dia:", max_dia)
    print(f"{ym} =", float(val))


if __name__ == "__main__":
    main()

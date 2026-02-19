import json
import os
import sqlite3
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "pld_ccee", "data", "pld_ccee.sqlite")

OUT_DIR = os.path.join(BASE_DIR, "dashboard", "data")
OUT_MONTHLY = os.path.join(OUT_DIR, "pld_monthly_avg_test.json")
OUT_META = os.path.join(OUT_DIR, "pld_meta_test.json")

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"DB não encontrado: {DB_PATH}")

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # mensal = média do PLD médio diário do mês
    rows = con.execute("""
      SELECT substr(DIA,1,7) as ym, AVG(PLD_MEDIO) as pld_medio_mensal
      FROM pld_diario_medio
      WHERE length(DIA)=10
      GROUP BY substr(DIA,1,7)
      ORDER BY ym
    """).fetchall()

    monthly = {r["ym"]: float(r["pld_medio_mensal"]) for r in rows if r["pld_medio_mensal"] is not None}

    rmax = con.execute("""
      SELECT MAX(DIA) as max_dia
      FROM pld_diario_medio
      WHERE length(DIA)=10
    """).fetchone()

    max_dia = rmax["max_dia"] if rmax else None
    con.close()

    if not max_dia:
        raise SystemExit("❌ max_dia veio None (sem dados em pld_diario_medio). Abortando export.")

    with open(OUT_MONTHLY, "w", encoding="utf-8") as f:
        json.dump(monthly, f, ensure_ascii=False, indent=2)

    with open(OUT_META, "w", encoding="utf-8") as f:
        json.dump(
            {
                "max_dia": max_dia,
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("✅ Gerados:")
    print(" -", OUT_MONTHLY)
    print(" -", OUT_META)
    print("PLD max_dia:", max_dia)

if __name__ == "__main__":
    main()

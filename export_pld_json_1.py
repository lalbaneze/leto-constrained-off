# export_pld_json.py
import json
import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ✅ caminho correto do DB gerado pelo update_pld_2025
DB_PATH = os.path.join(BASE_DIR, "pld_ccee", "data", "pld_ccee.sqlite")

OUT_DIR = os.path.join(BASE_DIR, "dashboard", "data")
OUT_MONTHLY = os.path.join(OUT_DIR, "pld_monthly_avg.json")
OUT_META = os.path.join(OUT_DIR, "pld_meta.json")

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"DB não encontrado: {DB_PATH}")

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    rows = con.execute("""
      SELECT substr(DIA,1,7) as ym, AVG(PLD_MEDIO) as pld_medio_mensal
      FROM pld_medio
      WHERE length(DIA)=10
      GROUP BY substr(DIA,1,7)
      ORDER BY ym
    """).fetchall()

    monthly = {}
    for r in rows:
        ym = r["ym"]
        val = r["pld_medio_mensal"]
        monthly[ym] = float(val) if val is not None else None

    rmax = con.execute("""
      SELECT MAX(DIA) as max_dia
      FROM pld_medio
      WHERE length(DIA)=10
    """).fetchone()
    max_dia = rmax["max_dia"] if rmax else None
    con.close()

    with open(OUT_MONTHLY, "w", encoding="utf-8") as f:
        json.dump(monthly, f, ensure_ascii=False, indent=2)

    with open(OUT_META, "w", encoding="utf-8") as f:
        json.dump({
            "max_dia": max_dia,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }, f, ensure_ascii=False, indent=2)

    print("✅ Gerados:")
    print(" -", OUT_MONTHLY)
    print(" -", OUT_META)
    print("PLD max_dia:", max_dia)

if __name__ == "__main__":
    main()





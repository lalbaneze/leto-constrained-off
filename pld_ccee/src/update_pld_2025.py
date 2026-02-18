import io
import os
import sqlite3
from datetime import date

import pandas as pd
import requests

# Base = .../pld_ccee
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "pld_ccee.sqlite")

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ✅ Links diretos (pda-download) – não passam pelo CKAN e não dão 403
PDA_BY_YEAR = {
    2025: "https://pda-download.ccee.org.br/korJMXwpSLGyVlpRMQWduA/content",
    2026: "https://pda-download.ccee.org.br/6A5wq97KTCWv_bvs3CqsQQ/content",
}


def years_to_update():
    y = date.today().year
    return [y - 1, y]


def ensure_tables(con: sqlite3.Connection) -> None:
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pld_horario (
            DIA TEXT,
            HORA INTEGER,
            SUBMERCADO TEXT,
            PLD_HORA REAL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pld_medio (
            DIA TEXT,
            HORA INTEGER,
            PLD_MEDIO REAL
        )
    """)

    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pld_horario
        ON pld_horario (DIA, HORA, SUBMERCADO)
    """)

    con.commit()


def _count_rows(db_path: str, table: str) -> int:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    try:
        n = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except Exception:
        n = 0
    con.close()
    return int(n)


def fetch_csv_text(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=180)
    r.raise_for_status()
    return r.text


def load_csv_text_to_sqlite(csv_text: str) -> None:
    sample = csv_text[:2000]
    sep = ";" if sample.count(";") > sample.count(",") else ","

    df = pd.read_csv(io.StringIO(csv_text), sep=sep)
    df.columns = [c.strip().upper() for c in df.columns]

    # CSV oficial geralmente tem: DIA (DD/MM/AAAA), HORA, SUBMERCADO, PLD_HORA
    for c in ["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]:
        if c not in df.columns:
            raise RuntimeError(f"Coluna {c} não encontrada no CSV. Colunas: {df.columns.tolist()}")

    df2 = df[["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]].copy()

    df2["DATA"] = pd.to_datetime(df2["DIA"].astype(str).str.strip(), dayfirst=True, errors="coerce")
    df2 = df2.dropna(subset=["DATA"])
    df2["DIA"] = df2["DATA"].dt.strftime("%Y-%m-%d")

    df2["HORA"] = pd.to_numeric(df2["HORA"], errors="coerce").fillna(0).astype(int)
    df2["SUBMERCADO"] = df2["SUBMERCADO"].astype(str).str.strip().str.lower()

    # PLD com vírgula decimal (se vier assim)
    pld_raw = df2["PLD_HORA"].astype(str).str.strip()
    mask_pt = pld_raw.str.contains(",", na=False)
    pld_raw.loc[mask_pt] = (
        pld_raw.loc[mask_pt]
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    df2["PLD_HORA"] = pd.to_numeric(pld_raw, errors="coerce")

    df2 = df2[["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]].dropna()

    if df2.empty:
        raise RuntimeError("CSV veio sem linhas válidas após limpeza.")

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    ensure_tables(con)
    cur = con.cursor()

    min_dia = df2["DIA"].min()
    max_dia = df2["DIA"].max()
    print(f"Atualizando intervalo {min_dia} → {max_dia} (linhas={len(df2)})")

    cur.execute("DELETE FROM pld_horario WHERE DIA BETWEEN ? AND ?", (min_dia, max_dia))
    df2.to_sql("pld_horario", con, if_exists="append", index=False)

    # rebuild completo do pld_medio
    cur.execute("DELETE FROM pld_medio")
    cur.execute("""
        INSERT INTO pld_medio (DIA, HORA, PLD_MEDIO)
        SELECT DIA, HORA, AVG(PLD_HORA)
        FROM pld_horario
        GROUP BY DIA, HORA
    """)

    con.commit()

    n_h = cur.execute("SELECT COUNT(*) FROM pld_horario").fetchone()[0]
    n_m = cur.execute("SELECT COUNT(*) FROM pld_medio").fetchone()[0]
    min_db = cur.execute("SELECT MIN(DIA) FROM pld_medio").fetchone()[0]
    max_db = cur.execute("SELECT MAX(DIA) FROM pld_medio").fetchone()[0]
    con.close()

    print("OK ✅")
    print("pld_horario:", n_h, "linhas")
    print("pld_medio  :", n_m, "linhas")
    print("DB range   :", min_db, "→", max_db)


def main():
    # garante DB/tabelas existirem sempre
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    ensure_tables(con)
    con.close()
    print("DB_PATH:", DB_PATH)

    updated_any = False

    for y in years_to_update():
        url = PDA_BY_YEAR.get(y)
        if not url:
            print(f"⚠️ Sem PDA link configurado para {y}. Pulando.")
            continue

        print(f"\n=== Baixando PLD horário {y} ===")
        before = _count_rows(DB_PATH, "pld_horario")
        csv_text = fetch_csv_text(url)
        load_csv_text_to_sqlite(csv_text)
        after = _count_rows(DB_PATH, "pld_horario")

        if after > before:
            updated_any = True

    if not updated_any:
        raise SystemExit("❌ Nenhum dado novo foi carregado (pld_horario não aumentou). Verifique download/URL.")


if __name__ == "__main__":
    main()

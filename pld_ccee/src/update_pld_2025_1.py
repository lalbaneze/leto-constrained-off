# pld_ccee/src/update_pld_2025.py

import io
import os
import sqlite3
from datetime import date

import requests
import pandas as pd

CKAN_BASE = "https://dadosabertos.ccee.org.br"
DATASET = "pld_horario"

# Base = .../pld_ccee
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "pld_ccee.sqlite")


# ------------------------------------------------------------
# Resources a atualizar: ano atual e anterior
# ------------------------------------------------------------
def resource_names_to_update():
    y = date.today().year
    return [f"pld_horario_{y-1}", f"pld_horario_{y}"]


# ------------------------------------------------------------
# CKAN helper
# ------------------------------------------------------------
def get_resource_url(resource_name: str) -> str:
    url = f"{CKAN_BASE}/api/3/action/package_show"
    r = requests.get(url, params={"id": DATASET}, timeout=60)
    r.raise_for_status()
    pkg = r.json()["result"]

    for res in pkg["resources"]:
        name = (res.get("name") or "").strip()
        if name.lower() == resource_name.lower():
            return res["url"]

    raise RuntimeError(
        f"Resource '{resource_name}' não encontrado no dataset '{DATASET}'."
    )


# ------------------------------------------------------------
# SQLite schema
# ------------------------------------------------------------
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

    # evita duplicação silenciosa
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pld_horario
        ON pld_horario (DIA, HORA, SUBMERCADO)
    """)

    con.commit()


# ------------------------------------------------------------
# Core loader
# ------------------------------------------------------------
def load_csv_to_sqlite(csv_url: str) -> None:
    print("Baixando CSV:", csv_url)

    resp = requests.get(csv_url, timeout=120)
    resp.raise_for_status()

    # detecta separador
    sample = resp.text[:2000]
    sep = ";" if sample.count(";") > sample.count(",") else ","

    df = pd.read_csv(io.StringIO(resp.text), sep=sep)
    df.columns = [c.strip().upper() for c in df.columns]

    print("Colunas:", df.columns.tolist())

    # --------------------------------------------------------
    # Checagens mínimas
    # --------------------------------------------------------
    if "MES_REFERENCIA" not in df.columns:
        raise RuntimeError(
            "Coluna MES_REFERENCIA não encontrada no CSV da CCEE."
        )

    def pick(*names):
        for n in names:
            if n in df.columns:
                return n
        return None

    col_dia  = pick("DIA")
    col_hora = pick("HORA", "HR", "HORA_INICIO")
    col_sub  = pick("SUBMERCADO", "SUBMERCADO_SBM", "SBM")
    col_pld  = pick("PLD_HORA", "PLD", "VALOR", "PRECO")

    if not all([col_dia, col_hora, col_sub, col_pld]):
        raise RuntimeError(
            f"Colunas não reconhecidas. Achei: "
            f"DIA={col_dia}, HORA={col_hora}, SUB={col_sub}, PLD={col_pld}"
        )

    # --------------------------------------------------------
    # Monta df2 base
    # --------------------------------------------------------
    df2 = df[["MES_REFERENCIA", col_dia, col_hora, col_sub, col_pld]].copy()
    df2 = df2.rename(columns={
        col_dia:  "DIA_NUM",
        col_hora: "HORA",
        col_sub:  "SUBMERCADO",
        col_pld:  "PLD_HORA",
    })

    df2["MES_REFERENCIA"] = df2["MES_REFERENCIA"].astype(str).str.strip()
    df2["DIA_NUM"] = pd.to_numeric(df2["DIA_NUM"], errors="coerce")

    df2 = df2.dropna(subset=["MES_REFERENCIA", "DIA_NUM"])

    # reconstrói data: YYYYMM + DIA
    df2["ANO"] = df2["MES_REFERENCIA"].str.slice(0, 4)
    df2["MES"] = df2["MES_REFERENCIA"].str.slice(4, 6)

    df2["DATA"] = pd.to_datetime(
        df2["ANO"] + "-" +
        df2["MES"] + "-" +
        df2["DIA_NUM"].astype(int).astype(str),
        errors="coerce"
    )

    df2 = df2.dropna(subset=["DATA"])
    df2["DIA"] = df2["DATA"].dt.strftime("%Y-%m-%d")

    # normaliza tipos
    df2["HORA"] = pd.to_numeric(df2["HORA"], errors="coerce").fillna(0).astype(int)
    df2["SUBMERCADO"] = (
        df2["SUBMERCADO"].astype(str).str.strip().str.lower()
    )

    # PLD com vírgula decimal
    pld_raw = df2["PLD_HORA"].astype(str).str.strip()
    mask_pt = pld_raw.str.contains(",", na=False)
    pld_raw.loc[mask_pt] = (
        pld_raw.loc[mask_pt]
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    df2["PLD_HORA"] = pd.to_numeric(pld_raw, errors="coerce")

    df2 = df2[["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]]
    df2 = df2.dropna()

    print("Linhas após limpeza:", len(df2))

    if df2.empty:
        print("⚠️ CSV sem dados válidos. Nada a atualizar.")
        return

    # --------------------------------------------------------
    # SQLite update (seguro)
    # --------------------------------------------------------
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    ensure_tables(con)
    cur = con.cursor()

    min_dia = df2["DIA"].min()
    max_dia = df2["DIA"].max()

    print(f"Atualizando intervalo {min_dia} → {max_dia}")

    # remove apenas o intervalo recebido
    cur.execute(
        "DELETE FROM pld_horario WHERE DIA BETWEEN ? AND ?",
        (min_dia, max_dia)
    )

    df2.to_sql("pld_horario", con, if_exists="append", index=False)

    # rebuild completo do pld_medio (a partir do histórico)
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


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main():
    for rn in resource_names_to_update():
        print(f"\n=== Atualizando resource {rn} ===")
        csv_url = get_resource_url(rn)
        load_csv_to_sqlite(csv_url)


if __name__ == "__main__":
    main()

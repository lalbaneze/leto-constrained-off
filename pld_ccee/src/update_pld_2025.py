# pld_ccee/src/update_pld_2025.py

import io
import os
import sqlite3
from datetime import date

import pandas as pd
import requests

CKAN_BASE = "https://dadosabertos.ccee.org.br"
DATASET = "pld_horario"

# Base = .../pld_ccee
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "pld_ccee.sqlite")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": "https://dadosabertos.ccee.org.br/",
}

SESSION = requests.Session()
SESSION.headers.update(DEFAULT_HEADERS)

# ------------------------------------------------------------
# 1) VOCÊ PRECISA PREENCHER ISSO UMA VEZ
# ------------------------------------------------------------
# Encontre esses IDs no site (no navegador) e cole aqui:
# - pld_horario_<ano atual-1>
# - pld_horario_<ano atual>
RESOURCE_ID_BY_NAME = {
    # EXEMPLO (troque pelos seus):
    # "pld_horario_2024": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    # "pld_horario_2025": "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy",
}

# ------------------------------------------------------------
# Resources a atualizar: ano atual e anterior
# ------------------------------------------------------------
def resource_names_to_update():
    y = date.today().year
    return [f"pld_horario_{y-1}", f"pld_horario_{y}"]


# ------------------------------------------------------------
# Baixa CSV via CKAN datastore_search (evita package_show)
# ------------------------------------------------------------
def fetch_resource_as_csv_text(resource_name: str) -> str:
    rid = RESOURCE_ID_BY_NAME.get(resource_name)
    if not rid:
        raise RuntimeError(
            f"RESOURCE_ID não configurado para '{resource_name}'. "
            f"Preencha RESOURCE_ID_BY_NAME no topo do arquivo."
        )

    # busca em páginas grandes (CKAN geralmente limita por request)
    # ajusta conforme necessário
    limit = 50000
    offset = 0
    chunks = []
    fields = None

    while True:
        url = f"{CKAN_BASE}/api/3/action/datastore_search"
        r = SESSION.get(url, params={"resource_id": rid, "limit": limit, "offset": offset}, timeout=120)
        r.raise_for_status()
        j = r.json()

        if not j.get("success"):
            raise RuntimeError(f"datastore_search falhou para {resource_name}: {j}")

        result = j["result"]
        records = result.get("records", [])

        if fields is None:
            fields = [f["id"] for f in result.get("fields", []) if f.get("id") != "_id"]

        if not records:
            break

        df = pd.DataFrame.from_records(records)
        # remove coluna interna se existir
        if "_id" in df.columns:
            df = df.drop(columns=["_id"])

        chunks.append(df)

        offset += limit
        # terminou?
        if offset >= result.get("total", 0):
            break

    if not chunks:
        raise RuntimeError(f"Nenhum registro retornado para {resource_name} (rid={rid}).")

    df_all = pd.concat(chunks, ignore_index=True)

    # gera CSV em memória (mantém compatibilidade com seu pipeline atual)
    buf = io.StringIO()
    df_all.to_csv(buf, index=False)
    return buf.getvalue()


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

    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pld_horario
        ON pld_horario (DIA, HORA, SUBMERCADO)
    """)

    con.commit()


# ------------------------------------------------------------
# Core loader
# ------------------------------------------------------------
def load_csv_text_to_sqlite(csv_text: str) -> None:
    # detecta separador
    sample = csv_text[:2000]
    sep = ";" if sample.count(";") > sample.count(",") else ","

    df = pd.read_csv(io.StringIO(csv_text), sep=sep)
    df.columns = [c.strip().upper() for c in df.columns]

    print("Colunas:", df.columns.tolist())

    if "MES_REFERENCIA" not in df.columns:
        raise RuntimeError("Coluna MES_REFERENCIA não encontrada no CSV da CCEE.")

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

    df2["ANO"] = df2["MES_REFERENCIA"].str.slice(0, 4)
    df2["MES"] = df2["MES_REFERENCIA"].str.slice(4, 6)

    df2["DATA"] = pd.to_datetime(
        df2["ANO"] + "-" + df2["MES"] + "-" + df2["DIA_NUM"].astype(int).astype(str),
        errors="coerce"
    )

    df2 = df2.dropna(subset=["DATA"])
    df2["DIA"] = df2["DATA"].dt.strftime("%Y-%m-%d")

    df2["HORA"] = pd.to_numeric(df2["HORA"], errors="coerce").fillna(0).astype(int)
    df2["SUBMERCADO"] = df2["SUBMERCADO"].astype(str).str.strip().str.lower()

    pld_raw = df2["PLD_HORA"].astype(str).str.strip()
    mask_pt = pld_raw.str.contains(",", na=False)
    pld_raw.loc[mask_pt] = (
        pld_raw.loc[mask_pt]
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    df2["PLD_HORA"] = pd.to_numeric(pld_raw, errors="coerce")

    df2 = df2[["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]].dropna()

    print("Linhas após limpeza:", len(df2))
    if df2.empty:
        print("⚠️ CSV sem dados válidos. Nada a atualizar.")
        return

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    ensure_tables(con)
    cur = con.cursor()

    min_dia = df2["DIA"].min()
    max_dia = df2["DIA"].max()

    print(f"Atualizando intervalo {min_dia} → {max_dia}")

    cur.execute("DELETE FROM pld_horario WHERE DIA BETWEEN ? AND ?", (min_dia, max_dia))
    df2.to_sql("pld_horario", con, if_exists="append", index=False)

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
        csv_text = fetch_resource_as_csv_text(rn)
        load_csv_text_to_sqlite(csv_text)


if __name__ == "__main__":
    main()

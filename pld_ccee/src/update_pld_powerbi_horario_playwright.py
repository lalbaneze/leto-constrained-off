import os
import re
import json
import sqlite3
from datetime import datetime

import pandas as pd
from playwright.sync_api import sync_playwright


# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
# Link do relatório embed (da sua página Painel de Preços)
REPORT_URL = "https://app.powerbi.com/view?r=eyJrIjoiNjk2NzUyNmEtNGZkMy00NDZhLWI4ZjgtMzEyMzhiMDA4NGRkIiwidCI6ImQ3YzNlNTA2LWVmODUtNDM4Ni04ZTU0LTJkZmNkYzgwMTdkMCJ9"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../pld_ccee
DB_PATH = os.path.join(BASE_DIR, "data", "pld_ccee.sqlite")


# ---------------------------------------------------------------------
# SQLITE
# ---------------------------------------------------------------------
def ensure_tables(con: sqlite3.Connection) -> None:
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pld_diario (
            DIA TEXT,
            SUBMERCADO TEXT,
            PLD_DIA REAL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pld_diario_medio (
            DIA TEXT,
            PLD_MEDIO REAL
        )
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pld_diario
        ON pld_diario (DIA, SUBMERCADO)
    """)
    con.commit()


# ---------------------------------------------------------------------
# HELPERS: detecta "tabelas" dentro do JSON do querydata
# ---------------------------------------------------------------------
_DATE_PATTERNS = [
    # 2026-02-12
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d"),
    # 12/02/2026
    (re.compile(r"^\d{2}/\d{2}/\d{4}$"), "%d/%m/%Y"),
]

def _parse_date_any(s: str):
    s = str(s).strip()
    for rx, fmt in _DATE_PATTERNS:
        if rx.match(s):
            try:
                return datetime.strptime(s, fmt).date()
            except Exception:
                return None
    return None

def _is_number(x) -> bool:
    try:
        if x is None:
            return False
        if isinstance(x, bool):
            return False
        float(x)
        return True
    except Exception:
        return False

def _walk(obj):
    """Gera recursivamente tudo que existe no JSON."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        yield obj
        for it in obj:
            yield from _walk(it)

def _find_candidate_tables(j: dict):
    """
    Procura por listas de linhas (list[list]) dentro do JSON.
    PowerBI costuma devolver estruturas com arrays de arrays em algum ponto.
    """
    tables = []
    for node in _walk(j):
        if isinstance(node, list) and node:
            # queremos algo como [[...],[...],...]
            if all(isinstance(r, list) for r in node) and len(node) >= 10:
                # e linhas com pelo menos 2 colunas
                if all(len(r) >= 2 for r in node[:10]):
                    tables.append(node)
    return tables

def _score_table(rows):
    """
    Heurística: tenta achar colunas (data, submercado, valor)
    """
    # só analisa um pedaço
    sample = rows[:200]
    ncols = max(len(r) for r in sample)
    # normaliza linhas curtas
    norm = [r + [None]*(ncols-len(r)) for r in sample]

    # para cada coluna, medir % datas, % strings "submercado-like", % números
    date_rate = []
    sub_rate = []
    num_rate = []
    for c in range(ncols):
        col = [r[c] for r in norm]
        dr = sum(_parse_date_any(v) is not None for v in col) / len(col)
        nr = sum(_is_number(v) for v in col) / len(col)
        sr = sum(isinstance(v, str) for v in col) / len(col)

        # submercado costuma ter strings e palavras tipo "n - norte", "se/co"
        sub_hint = 0
        for v in col:
            if isinstance(v, str):
                vv = v.lower()
                if ("n - norte" in vv) or ("ne - nordeste" in vv) or ("s - sul" in vv) or ("se/co" in vv) or ("sudeste" in vv) or ("submercado" in vv):
                    sub_hint += 1
        sh = sub_hint / len(col)

        date_rate.append(dr)
        num_rate.append(nr)
        sub_rate.append(max(sr, sh))

    # escolhe melhores colunas
    c_date = int(max(range(ncols), key=lambda i: date_rate[i]))
    c_num  = int(max(range(ncols), key=lambda i: num_rate[i]))
    c_sub  = int(max(range(ncols), key=lambda i: sub_rate[i]))

    # score geral: queremos bastante data + bastante número + bastante sub
    score = (date_rate[c_date] * 5) + (num_rate[c_num] * 3) + (sub_rate[c_sub] * 3)

    return score, c_date, c_sub, c_num, date_rate[c_date], sub_rate[c_sub], num_rate[c_num]

def extract_pld_diario_from_querydata_json(j: dict) -> pd.DataFrame:
    """
    Retorna DF com colunas: DIA (yyyy-mm-dd), SUBMERCADO, PLD_DIA
    """
    tables = _find_candidate_tables(j)
    if not tables:
        return pd.DataFrame(columns=["DIA", "SUBMERCADO", "PLD_DIA"])

    best = None
    best_info = None
    for t in tables:
        score, c_date, c_sub, c_num, dr, sr, nr = _score_table(t)
        if best is None or score > best:
            best = score
            best_info = (t, c_date, c_sub, c_num, dr, sr, nr)

    t, c_date, c_sub, c_num, dr, sr, nr = best_info
    if dr < 0.2 or nr < 0.2:
        # não parece série temporal
        return pd.DataFrame(columns=["DIA", "SUBMERCADO", "PLD_DIA"])

    # monta DF
    rows = []
    for r in t:
        if len(r) <= max(c_date, c_sub, c_num):
            continue
        d = _parse_date_any(r[c_date])
        if d is None:
            continue
        sub = r[c_sub]
        val = r[c_num]
        if not isinstance(sub, str):
            continue
        if not _is_number(val):
            continue
        rows.append((d.strftime("%Y-%m-%d"), sub.strip().lower(), float(val)))

    df = pd.DataFrame(rows, columns=["DIA", "SUBMERCADO", "PLD_DIA"]).dropna()
    return df


# ---------------------------------------------------------------------
# PLAYWRIGHT: abre painel, clica na aba "histórico da média diária",
# captura respostas do endpoint querydata.
# ---------------------------------------------------------------------
def fetch_querydata_payloads_for_daily() -> list[dict]:
    payloads = []

    def on_response(resp):
        try:
            url = resp.url
            if "querydata" in url and resp.status == 200:
                ct = resp.headers.get("content-type", "")
                if "application/json" in ct or "text/plain" in ct:
                    payloads.append(resp.json())
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("response", on_response)

        page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=120000)

        # Espera aparecer algo do relatório
        page.wait_for_timeout(8000)

        # Clica na aba "histórico da média diária"
        # (no print que você mandou, existe esse tab)
        tab = page.get_by_text("histórico da média diária", exact=False).first
        tab.click(timeout=30000)

        # dá tempo de carregar e disparar querydata
        page.wait_for_timeout(15000)

        browser.close()

    return payloads


# ---------------------------------------------------------------------
# LOAD TO SQLITE + rebuild mensal
# ---------------------------------------------------------------------
def load_diario_to_sqlite(df: pd.DataFrame) -> None:
    if df.empty:
        raise SystemExit("❌ Não consegui extrair PLD médio diário do Power BI (DF vazio).")

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    ensure_tables(con)
    cur = con.cursor()

    min_d = df["DIA"].min()
    max_d = df["DIA"].max()
    print(f"Atualizando pld_diario intervalo {min_d} → {max_d}")

    # remove intervalo recebido (evita duplicação)
    cur.execute("DELETE FROM pld_diario WHERE DIA BETWEEN ? AND ?", (min_d, max_d))
    df.to_sql("pld_diario", con, if_exists="append", index=False)

    # reconstrói pld_diario_medio como média entre submercados
    cur.execute("DELETE FROM pld_diario_medio WHERE DIA BETWEEN ? AND ?", (min_d, max_d))
    cur.execute("""
        INSERT INTO pld_diario_medio (DIA, PLD_MEDIO)
        SELECT DIA, AVG(PLD_DIA)
        FROM pld_diario
        WHERE DIA BETWEEN ? AND ?
        GROUP BY DIA
    """, (min_d, max_d))

    con.commit()

    n1 = cur.execute("SELECT COUNT(*) FROM pld_diario").fetchone()[0]
    n2 = cur.execute("SELECT COUNT(*) FROM pld_diario_medio").fetchone()[0]
    mindb = cur.execute("SELECT MIN(DIA) FROM pld_diario_medio").fetchone()[0]
    maxdb = cur.execute("SELECT MAX(DIA) FROM pld_diario_medio").fetchone()[0]
    con.close()

    print("OK ✅")
    print("pld_diario       :", n1, "linhas")
    print("pld_diario_medio :", n2, "linhas")
    print("DB range diario  :", mindb, "→", maxdb)


def main():
    payloads = fetch_querydata_payloads_for_daily()
    print("Captured querydata payloads:", len(payloads))

    best_df = pd.DataFrame()
    best_n = 0

    for i, j in enumerate(payloads):
        df = extract_pld_diario_from_querydata_json(j)
        if len(df) > best_n:
            best_df = df
            best_n = len(df)

    print("Melhor DF linhas:", best_n)
    if best_df.empty:
        raise SystemExit("❌ Não identifiquei dataset de PLD médio diário em nenhum querydata.")

    # limpa: remove submercados “vazios” e mantém os 4 principais
    # (sem assumir SE/CO; a média é entre todos que existirem)
    best_df = best_df.dropna()
    load_diario_to_sqlite(best_df)


if __name__ == "__main__":
    main()

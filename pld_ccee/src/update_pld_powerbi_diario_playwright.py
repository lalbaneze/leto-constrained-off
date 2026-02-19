import os
import re
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright


REPORT_URL = "https://app.powerbi.com/view?r=eyJrIjoiNjk2NzUyNmEtNGZkMy00NDZhLWI4ZjgtMzEyMzhiMDA4NGRkIiwidCI6ImQ3YzNlNTA2LWVmODUtNDM4Ni04ZTU0LTJkZmNkYzgwMTdkMCJ9"

# raiz do repositório (2 níveis acima de pld_ccee/src/)
REPO_ROOT = Path(__file__).resolve().parents[2]

DEBUG_OUT = REPO_ROOT / "dashboard" / "data" / "_debug_querydata_sample.json"

BASE_DIR = Path(__file__).resolve().parents[1]  # .../pld_ccee
DB_PATH = BASE_DIR / "data" / "pld_ccee.sqlite"



# ------------------------- SQLITE -------------------------
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


# ------------------------- PARSE POWERBI -------------------------
_DATE_RX_1 = re.compile(r"^\d{4}-\d{2}-\d{2}$")   # 2026-02-12
_DATE_RX_2 = re.compile(r"^\d{2}/\d{2}/\d{4}$")   # 12/02/2026

def parse_date(s):
    if s is None:
        return None
    s = str(s).strip()
    try:
        if _DATE_RX_1.match(s):
            return datetime.strptime(s, "%Y-%m-%d").date()
        if _DATE_RX_2.match(s):
            return datetime.strptime(s, "%d/%m/%Y").date()
    except Exception:
        return None
    return None

def is_number(x):
    try:
        if x is None or isinstance(x, bool):
            return False
        float(x)
        return True
    except Exception:
        return False

def normalize_sub(s: str) -> str:
    return str(s).strip().lower()

def extract_tables_from_powerbi_querydata(j: dict) -> list[list[list]]:
    """
    Retorna uma lista de "tables", cada table = lista de linhas
    Cada linha (no formato do PowerBI) normalmente vem em result["data"]["dsr"]["DS"][...]["PH"][...]["DM0"][...]["C"]
    """
    tables = []
    try:
        results = j.get("results") or []
        for res in results:
            data = (res.get("result") or {}).get("data") or {}
            dsr = data.get("dsr") or {}
            DS = dsr.get("DS") or []
            for ds in DS:
                PH = ds.get("PH") or []
                for ph in PH:
                    # DM0 costuma conter uma lista de "data matrices"
                    for dm in ph.get("DM0") or []:
                        rows = dm.get("C")
                        # rows aqui pode ser "colunas" (dependendo), mas muitas vezes é list[list]
                        if isinstance(rows, list) and rows and isinstance(rows[0], list):
                            tables.append(rows)
                    # alguns reports usam DM1, DM2...
                    for key in ("DM1", "DM2", "DM3"):
                        for dm in ph.get(key) or []:
                            rows = dm.get("C")
                            if isinstance(rows, list) and rows and isinstance(rows[0], list):
                                tables.append(rows)
    except Exception:
        pass
    return tables

def score_table(rows: list[list]):
    # avalia qual coluna é data / sub / valor
    sample = rows[:300]
    ncols = max(len(r) for r in sample)
    # normaliza
    norm = [r + [None]*(ncols-len(r)) for r in sample]

    date_rate = []
    sub_rate = []
    num_rate = []
    for c in range(ncols):
        col = [r[c] for r in norm]
        dr = sum(parse_date(v) is not None for v in col) / len(col)
        nr = sum(is_number(v) for v in col) / len(col)

        # submercado: strings com hints
        sh = 0
        for v in col:
            if isinstance(v, str):
                vv = v.lower()
                if ("n - norte" in vv) or ("ne - nordeste" in vv) or ("s - sul" in vv) or ("se/co" in vv) or ("sudeste" in vv):
                    sh += 1
        sr = sh / len(col)

        date_rate.append(dr)
        num_rate.append(nr)
        sub_rate.append(sr)

    c_date = int(max(range(ncols), key=lambda i: date_rate[i]))
    c_num  = int(max(range(ncols), key=lambda i: num_rate[i]))
    c_sub  = int(max(range(ncols), key=lambda i: sub_rate[i]))

    score = date_rate[c_date]*6 + sub_rate[c_sub]*4 + num_rate[c_num]*3
    return score, c_date, c_sub, c_num, date_rate[c_date], sub_rate[c_sub], num_rate[c_num]

def extract_pld_diario_df(j: dict) -> pd.DataFrame:
    tables = extract_tables_from_powerbi_querydata(j)
    if not tables:
        return pd.DataFrame(columns=["DIA", "SUBMERCADO", "PLD_DIA"])

    best = (-1, None)
    for t in tables:
        try:
            sc = score_table(t)
            if sc[0] > best[0]:
                best = (sc[0], (t, sc))
        except Exception:
            continue

    if best[1] is None:
        return pd.DataFrame(columns=["DIA", "SUBMERCADO", "PLD_DIA"])

    t, sc = best[1]
    _, c_date, c_sub, c_num, dr, sr, nr = sc
    if dr < 0.15 or nr < 0.15 or sr < 0.05:
        return pd.DataFrame(columns=["DIA", "SUBMERCADO", "PLD_DIA"])

    out = []
    for r in t:
        if len(r) <= max(c_date, c_sub, c_num):
            continue
        d = parse_date(r[c_date])
        if d is None:
            continue
        sub = r[c_sub]
        val = r[c_num]
        if not isinstance(sub, str) or not is_number(val):
            continue
        out.append((d.strftime("%Y-%m-%d"), normalize_sub(sub), float(val)))

    return pd.DataFrame(out, columns=["DIA", "SUBMERCADO", "PLD_DIA"]).dropna()


# ------------------------- PLAYWRIGHT CAPTURE -------------------------
def fetch_querydata_payloads():
    payloads = []

    def on_response(resp):
        try:
            if "querydata" in resp.url and resp.status == 200:
                ct = (resp.headers.get("content-type") or "").lower()
                if "json" in ct or "text/plain" in ct:
                    payloads.append(resp.json())
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("response", on_response)

        page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(8000)

        # clique na aba do diário
        page.get_by_text("histórico da média diária", exact=False).first.click(timeout=30000)
        page.wait_for_timeout(15000)

        browser.close()

    return payloads


# ------------------------- LOAD SQLITE -------------------------
def load_to_sqlite(df: pd.DataFrame) -> None:
    if df.empty:
        raise SystemExit("❌ DF vazio após extração.")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    ensure_tables(con)
    cur = con.cursor()

    min_d = df["DIA"].min()
    max_d = df["DIA"].max()
    print(f"Atualizando pld_diario intervalo {min_d} → {max_d}")

    cur.execute("DELETE FROM pld_diario WHERE DIA BETWEEN ? AND ?", (min_d, max_d))
    df.to_sql("pld_diario", con, if_exists="append", index=False)

    cur.execute("DELETE FROM pld_diario_medio WHERE DIA BETWEEN ? AND ?", (min_d, max_d))
    cur.execute("""
        INSERT INTO pld_diario_medio (DIA, PLD_MEDIO)
        SELECT DIA, AVG(PLD_DIA)
        FROM pld_diario
        WHERE DIA BETWEEN ? AND ?
        GROUP BY DIA
    """, (min_d, max_d))

    con.commit()
    maxdb = cur.execute("SELECT MAX(DIA) FROM pld_diario_medio").fetchone()[0]
    con.close()
    print("OK ✅ max_dia diário:", maxdb)


def main():
    payloads = fetch_querydata_payloads()
    print("Captured querydata payloads:", len(payloads))

    best_df = pd.DataFrame()
    for j in payloads:
        df = extract_pld_diario_df(j)
        if len(df) > len(best_df):
            best_df = df

    print("Melhor DF linhas:", len(best_df))

    if best_df.empty:
        # salva debug para a gente inspecionar
        DEBUG_OUT.parent.mkdir(parents=True, exist_ok=True)
        with open(DEBUG_OUT, "w", encoding="utf-8") as f:
            json.dump(payloads[0] if payloads else {}, f, ensure_ascii=False, indent=2)
        print(f"📦 Debug salvo em: {DEBUG_OUT}")

        raise SystemExit(f"❌ Não identifiquei dataset de PLD médio diário. Debug salvo em: {DEBUG_OUT}")

    load_to_sqlite(best_df)


if __name__ == "__main__":
    main()

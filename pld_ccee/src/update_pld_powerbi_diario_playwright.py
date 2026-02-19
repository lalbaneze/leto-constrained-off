# pld_ccee/src/update_pld_powerbi_diario_playwright.py
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright

REPORT_URL = "https://app.powerbi.com/view?r=eyJrIjoiNjk2NzUyNmEtNGZkMy00NDZhLWI4ZjgtMzEyMzhiMDA4NGRkIiwidCI6ImQ3YzNlNTA2LWVmODUtNDM4Ni04ZTU0LTJkZmNkYzgwMTdkMCJ9"

# raiz do repo (2 níveis acima de pld_ccee/src/)
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


def ms_to_ymd(ms: int) -> str:
    # ms UTC -> YYYY-MM-DD
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def to_float(x):
    if x is None:
        return None
    if isinstance(x, bool):
        return None
    try:
        return float(x)
    except Exception:
        try:
            return float(str(x).strip().replace(",", "."))
        except Exception:
            return None


def normalize_sub(s: str) -> str:
    return str(s).strip().lower()


# ------------------------- PARSE QUERYDATA (FORMATO PIVOT) -------------------------
def looks_like_pld_diario_payload(j: dict) -> bool:
    """
    Confere se o payload tem a assinatura do que você colou:
    - descriptor.Select contém DataHora.Data, Submercado..., e medida PLDHorario...
    - dsr.DS[0].PH[0].DM0 com G0 e X/M0
    - SH[0].DM1 com G1
    """
    try:
        res0 = (j.get("results") or [])[0]
        data = ((res0.get("result") or {}).get("data") or {})
        desc = data.get("descriptor") or {}
        sel = desc.get("Select") or []
        names = " | ".join(str(s.get("Name")) for s in sel)

        if ("DataHora.Data" not in names) or ("Submercado.SUBMERCADO_TEXTO" not in names):
            return False
        if ("PLDHorario" not in names) and ("PLDhora" not in names) and ("PLD" not in names):
            return False

        dsr = data.get("dsr") or {}
        DS = dsr.get("DS") or []
        if not DS:
            return False
        d0 = DS[0]
        PH = d0.get("PH") or []
        SH = d0.get("SH") or []
        if not PH or not SH:
            return False
        dm0 = (PH[0].get("DM0") or [])
        dm1 = ((SH[0].get("DM1") or []))
        if not dm0 or not dm1:
            return False
        # checa campos esperados
        if ("G0" not in dm0[0]) or ("X" not in dm0[0]):
            return False
        if ("G1" not in dm1[0]):
            return False
        return True
    except Exception:
        return False


def extract_pld_diario_df_from_payload(j: dict) -> pd.DataFrame:
    """
    Extrai DF com colunas [DIA, SUBMERCADO, PLD_DIA]
    a partir do formato pivot:
      SH[0].DM1 -> lista de submercados (ordem)
      PH[0].DM0 -> lista de dias, cada um com G0 e X (valores M0 na ordem dos submercados)
    """
    res0 = (j.get("results") or [])[0]
    data = ((res0.get("result") or {}).get("data") or {})
    dsr = data.get("dsr") or {}
    DS = dsr.get("DS") or []
    if not DS:
        return pd.DataFrame(columns=["DIA", "SUBMERCADO", "PLD_DIA"])

    d0 = DS[0]
    sub_list = []
    try:
        dm1 = (d0.get("SH") or [])[0].get("DM1") or []
        for it in dm1:
            if "G1" in it:
                sub_list.append(normalize_sub(it["G1"]))
    except Exception:
        sub_list = []

    rows_out = []
    dm0_list = ((d0.get("PH") or [])[0].get("DM0") or [])
    for day in dm0_list:
        if "G0" not in day:
            continue
        dia = ms_to_ymd(int(day["G0"]))
        X = day.get("X") or []
        # X tem 1 valor por submercado, na mesma ordem do dm1
        for i, cell in enumerate(X):
            if i >= len(sub_list):
                break
            sub = sub_list[i] if sub_list else f"sub_{i}"
            val = to_float(cell.get("M0"))
            if val is None:
                continue
            rows_out.append((dia, sub, val))

    df = pd.DataFrame(rows_out, columns=["DIA", "SUBMERCADO", "PLD_DIA"])
    return df


# ------------------------- PLAYWRIGHT CAPTURE -------------------------
def fetch_querydata_payloads():
    payloads = []

    def on_response(resp):
        try:
            if "querydata" in resp.url and resp.status == 200:
                ct = (resp.headers.get("content-type") or "").lower()
                if ("json" in ct) or ("text/plain" in ct):
                    payloads.append(resp.json())
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("response", on_response)

        page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(8000)

        # tenta ir para a aba de "histórico da média diária"
        page.get_by_text("histórico da média diária", exact=False).first.click(timeout=30000)
        page.wait_for_timeout(15000)

        browser.close()

    return payloads


# ------------------------- LOAD SQLITE -------------------------
def load_to_sqlite(df: pd.DataFrame) -> None:
    if df.empty:
        raise SystemExit("❌ DF vazio após extração.")

    os.makedirs(DB_PATH.parent, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    ensure_tables(con)
    cur = con.cursor()

    min_d = df["DIA"].min()
    max_d = df["DIA"].max()
    print(f"Atualizando pld_diario intervalo {min_d} → {max_d}")

    cur.execute("DELETE FROM pld_diario WHERE DIA BETWEEN ? AND ?", (min_d, max_d))
    df.to_sql("pld_diario", con, if_exists="append", index=False)

    # média simples dos 4 submercados por DIA (sem assumir SE/CO)
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

    # escolhe o primeiro payload que bate a assinatura do PLD diário
    chosen = None
    for j in payloads:
        if looks_like_pld_diario_payload(j):
            chosen = j
            break

    if chosen is None:
        DEBUG_OUT.parent.mkdir(parents=True, exist_ok=True)
        with open(DEBUG_OUT, "w", encoding="utf-8") as f:
            json.dump(payloads[0] if payloads else {}, f, ensure_ascii=False, indent=2)
        raise SystemExit(f"❌ Não achei payload do PLD diário. Debug salvo em {DEBUG_OUT}")

    df = extract_pld_diario_df_from_payload(chosen)
    print("DF linhas:", len(df))

    if df.empty:
        DEBUG_OUT.parent.mkdir(parents=True, exist_ok=True)
        with open(DEBUG_OUT, "w", encoding="utf-8") as f:
            json.dump(chosen, f, ensure_ascii=False, indent=2)
        raise SystemExit(f"❌ Extração gerou DF vazio. Debug salvo em {DEBUG_OUT}")

    load_to_sqlite(df)


if __name__ == "__main__":
    main()

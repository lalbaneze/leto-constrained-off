import json
import os
import re
import sqlite3
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from playwright.sync_api import sync_playwright

POWERBI_VIEW_URL = os.environ.get("POWERBI_VIEW_URL")
if not POWERBI_VIEW_URL:
    raise SystemExit("Defina POWERBI_VIEW_URL (env)")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "pld_ccee.sqlite")


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


def write_sqlite(df: pd.DataFrame) -> None:
    if df.empty:
        raise RuntimeError("0 linhas no dataframe final — nada para gravar.")

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    ensure_tables(con)
    cur = con.cursor()

    min_dia = df["DIA"].min()
    max_dia = df["DIA"].max()
    print(f"Atualizando intervalo {min_dia} → {max_dia}")

    cur.execute("DELETE FROM pld_horario WHERE DIA BETWEEN ? AND ?", (min_dia, max_dia))
    df.to_sql("pld_horario", con, if_exists="append", index=False)

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


def parse_querydata(resp_json: Dict[str, Any]) -> pd.DataFrame:
    results = resp_json.get("results") or resp_json.get("Results") or []
    if not results:
        raise RuntimeError("querydata sem results")

    data = None
    for item in results:
        data = (item.get("result") or item.get("Result") or {}).get("data")
        if data:
            break
    if not data:
        raise RuntimeError("querydata sem data")

    dsr = data.get("dsr") or data.get("DSR") or {}
    ds_list = dsr.get("DS") or []
    if not ds_list:
        raise RuntimeError("querydata sem dsr.DS")

    ph = ds_list[0].get("PH") or []
    if not ph:
        raise RuntimeError("querydata sem PH")

    dm0 = ph[0].get("DM0")
    if dm0 is None:
        raise RuntimeError("querydata sem DM0")

    # DM0 vem como lista de linhas, cada linha tem "C": [col0, col1, ...]
    rows = [r.get("C") or [] for r in dm0]

    # tentamos inferir colunas pelo formato típico:
    # DIA, HORA, SUBMERCADO, PLD
    # mas às vezes vem com mais colunas. vamos cortar para 4 primeiras.
    rows4 = [r[:4] for r in rows if len(r) >= 4]
    df = pd.DataFrame(rows4, columns=["DIA", "HORA", "SUBMERCADO", "PLD_HORA"])
    return df


def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    df["DIA"] = df["DIA"].astype(str).str.slice(0, 10)
    df["HORA"] = pd.to_numeric(df["HORA"], errors="coerce").fillna(0).astype(int)
    df["PLD_HORA"] = pd.to_numeric(df["PLD_HORA"], errors="coerce")
    df["SUBMERCADO"] = df["SUBMERCADO"].astype(str).str.strip().str.lower()
    df = df.dropna(subset=["DIA", "HORA", "PLD_HORA"])

    # mantém ano atual e anterior
    y = date.today().year
    y0 = y - 1
    df = df[df["DIA"].str.startswith(str(y0)) | df["DIA"].str.startswith(str(y))].copy()

    return df[["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]]


def main() -> None:
    captured: Dict[str, Any] = {"url": None, "headers": None, "postData": None}

    def looks_like_querydata(url: str) -> bool:
        return "/public/reports/querydata" in url or url.endswith("/querydata") or "querydata" in url

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()

        def on_request(req):
            try:
                url = req.url
                if req.method == "POST" and looks_like_querydata(url):
                    post = req.post_data or ""
                    if post and "SemanticQueryDataShapeCommand" in post:
                        # captura a primeira querydata "grande"
                        if captured["url"] is None:
                            captured["url"] = url
                            captured["headers"] = dict(req.headers)
                            captured["postData"] = post
            except Exception:
                pass

        page.on("request", on_request)

        page.goto(POWERBI_VIEW_URL, wait_until="networkidle", timeout=120000)

        # Tenta forçar um refresh/interaction leve para disparar querydata, se ainda não capturou
        if captured["url"] is None:
            page.wait_for_timeout(5000)

        if captured["url"] is None:
            raise RuntimeError("Não consegui capturar nenhuma chamada querydata do Power BI.")

        print("Captured querydata URL:", captured["url"])

        # Reexecuta a mesma querydata pela sessão do browser (ctx.request)
        # Remove headers problemáticos que o Playwright não gosta / ou que podem conflitar
        hdr = captured["headers"] or {}
        # garante accept/json
        hdr["accept"] = "application/json, text/plain, */*"

        resp = ctx.request.post(
            captured["url"],
            headers=hdr,
            data=captured["postData"],
            timeout=180000,
        )

        print("querydata status:", resp.status)
        if resp.status != 200:
            raise RuntimeError(f"querydata falhou: status={resp.status}")

        j = resp.json()
        df = parse_querydata(j)
        df = clean_df(df)

        print("linhas retornadas:", len(df))
        if df.empty:
            raise RuntimeError("DataFrame vazio após limpeza.")

        write_sqlite(df)

        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()

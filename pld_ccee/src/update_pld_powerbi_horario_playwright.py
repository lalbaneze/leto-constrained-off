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


def extract_dm0(resp_json: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    results = resp_json.get("results") or resp_json.get("Results") or []
    if not results:
        return None

    data = None
    for item in results:
        data = (item.get("result") or item.get("Result") or {}).get("data")
        if data:
            break
    if not data:
        return None

    dsr = data.get("dsr") or data.get("DSR") or {}
    ds_list = dsr.get("DS") or []
    if not ds_list:
        return None

    ph = ds_list[0].get("PH") or []
    if not ph:
        return None

    dm0 = ph[0].get("DM0")
    if dm0 is None:
        return None

    return dm0


def score_candidate(df: pd.DataFrame) -> int:
    """
    Dá uma nota para um DF "parecer PLD horário":
      - DIA parseável YYYY-MM-DD
      - HORA 1..24 (ou 0..23)
      - SUBMERCADO parece string curta
      - PLD numérico razoável
    """
    if df.empty:
        return 0

    score = 0

    # DIA
    dia = df.iloc[:, 0].astype(str).str.slice(0, 10)
    ok_dia = dia.str.match(r"^\d{4}-\d{2}-\d{2}$", na=False).mean()
    score += int(ok_dia * 40)

    # HORA
    hora = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    ok_h = ((hora >= 0) & (hora <= 24)).mean()
    score += int(ok_h * 30)

    # SUBMERCADO
    sub = df.iloc[:, 2].astype(str).str.lower()
    # costuma ter "norte", "nordeste", "sul", "sudeste" ou siglas
    hits = sub.str.contains("norte|nord|sul|sud|se|co|ne|n\\b|s\\b", regex=True, na=False).mean()
    score += int(hits * 15)

    # PLD
    pld = pd.to_numeric(df.iloc[:, 3], errors="coerce")
    ok_pld = (pld.notna()).mean()
    score += int(ok_pld * 15)

    return score


def try_build_df_from_dm0(dm0: List[Dict[str, Any]]) -> Optional[pd.DataFrame]:
    # cada linha tem "C": [col0, col1, ...]
    rows = [r.get("C") or [] for r in dm0]
    if not rows:
        return None

    # precisamos de pelo menos 4 colunas
    rows4 = [r[:4] for r in rows if len(r) >= 4]
    if not rows4:
        return None

    df = pd.DataFrame(rows4, columns=["DIA", "HORA", "SUBMERCADO", "PLD_HORA"])
    return df


def clean_pld_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["DIA"] = df["DIA"].astype(str).str.slice(0, 10)
    df["HORA"] = pd.to_numeric(df["HORA"], errors="coerce").fillna(-1).astype(int)
    df["PLD_HORA"] = pd.to_numeric(df["PLD_HORA"], errors="coerce")
    df["SUBMERCADO"] = df["SUBMERCADO"].astype(str).str.strip().str.lower()

    df = df.dropna(subset=["DIA", "PLD_HORA"])
    df = df[df["DIA"].str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)]
    df = df[df["HORA"].between(0, 24)]
    df = df[df["SUBMERCADO"].str.len().between(1, 30)]

    # mantém ano atual e anterior
    y = date.today().year
    y0 = y - 1
    df = df[df["DIA"].str.startswith(str(y0)) | df["DIA"].str.startswith(str(y))].copy()

    return df[["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]]


def looks_like_querydata(url: str) -> bool:
    return "querydata" in url and "/public/reports/" in url


def main() -> None:
    captured_requests: List[Dict[str, Any]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()

        def on_request(req):
            try:
                if req.method == "POST" and looks_like_querydata(req.url):
                    post = req.post_data or ""
                    # só guarda requests "reais" (com SemanticQuery)
                    if post and "SemanticQueryDataShapeCommand" in post:
                        captured_requests.append({
                            "url": req.url,
                            "headers": dict(req.headers),
                            "postData": post,
                        })
            except Exception:
                pass

        page.on("request", on_request)

        page.goto(POWERBI_VIEW_URL, wait_until="networkidle", timeout=120000)

        # dá um tempinho pra capturar mais chamadas
        page.wait_for_timeout(8000)

        if not captured_requests:
            raise RuntimeError("Não capturei nenhuma chamada querydata do Power BI.")

        print("Captured querydata requests:", len(captured_requests))

        # vamos testar as primeiras N chamadas (pra não ficar lento)
        N = min(len(captured_requests), 40)

        best_score = -1
        best_df: Optional[pd.DataFrame] = None
        best_i = None

        for i in range(N):
            item = captured_requests[i]
            hdr = item["headers"]
            hdr["accept"] = "application/json, text/plain, */*"

            resp = ctx.request.post(item["url"], headers=hdr, data=item["postData"], timeout=180000)
            if resp.status != 200:
                continue

            j = resp.json()
            dm0 = extract_dm0(j)
            if dm0 is None:
                continue

            df0 = try_build_df_from_dm0(dm0)
            if df0 is None:
                continue

            sc = score_candidate(df0)
            if sc > best_score:
                best_score = sc
                best_i = i
                best_df = df0

        if best_df is None or best_score < 50:
            raise RuntimeError(
                f"Não consegui identificar qual querydata contém PLD horário. best_score={best_score}"
            )

        print(f"Best querydata index: {best_i} | best_score: {best_score}")

        df = clean_pld_df(best_df)
        print("linhas após limpeza:", len(df))
        if df.empty:
            # se ficou vazio, imprime amostra para debug (primeiras linhas cruas)
            print("Amostra crua (head):")
            print(best_df.head(5).to_string(index=False))
            raise RuntimeError("DataFrame vazio após limpeza (mesmo no melhor candidato).")

        write_sqlite(df)

        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()

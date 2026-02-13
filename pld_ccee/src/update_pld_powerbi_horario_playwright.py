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


def looks_like_querydata(url: str) -> bool:
    return "/public/reports/querydata" in url and "synchronous=true" in url


def extract_rows_any_dm(resp_json: Dict[str, Any]) -> List[List[Any]]:
    """
    Retorna rows (listas) de qualquer matriz DM* que existir.
    Em alguns relatórios a matriz pode vir como DM0, DM1, etc.
    """
    results = resp_json.get("results") or resp_json.get("Results") or []
    if not results:
        return []

    data = None
    for item in results:
        data = (item.get("result") or item.get("Result") or {}).get("data")
        if data:
            break
    if not data:
        return []

    dsr = data.get("dsr") or data.get("DSR") or {}
    ds_list = dsr.get("DS") or []
    if not ds_list:
        return []

    ph = ds_list[0].get("PH") or []
    if not ph:
        return []

    # pega o primeiro PH e procura qualquer chave DM*
    ph0 = ph[0]
    dm_keys = [k for k in ph0.keys() if re.match(r"^DM\d+$", str(k))]
    dm_keys.sort()

    all_rows: List[List[Any]] = []
    for k in dm_keys:
        mat = ph0.get(k)
        if isinstance(mat, list) and mat:
            for r in mat:
                c = r.get("C") if isinstance(r, dict) else None
                if isinstance(c, list):
                    all_rows.append(c)

    return all_rows


def score_rows_as_pld(rows: List[List[Any]]) -> int:
    """
    Dá uma nota para rows parecerem [DIA, HORA, SUBMERCADO, PLD].
    A gente avalia as 4 primeiras colunas.
    """
    rows4 = [r[:4] for r in rows if isinstance(r, list) and len(r) >= 4]
    if not rows4:
        return 0

    df = pd.DataFrame(rows4, columns=["c0", "c1", "c2", "c3"])

    # DIA (YYYY-MM-DD)
    dia = df["c0"].astype(str).str.slice(0, 10)
    ok_dia = dia.str.match(r"^\d{4}-\d{2}-\d{2}$", na=False).mean()

    # HORA 0..24
    hora = pd.to_numeric(df["c1"], errors="coerce")
    ok_h = ((hora >= 0) & (hora <= 24)).mean()

    # SUBMERCADO string
    sub = df["c2"].astype(str).str.lower()
    hits = sub.str.contains("norte|nord|sul|sud|se/co|sudeste|centro|co", regex=True, na=False).mean()

    # PLD numérico
    pld = pd.to_numeric(df["c3"], errors="coerce")
    ok_pld = pld.notna().mean()

    score = int(ok_dia * 40) + int(ok_h * 30) + int(hits * 15) + int(ok_pld * 15)
    return score


def build_clean_df(rows: List[List[Any]]) -> pd.DataFrame:
    rows4 = [r[:4] for r in rows if isinstance(r, list) and len(r) >= 4]
    df = pd.DataFrame(rows4, columns=["DIA", "HORA", "SUBMERCADO", "PLD_HORA"])

    df["DIA"] = df["DIA"].astype(str).str.slice(0, 10)
    df["HORA"] = pd.to_numeric(df["HORA"], errors="coerce").fillna(-1).astype(int)
    df["PLD_HORA"] = pd.to_numeric(df["PLD_HORA"], errors="coerce")
    df["SUBMERCADO"] = df["SUBMERCADO"].astype(str).str.strip().str.lower()

    df = df.dropna(subset=["DIA", "PLD_HORA"])
    df = df[df["DIA"].str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)]
    df = df[df["HORA"].between(0, 24)]
    df = df[df["SUBMERCADO"].str.len().between(1, 40)]

    y = date.today().year
    y0 = y - 1
    df = df[df["DIA"].str.startswith(str(y0)) | df["DIA"].str.startswith(str(y))].copy()

    return df[["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]]


def main() -> None:
    captured_responses: List[Dict[str, Any]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()

        def on_response(resp):
            try:
                if resp.request.method == "POST" and looks_like_querydata(resp.url):
                    if resp.status == 200:
                        # tenta ler JSON; se falhar, ignora
                        j = resp.json()
                        captured_responses.append({
                            "url": resp.url,
                            "json": j
                        })
            except Exception:
                pass

        page.on("response", on_response)

        page.goto(POWERBI_VIEW_URL, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(4000)

        # força carregar o visual do "histórico do preço horário"
        # (se já estiver selecionado, não tem problema)
        try:
            page.locator("text=histórico do preço horário").first.click(timeout=5000)
        except Exception:
            pass

        # dá tempo para Power BI disparar querydata
        page.wait_for_timeout(12000)

        print("Captured querydata responses:", len(captured_responses))
        if not captured_responses:
            raise RuntimeError("Não capturei respostas querydata (status 200).")

        best_score = -1
        best_rows: Optional[List[List[Any]]] = None
        best_idx = None

        for i, item in enumerate(captured_responses):
            rows = extract_rows_any_dm(item["json"])
            sc = score_rows_as_pld(rows)
            if sc > best_score:
                best_score = sc
                best_rows = rows
                best_idx = i

        print("best_score:", best_score, "| best_idx:", best_idx)
        if best_rows is None or best_score < 50:
            # debug leve: mostra as chaves do primeiro json capturado
            keys = list((captured_responses[0]["json"] or {}).keys())
            raise RuntimeError(
                f"Não identifiquei PLD horário em nenhuma resposta querydata. "
                f"best_score={best_score}. top-level keys (primeiro json)={keys}"
            )

        df = build_clean_df(best_rows)
        print("linhas após limpeza:", len(df))
        if df.empty:
            # debug: mostra amostra crua
            sample = [r[:6] for r in (best_rows[:5] if best_rows else [])]
            print("Amostra crua (primeiras 5 linhas):", sample)
            raise RuntimeError("DataFrame vazio após limpeza, mesmo no melhor candidato.")

        write_sqlite(df)

        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()

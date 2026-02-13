import os
import re
import sqlite3
from datetime import date
from typing import Any, Dict, List, Optional, Tuple, Union

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


# -----------------------------
# RECUPERA "C": [...] de QUALQUER lugar do JSON
# -----------------------------
def collect_C_rows(obj: Any, out: List[List[Any]]) -> None:
    if isinstance(obj, dict):
        if "C" in obj and isinstance(obj["C"], list):
            out.append(obj["C"])
        for v in obj.values():
            collect_C_rows(v, out)
    elif isinstance(obj, list):
        for it in obj:
            collect_C_rows(it, out)


def to_datetime_series(x: pd.Series) -> pd.Series:
    """
    Converte DIA que pode vir como:
      - 'YYYY-MM-DD' ou 'YYYY-MM-DDTHH:MM:SS'
      - excel serial (ex: 45200)
      - unix epoch em ms (1.7e12) ou s (1.7e9)
    Retorna string YYYY-MM-DD (ou NaN).
    """
    s = x.copy()

    # tenta string primeiro
    s_str = s.astype(str)
    s_str = s_str.str.strip()

    # caso ISO com hora: pega os 10 primeiros
    cand = s_str.str.slice(0, 10)
    mask_iso = cand.str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)
    out = pd.Series([None] * len(s), index=s.index, dtype="object")
    out.loc[mask_iso] = cand.loc[mask_iso]

    # o resto: tenta numérico
    mask_rest = ~mask_iso
    if mask_rest.any():
        num = pd.to_numeric(s.loc[mask_rest], errors="coerce")

        # excel serial típico ~ 40000-60000
        mask_excel = num.between(30000, 70000)
        if mask_excel.any():
            dt = pd.to_datetime(num.loc[mask_excel], unit="D", origin="1899-12-30", errors="coerce")
            out.loc[mask_rest[mask_rest].index[mask_excel]] = dt.dt.strftime("%Y-%m-%d").values

        # unix ms ~ 1e12-2e12
        mask_ms = num.between(1e12, 2e12)
        if mask_ms.any():
            dt = pd.to_datetime(num.loc[mask_ms], unit="ms", errors="coerce")
            out.loc[mask_rest[mask_rest].index[mask_ms]] = dt.dt.strftime("%Y-%m-%d").values

        # unix s ~ 1e9-2e9
        mask_s = num.between(1e9, 2e9)
        if mask_s.any():
            dt = pd.to_datetime(num.loc[mask_s], unit="s", errors="coerce")
            out.loc[mask_rest[mask_rest].index[mask_s]] = dt.dt.strftime("%Y-%m-%d").values

    return out


def score_rows_as_pld(rows: List[List[Any]]) -> int:
    """
    Score para rows parecerem [DIA, HORA, SUBMERCADO, PLD].
    (avaliamos 4 primeiras colunas)
    """
    rows4 = [r[:4] for r in rows if isinstance(r, list) and len(r) >= 4]
    if not rows4:
        return 0

    df = pd.DataFrame(rows4, columns=["c0", "c1", "c2", "c3"])

    # DIA ok?
    dia = to_datetime_series(df["c0"])
    ok_dia = dia.notna().mean()

    # HORA 0..24
    hora = pd.to_numeric(df["c1"], errors="coerce")
    ok_h = ((hora >= 0) & (hora <= 24)).mean()

    # SUBMERCADO (string com pistas)
    sub = df["c2"].astype(str).str.lower()
    hits = sub.str.contains("norte|nord|sul|sud|se/co|sudeste|centro|co|ne\\b|se\\b", regex=True, na=False).mean()

    # PLD numérico
    pld = pd.to_numeric(df["c3"], errors="coerce")
    ok_pld = pld.notna().mean()

    return int(ok_dia * 40) + int(ok_h * 30) + int(hits * 15) + int(ok_pld * 15)


def build_clean_df(rows: List[List[Any]]) -> pd.DataFrame:
    rows4 = [r[:4] for r in rows if isinstance(r, list) and len(r) >= 4]
    df = pd.DataFrame(rows4, columns=["DIA", "HORA", "SUBMERCADO", "PLD_HORA"])

    df["DIA"] = to_datetime_series(df["DIA"])
    df["HORA"] = pd.to_numeric(df["HORA"], errors="coerce").fillna(-1).astype(int)
    df["PLD_HORA"] = pd.to_numeric(df["PLD_HORA"], errors="coerce")
    df["SUBMERCADO"] = df["SUBMERCADO"].astype(str).str.strip().str.lower()

    df = df.dropna(subset=["DIA", "PLD_HORA"])
    df = df[df["HORA"].between(0, 24)]
    df = df[df["SUBMERCADO"].str.len().between(1, 50)]

    # mantém ano atual e anterior
    y = date.today().year
    y0 = y - 1
    df = df[df["DIA"].str.startswith(str(y0)) | df["DIA"].str.startswith(str(y))].copy()

    return df[["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]]


def main() -> None:
    captured_jsons: List[Dict[str, Any]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()

        def on_response(resp):
            try:
                if resp.request.method == "POST" and looks_like_querydata(resp.url) and resp.status == 200:
                    captured_jsons.append(resp.json())
            except Exception:
                pass

        page.on("response", on_response)

        page.goto(POWERBI_VIEW_URL, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(4000)

        # tenta forçar aba do horário
        for txt in ["histórico do preço horário", "histórico do preco horario"]:
            try:
                page.locator(f"text={txt}").first.click(timeout=4000)
                break
            except Exception:
                pass

        page.wait_for_timeout(12000)

        print("Captured querydata responses:", len(captured_jsons))
        if not captured_jsons:
            raise RuntimeError("Não capturei respostas querydata (status 200).")

        best_score = -1
        best_rows: Optional[List[List[Any]]] = None
        best_idx = None

        for i, j in enumerate(captured_jsons):
            rows: List[List[Any]] = []
            collect_C_rows(j, rows)
            sc = score_rows_as_pld(rows)
            if sc > best_score:
                best_score = sc
                best_rows = rows
                best_idx = i

        print("best_score:", best_score, "| best_idx:", best_idx)

        if best_rows is None or best_score < 50:
            # debug útil: mostra amostra da melhor tentativa (primeiras linhas)
            sample = [r[:6] for r in (best_rows[:5] if best_rows else [])]
            print("Amostra C-rows (top5):", sample)
            raise RuntimeError(
                f"Não identifiquei PLD horário em nenhuma resposta querydata. best_score={best_score}"
            )

        df = build_clean_df(best_rows)
        print("linhas após limpeza:", len(df))
        if df.empty:
            sample = [r[:6] for r in (best_rows[:5] if best_rows else [])]
            print("Amostra C-rows (top5):", sample)
            raise RuntimeError("DataFrame vazio após limpeza (mesmo no melhor candidato).")

        write_sqlite(df)

        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()

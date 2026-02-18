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

    # Rebuild completo do pld_medio (igual ao seu racional original)
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
    return "/public/reports/querydata" in url


def collect_C_rows(obj: Any, out: List[List[Any]]) -> None:
    if isinstance(obj, dict):
        if "C" in obj and isinstance(obj["C"], list):
            out.append(obj["C"])
        for v in obj.values():
            collect_C_rows(v, out)
    elif isinstance(obj, list):
        for it in obj:
            collect_C_rows(it, out)


def normalize_date(v: Any) -> Optional[str]:
    s = str(v).strip()

    # ISO string
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]

    # excel serial, unix ms/s
    try:
        x = float(v)

        # excel serial típico
        if 30000 <= x <= 70000:
            dt = pd.to_datetime(x, unit="D", origin="1899-12-30", errors="coerce")
            if pd.notna(dt):
                return dt.strftime("%Y-%m-%d")

        # unix ms
        if 1e12 <= x <= 2e12:
            dt = pd.to_datetime(x, unit="ms", errors="coerce")
            if pd.notna(dt):
                return dt.strftime("%Y-%m-%d")

        # unix s
        if 1e9 <= x <= 2e9:
            dt = pd.to_datetime(x, unit="s", errors="coerce")
            if pd.notna(dt):
                return dt.strftime("%Y-%m-%d")

    except Exception:
        pass

    return None


def is_hour(v: Any) -> bool:
    try:
        x = int(float(v))
        return 0 <= x <= 24
    except Exception:
        return False


def is_submarket(v: Any) -> bool:
    s = str(v).strip().lower()
    return bool(re.search(r"(norte|nordeste|sul|sudeste|se/co|seco|centro|oeste|ne\b|se\b|co\b|n\b|s\b)", s))


def infer_columns(rows: List[List[Any]]) -> Optional[Tuple[int, int, int, int]]:
    """
    Descobre (dia, hora, sub, pld) olhando taxa de "match" por coluna.
    """
    rows = [r for r in rows if isinstance(r, list) and len(r) >= 4]
    if not rows:
        return None

    max_len = min(max(len(r) for r in rows), 25)

    stats = []
    for i in range(max_len):
        col = [r[i] for r in rows if len(r) > i]
        if not col:
            continue

        dia_rate = sum(1 for v in col if normalize_date(v) is not None) / len(col)
        hora_rate = sum(1 for v in col if is_hour(v)) / len(col)
        sub_rate  = sum(1 for v in col if is_submarket(v)) / len(col)
        num_rate  = pd.to_numeric(pd.Series(col), errors="coerce").notna().mean()

        stats.append((i, dia_rate, hora_rate, sub_rate, num_rate))

    if not stats:
        return None

    dia_i  = max(stats, key=lambda t: t[1])[0]
    hora_i = max(stats, key=lambda t: t[2])[0]
    sub_i  = max(stats, key=lambda t: t[3])[0]

    # pld: coluna numérica que não seja dia/hora/sub
    cand = [t for t in stats if t[0] not in {dia_i, hora_i, sub_i}]
    if not cand:
        return None
    pld_i = max(cand, key=lambda t: t[4])[0]

    # valida mínimos (bem permissivo)
    dia_rate  = next(t[1] for t in stats if t[0] == dia_i)
    hora_rate = next(t[2] for t in stats if t[0] == hora_i)
    sub_rate  = next(t[3] for t in stats if t[0] == sub_i)
    if dia_rate < 0.05 or hora_rate < 0.10 or sub_rate < 0.03:
        return None

    return (dia_i, hora_i, sub_i, pld_i)


def build_df(rows: List[List[Any]], idxs: Tuple[int, int, int, int]) -> pd.DataFrame:
    di, hi, si, pi = idxs
    out = []

    for r in rows:
        if not isinstance(r, list) or len(r) <= max(idxs):
            continue

        dia = normalize_date(r[di])
        if dia is None:
            continue

        try:
            hora = int(float(r[hi]))
        except Exception:
            continue

        sub = str(r[si]).strip().lower()
        pld = pd.to_numeric(pd.Series([r[pi]]), errors="coerce").iloc[0]
        if pd.isna(pld):
            continue

        out.append((dia, hora, sub, float(pld)))

    df = pd.DataFrame(out, columns=["DIA", "HORA", "SUBMERCADO", "PLD_HORA"])

    # mantém ano atual e anterior
    y = date.today().year
    y0 = y - 1
    df = df[df["DIA"].str.startswith(str(y0)) | df["DIA"].str.startswith(str(y))].copy()

    return df


def click_if_exists(page, texts: List[str], timeout_ms: int = 6000) -> None:
    for txt in texts:
        try:
            page.locator(f"text={txt}").first.click(timeout=timeout_ms)
            return
        except Exception:
            pass


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
        page.wait_for_timeout(5000)

        # Aba do horário
        click_if_exists(page, ["histórico do preço horário", "histórico do preco horario"])

        page.wait_for_timeout(6000)

        # Força o visual de hora (sem filtro de submercado)
        click_if_exists(page, ["preço médio por hora", "preco medio por hora"])

        # espera mais pra capturar mais querydatas (antes estava vindo só 4)
        page.wait_for_timeout(18000)

        print("Captured querydata responses:", len(captured_jsons))
        if not captured_jsons:
            raise RuntimeError("Não capturei respostas querydata (status 200).")

        best_len = -1
        best_idxs = None
        best_rows = None

        for j in captured_jsons:
            rows: List[List[Any]] = []
            collect_C_rows(j, rows)
            idxs = infer_columns(rows)
            if idxs is None:
                continue

            df = build_df(rows, idxs)
            if len(df) > best_len:
                best_len = len(df)
                best_idxs = idxs
                best_rows = rows

        if best_idxs is None or best_len <= 0:
            # debug útil
            rows0: List[List[Any]] = []
            collect_C_rows(captured_jsons[-1], rows0)
            print("Amostra C-rows (top5) do último JSON:", [r[:10] for r in rows0[:5]])
            raise RuntimeError("Não consegui identificar colunas DIA/HORA/SUB/PLD em nenhuma resposta.")

        print("Melhor candidato: linhas=", best_len, "| idxs(dia,hora,sub,pld)=", best_idxs)

        df_final = build_df(best_rows, best_idxs)
        print("linhas finais:", len(df_final))
        if df_final.empty:
            raise RuntimeError("DataFrame vazio no final.")

        write_sqlite(df_final)

        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()

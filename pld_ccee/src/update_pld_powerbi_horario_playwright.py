import os
import re
import json
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
    return "/public/reports/querydata" in url


def click_text(scope, texts, timeout=8000) -> bool:
    for t in texts:
        try:
            scope.locator(f"text={t}").first.click(timeout=timeout)
            return True
        except Exception:
            pass
    return False


def find_frame_with_any_text(page, texts: List[str], timeout_ms: int = 120000):
    page.wait_for_timeout(2000)
    end = pd.Timestamp.now(tz="UTC").value // 10**6 + timeout_ms
    while (pd.Timestamp.now(tz="UTC").value // 10**6) < end:
        for fr in page.frames:
            try:
                if fr.is_detached():
                    continue
                for t in texts:
                    if fr.locator(f"text={t}").count() > 0:
                        return fr
            except Exception:
                continue
        page.wait_for_timeout(1500)
    return None


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
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    try:
        x = float(v)
        if 30000 <= x <= 70000:
            dt = pd.to_datetime(x, unit="D", origin="1899-12-30", errors="coerce")
            if pd.notna(dt):
                return dt.strftime("%Y-%m-%d")
        if 1e12 <= x <= 2e12:
            dt = pd.to_datetime(x, unit="ms", errors="coerce")
            if pd.notna(dt):
                return dt.strftime("%Y-%m-%d")
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
    return bool(re.search(r"(norte|nordeste|sul|sudeste|se/co|seco|centro|oeste|ne\b|se\b|co\b)", s))


def infer_columns(rows: List[List[Any]]) -> Optional[Tuple[int, int, int, int]]:
    rows = [r for r in rows if isinstance(r, list) and len(r) >= 4]
    if not rows:
        return None

    max_len = min(max(len(r) for r in rows), 30)
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
    cand = [t for t in stats if t[0] not in {dia_i, hora_i, sub_i}]
    if not cand:
        return None
    pld_i = max(cand, key=lambda t: t[4])[0]

    # mínimos um pouco mais fortes (pra não pegar “cards”)
    dia_rate  = next(t[1] for t in stats if t[0] == dia_i)
    hora_rate = next(t[2] for t in stats if t[0] == hora_i)
    sub_rate  = next(t[3] for t in stats if t[0] == sub_i)
    if dia_rate < 0.15 or hora_rate < 0.15 or sub_rate < 0.08:
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
    y = date.today().year
    y0 = y - 1
    df = df[df["DIA"].str.startswith(str(y0)) | df["DIA"].str.startswith(str(y))].copy()
    return df


def main() -> None:
    # vamos guardar respostas com (bytes_len, json)
    captured: List[Tuple[int, Dict[str, Any]]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1200},
            device_scale_factor=1,
        )
        page = ctx.new_page()

        def on_response(resp):
            try:
                if resp.request.method == "POST" and looks_like_querydata(resp.url) and resp.status == 200:
                    # pega tamanho bruto (heurística: a query do dataset é grande)
                    body = resp.body()
                    if body:
                        j = resp.json()
                        captured.append((len(body), j))
            except Exception:
                pass

        page.on("response", on_response)

        page.goto(POWERBI_VIEW_URL, wait_until="domcontentloaded", timeout=120000)

        frame = find_frame_with_any_text(page, ["histórico do preço horário", "histórico do preco horario"])
        if frame is None:
            raise RuntimeError("Não achei o iframe do report (texto da aba não apareceu).")

        # Aba do horário + visual por hora
        click_text(frame, ["histórico do preço horário", "histórico do preco horario"])
        frame.wait_for_timeout(5000)
        click_text(frame, ["preço médio por hora", "preco medio por hora"])
        frame.wait_for_timeout(5000)

        # Scroll pra “acordar” visuais (lazy render)
        try:
            frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass

        frame.wait_for_timeout(4000)

        # Scroll de volta pro topo (às vezes o visual está em cima)
        try:
            frame.evaluate("window.scrollTo(0, 0)")
        except Exception:
            try:
                page.evaluate("window.scrollTo(0, 0)")
            except Exception:
                pass

        frame.wait_for_timeout(8000)

        print("Captured querydata responses:", len(captured))
        if not captured:
            raise RuntimeError("Não capturei nenhuma resposta querydata com body.")

        # pega as maiores respostas primeiro (mais chance de ser dataset)
        captured.sort(key=lambda t: t[0], reverse=True)

        best_df = None
        best_len = 0

        for size, j in captured[:15]:
            rows: List[List[Any]] = []
            collect_C_rows(j, rows)
            idxs = infer_columns(rows)
            if idxs is None:
                continue
            df = build_df(rows, idxs)
            if len(df) > best_len:
                best_len = len(df)
                best_df = df

        if best_df is None or best_df.empty:
            # debug: mostra amostra das maiores respostas
            top_sizes = [s for s, _ in captured[:5]]
            rows0: List[List[Any]] = []
            collect_C_rows(captured[0][1], rows0)
            print("Top5 response sizes:", top_sizes)
            print("Amostra C-rows (top5) do MAIOR response:", [r[:10] for r in rows0[:5]])
            raise RuntimeError("Não consegui extrair dataset de PLD horário a partir de querydata (só agregados).")

        print("Dataset extraído. Linhas:", best_len)
        write_sqlite(best_df[["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]])

        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()

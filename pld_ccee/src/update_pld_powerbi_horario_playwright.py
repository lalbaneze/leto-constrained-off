import os
import re
import sqlite3
from datetime import date
from typing import Optional

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

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
        raise RuntimeError("CSV exportado veio vazio após limpeza — nada para gravar.")

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


def click_text(page, texts, timeout=6000) -> None:
    for t in texts:
        try:
            page.locator(f"text={t}").first.click(timeout=timeout)
            return
        except Exception:
            pass


def normalize_decimal_series(s: pd.Series) -> pd.Series:
    raw = s.astype(str).str.strip()
    # se tiver vírgula decimal, remove separador de milhar "." e troca "," por "."
    mask_pt = raw.str.contains(",", na=False)
    raw.loc[mask_pt] = raw.loc[mask_pt].str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    return pd.to_numeric(raw, errors="coerce")


def find_col(df: pd.DataFrame, *cands: str) -> Optional[str]:
    cols = {c.strip().lower(): c for c in df.columns}
    for cand in cands:
        if cand.lower() in cols:
            return cols[cand.lower()]
    return None


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()

        page.goto(POWERBI_VIEW_URL, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(5000)

        # 1) Aba "histórico do preço horário"
        click_text(page, ["histórico do preço horário", "histórico do preco horario"])
        page.wait_for_timeout(5000)

        # 2) Força "preço médio por hora" (se existir)
        click_text(page, ["preço médio por hora", "preco medio por hora"])
        page.wait_for_timeout(5000)

        # 3) Tenta abrir menu "..." do visual e exportar dados
        #    (Power BI varia: "More options", "Mais opções", etc.)
        more_selectors = [
            '[aria-label="More options"]',
            '[aria-label="Mais opções"]',
            '[title="More options"]',
            '[title="Mais opções"]',
            'button:has-text("...")',
        ]

        clicked_more = False
        for sel in more_selectors:
            try:
                page.locator(sel).first.click(timeout=4000)
                clicked_more = True
                break
            except Exception:
                continue

        if not clicked_more:
            raise RuntimeError(
                "Não consegui achar o botão de 'Mais opções' (…)\n"
                "Dica: abra o report no navegador, passe o mouse no visual e veja se aparece o menu (…) no canto."
            )

        # 4) Clica em "Exportar dados" / "Export data"
        export_texts = ["Exportar dados", "Export data", "Exportar", "Export"]
        export_clicked = False
        for t in export_texts:
            try:
                page.locator(f'text={t}').first.click(timeout=6000)
                export_clicked = True
                break
            except Exception:
                continue

        if not export_clicked:
            raise RuntimeError("Abriu o menu (…), mas não achei 'Exportar dados'.")

        # 5) Algumas vezes aparece modal com opções (dados resumidos/subjacentes).
        #    Tenta escolher "Dados subjacentes"/"Underlying data" se existir.
        click_text(page, ["Dados subjacentes", "Underlying data", "Dados resumidos", "Summarized data"], timeout=3000)

        # 6) Botão final "Exportar" / "Export"
        #    Aqui a gente espera o download.
        with page.expect_download(timeout=60000) as dl_info:
            click_text(page, ["Exportar", "Export"], timeout=8000)
        download = dl_info.value

        out_path = os.path.join("/tmp", "pld_powerbi_export.csv")
        download.save_as(out_path)
        print("CSV baixado em:", out_path)

        # 7) Lê e normaliza CSV
        #    Detecta separador ; ou ,
        with open(out_path, "rb") as f:
            sample = f.read(4096).decode("utf-8", errors="ignore")
        sep = ";" if sample.count(";") > sample.count(",") else ","

        df = pd.read_csv(out_path, sep=sep)
        df.columns = [c.strip() for c in df.columns]
        print("CSV columns:", df.columns.tolist())

        # tenta mapear colunas comuns
        col_dia = find_col(df, "DIA", "Data", "Date", "Dia")
        col_hora = find_col(df, "HORA", "Hora", "Hour", "HORA_INICIO", "Hora Início", "Hora Inicio")
        col_sub = find_col(df, "SUBMERCADO", "Submercado", "SBM", "Submercado (SBM)")
        col_val = find_col(df, "PLD", "PLD_HORA", "PLD HORA", "Preço", "Preco", "Valor", "Value", "Price")

        if not col_dia or not col_val:
            raise RuntimeError(
                f"Não consegui mapear colunas no CSV. Achei DIA={col_dia}, VAL={col_val}, HORA={col_hora}, SUB={col_sub}."
            )

        out = pd.DataFrame()
        out["DIA"] = pd.to_datetime(df[col_dia], errors="coerce").dt.strftime("%Y-%m-%d")

        # se não tiver hora, assume 0 (ainda preserva média mensal corretamente)
        if col_hora:
            out["HORA"] = pd.to_numeric(df[col_hora], errors="coerce").fillna(0).astype(int)
        else:
            out["HORA"] = 0

        out["SUBMERCADO"] = (df[col_sub].astype(str).str.strip().str.lower() if col_sub else "avg")
        out["PLD_HORA"] = normalize_decimal_series(df[col_val])

        out = out.dropna(subset=["DIA", "PLD_HORA"])
        out = out[out["HORA"].between(0, 24)]

        # mantém ano atual e anterior
        y = date.today().year
        y0 = y - 1
        out = out[out["DIA"].str.startswith(str(y0)) | out["DIA"].str.startswith(str(y))].copy()

        print("linhas após limpeza:", len(out))
        if out.empty:
            raise RuntimeError("CSV exportado não tem linhas válidas após limpeza.")

        write_sqlite(out[["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]])

        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()

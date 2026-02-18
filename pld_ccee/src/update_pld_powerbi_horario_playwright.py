import os
import re
import sqlite3
from datetime import date
from typing import Optional

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
    con.close()

    print("OK ✅")


def click_text(scope, texts, timeout=6000) -> bool:
    for t in texts:
        try:
            scope.locator(f"text={t}").first.click(timeout=timeout)
            return True
        except Exception:
            pass
    return False


def normalize_decimal_series(s: pd.Series) -> pd.Series:
    raw = s.astype(str).str.strip()
    mask_pt = raw.str.contains(",", na=False)
    raw.loc[mask_pt] = raw.loc[mask_pt].str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    return pd.to_numeric(raw, errors="coerce")


def find_frame_with_text(page, needle: str, timeout_ms: int = 120000):
    """
    Espera e retorna o frame que contém o texto 'needle'.
    """
    page.wait_for_timeout(3000)
    deadline = pd.Timestamp.utcnow().value // 10**6 + timeout_ms

    while (pd.Timestamp.utcnow().value // 10**6) < deadline:
        for fr in page.frames:
            try:
                if fr.is_detached():
                    continue
                # procura texto dentro do frame
                if fr.locator(f"text={needle}").count() > 0:
                    return fr
            except Exception:
                continue
        page.wait_for_timeout(1500)

    return None


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()

        page.goto(POWERBI_VIEW_URL, wait_until="domcontentloaded", timeout=120000)

        # 🔎 acha o frame do report
        frame = find_frame_with_text(page, "histórico do preço horário")
        if frame is None:
            # tenta sem acento
            frame = find_frame_with_text(page, "historico do preco horario")
        if frame is None:
            raise RuntimeError(
                "Não achei o frame do report (texto da aba não aparece). "
                "Isso geralmente indica que o conteúdo ainda está carregando dentro de iframe diferente."
            )

        # 1) Aba do horário
        ok = click_text(frame, ["histórico do preço horário", "histórico do preco horario"])
        frame.wait_for_timeout(4000)

        # 2) Força visual “preço médio por hora”
        click_text(frame, ["preço médio por hora", "preco medio por hora"])
        frame.wait_for_timeout(5000)

        # 3) Hover em algum visual container pra aparecer o menu
        # (esses seletores variam; usamos vários)
        hover_selectors = [
            "div.visualContainer",
            "div[role='presentation']",
            "svg",
            "canvas",
        ]
        hovered = False
        for sel in hover_selectors:
            try:
                loc = frame.locator(sel).first
                if loc.count() > 0:
                    loc.hover(timeout=5000)
                    hovered = True
                    break
            except Exception:
                continue

        frame.wait_for_timeout(1500)

        # 4) Botão de “Mais opções” / “More options” (…)
        more_selectors = [
            "button[aria-label*='More options']",
            "button[aria-label*='Mais opções']",
            "button[title*='More options']",
            "button[title*='Mais opções']",
            "[aria-label='More options']",
            "[aria-label='Mais opções']",
        ]

        more_btn = None
        for sel in more_selectors:
            try:
                loc = frame.locator(sel).first
                if loc.count() > 0:
                    more_btn = loc
                    break
            except Exception:
                continue

        if more_btn is None:
            raise RuntimeError(
                "Não consegui achar o botão de 'Mais opções' (…).\n"
                "Isso pode acontecer por 2 motivos:\n"
                "1) o menu só aparece em hover e o visual não foi detectado (DOM diferente), ou\n"
                "2) o report embed está com EXPORT desabilitado (bem comum no app.powerbi.com/view).\n"
                "Se for (2), não dá pra exportar via UI e precisamos voltar para extração por querydata."
            )

        more_btn.click(timeout=8000)

        # 5) Exportar dados
        export_clicked = click_text(frame, ["Exportar dados", "Export data", "Exportar", "Export"], timeout=8000)
        if not export_clicked:
            raise RuntimeError(
                "Abri o menu (…), mas não achei 'Exportar dados'. "
                "Provavelmente o export está desabilitado nesse report embed."
            )

        # 6) Modal opções (se aparecer)
        click_text(frame, ["Dados subjacentes", "Underlying data", "Dados resumidos", "Summarized data"], timeout=3000)

        # 7) Espera download no contexto da página (não do frame)
        with page.expect_download(timeout=60000) as dl_info:
            clicked = click_text(frame, ["Exportar", "Export"], timeout=10000)
            if not clicked:
                raise RuntimeError("Não achei o botão final 'Exportar' no modal.")

        download = dl_info.value
        out_path = os.path.join("/tmp", "pld_powerbi_export.csv")
        download.save_as(out_path)
        print("CSV baixado em:", out_path)

        # 8) Lê CSV (detecta sep)
        with open(out_path, "rb") as f:
            sample = f.read(4096).decode("utf-8", errors="ignore")
        sep = ";" if sample.count(";") > sample.count(",") else ","

        df = pd.read_csv(out_path, sep=sep)
        df.columns = [c.strip() for c in df.columns]
        print("CSV columns:", df.columns.tolist())

        # Mapeia colunas
        cols_lower = {c.lower(): c for c in df.columns}
        def pick(*names):
            for n in names:
                if n.lower() in cols_lower:
                    return cols_lower[n.lower()]
            return None

        col_dia = pick("DIA", "Data", "Date", "Dia")
        col_hora = pick("HORA", "Hora", "Hour", "HORA_INICIO", "Hora Início", "Hora Inicio")
        col_sub = pick("SUBMERCADO", "Submercado", "SBM", "Submercado (SBM)")
        col_val = pick("PLD", "PLD_HORA", "PLD HORA", "Preço", "Preco", "Valor", "Value", "Price")

        if not col_dia or not col_val:
            raise RuntimeError(f"Não consegui mapear colunas no CSV. DIA={col_dia}, VAL={col_val}, HORA={col_hora}, SUB={col_sub}")

        out = pd.DataFrame()
        out["DIA"] = pd.to_datetime(df[col_dia], errors="coerce").dt.strftime("%Y-%m-%d")
        out["HORA"] = pd.to_numeric(df[col_hora], errors="coerce").fillna(0).astype(int) if col_hora else 0
        out["SUBMERCADO"] = df[col_sub].astype(str).str.strip().str.lower() if col_sub else "avg"
        out["PLD_HORA"] = normalize_decimal_series(df[col_val])

        out = out.dropna(subset=["DIA", "PLD_HORA"])
        out = out[out["HORA"].between(0, 24)]

        y = date.today().year
        y0 = y - 1
        out = out[out["DIA"].str.startswith(str(y0)) | out["DIA"].str.startswith(str(y))].copy()

        print("linhas após limpeza:", len(out))
        write_sqlite(out[["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]])

        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()

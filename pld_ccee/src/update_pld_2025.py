# pld_ccee/src/update_pld_2025.py
import io
import os
import re
import sqlite3
from datetime import date

import pandas as pd
import requests

DATASET_PAGE = "https://dadosabertos.ccee.org.br/dataset/pld_horario"

# Base = .../pld_ccee
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "pld_ccee.sqlite")

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ------------------------------------------------------------
# Resources a atualizar: ano atual e anterior
# ------------------------------------------------------------
def years_to_update():
    y = date.today().year
    return [y - 1, y]


# ------------------------------------------------------------
# HTML helpers (sem depender da API CKAN, que às vezes dá 403)
# ------------------------------------------------------------
def _http_get_text(url: str, timeout: int = 60) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return r.text


def find_resource_page_url_for_year(year: int) -> str:
    """
    Encontra o link da página do resource do ano (ex: .../resource/<uuid>)
    procurando por 'pld_horario_<ano>' na página do dataset.
    """
    html = _http_get_text(DATASET_PAGE, timeout=60)

    # Exemplo típico na página:
    # href="/dataset/pld_horario/resource/<uuid>" ...>pld_horario_2026</a>
    pattern = re.compile(
        rf'href="(/dataset/pld_horario/resource/[a-f0-9-]+)".*?>\s*pld_horario_{year}\s*<',
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(html)
    if not m:
        # fallback: procura pelo texto e pega o href mais próximo
        # (menos elegante, mas ajuda se o HTML mudar um pouco)
        if f"pld_horario_{year}".lower() not in html.lower():
            raise RuntimeError(
                f"Não achei 'pld_horario_{year}' na página do dataset. "
                f"Pode ser que o resource do ano ainda não exista."
            )

        hrefs = re.findall(r'href="(/dataset/pld_horario/resource/[a-f0-9-]+)"', html, flags=re.I)
        if not hrefs:
            raise RuntimeError("Não encontrei links de resource na página do dataset.")
        # pega o primeiro e deixa o erro mais explícito
        raise RuntimeError(
            f"Encontrei o texto 'pld_horario_{year}', mas não consegui extrair o href do resource. "
            f"Provável mudança no HTML."
        )

    return "https://dadosabertos.ccee.org.br" + m.group(1)


def extract_direct_download_url(resource_page_url: str) -> str:
    """
    Abre a página do resource e extrai o link direto pda-download.../content
    (é o link que aparece em 'URL:' e também em 'Baixar recurso').
    """
    html = _http_get_text(resource_page_url, timeout=60)

    # A página costuma ter:
    # URL: https://pda-download.ccee.org.br/<token>/content
    m = re.search(r"(https://pda-download\.ccee\.org\.br/[A-Za-z0-9_\-]+/content)", html)
    if not m:
        # fallback: pega qualquer link pda-download e tenta
        m2 = re.search(r"(https://pda-download\.ccee\.org\.br/[A-Za-z0-9_\-]+/content[^\"<\s]*)", html)
        if not m2:
            raise RuntimeError(
                "Não consegui extrair o link direto de download (pda-download.../content) "
                f"da página: {resource_page_url}"
            )
        return m2.group(1)

    return m.group(1)


def get_year_csv_url(year: int) -> str:
    resource_page = find_resource_page_url_for_year(year)
    csv_url = extract_direct_download_url(resource_page)
    return csv_url


# ------------------------------------------------------------
# SQLite schema
# ------------------------------------------------------------
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


# ------------------------------------------------------------
# Core loader
# ------------------------------------------------------------
def load_csv_to_sqlite(csv_url: str) -> None:
    print("Baixando CSV:", csv_url)

    resp = requests.get(csv_url, headers={"User-Agent": UA}, timeout=180)
    resp.raise_for_status()

    # detecta separador
    sample = resp.text[:2000]
    sep = ";" if sample.count(";") > sample.count(",") else ","

    df = pd.read_csv(io.StringIO(resp.text), sep=sep)
    df.columns = [c.strip().upper() for c in df.columns]

    print("Colunas:", df.columns.tolist())

    # colunas esperadas no dicionário do recurso:
    # MES_REFERENCIA, SUBMERCADO, DIA (DD/MM/AAAA), HORA, PLD_HORA
    required = ["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]
    for c in required:
        if c not in df.columns:
            raise RuntimeError(f"Coluna {c} não encontrada no CSV.")

    df2 = df[["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]].copy()

    # DIA vem como DD/MM/AAAA
    df2["DATA"] = pd.to_datetime(df2["DIA"].astype(str).str.strip(), dayfirst=True, errors="coerce")
    df2 = df2.dropna(subset=["DATA"])
    df2["DIA"] = df2["DATA"].dt.strftime("%Y-%m-%d")

    df2["HORA"] = pd.to_numeric(df2["HORA"], errors="coerce").fillna(0).astype(int)
    df2["SUBMERCADO"] = df2["SUBMERCADO"].astype(str).str.strip().str.lower()

    # PLD pode vir com vírgula decimal (depende do export)
    pld_raw = df2["PLD_HORA"].astype(str).str.strip()
    mask_pt = pld_raw.str.contains(",", na=False)
    pld_raw.loc[mask_pt] = (
        pld_raw.loc[mask_pt]
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    df2["PLD_HORA"] = pd.to_numeric(pld_raw, errors="coerce")

    df2 = df2[["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]].dropna()

    print("Linhas após limpeza:", len(df2))
    if df2.empty:
        print("⚠️ CSV sem dados válidos. Nada a atualizar.")
        return

    # --------------------------------------------------------
    # SQLite update (seguro)
    # --------------------------------------------------------
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    ensure_tables(con)
    cur = con.cursor()

    min_dia = df2["DIA"].min()
    max_dia = df2["DIA"].max()

    print(f"Atualizando intervalo {min_dia} → {max_dia}")

    cur.execute(
        "DELETE FROM pld_horario WHERE DIA BETWEEN ? AND ?",
        (min_dia, max_dia)
    )

    df2.to_sql("pld_horario", con, if_exists="append", index=False)

    # rebuild completo do pld_medio (a partir do histórico)
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


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main():
    for y in years_to_update():
        print(f"\n=== Atualizando PLD horário ano {y} ===")
        csv_url = get_year_csv_url(y)
        load_csv_to_sqlite(csv_url)


if __name__ == "__main__":
    main()

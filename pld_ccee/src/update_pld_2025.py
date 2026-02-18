import io
import os
import re
import sqlite3
from datetime import date
from typing import Optional, Dict

import pandas as pd
import requests

# tenta usar cloudscraper (resolve muitos 403/Cloudflare)
try:
    import cloudscraper  # type: ignore
except Exception:
    cloudscraper = None


DATASET_PAGE = "https://dadosabertos.ccee.org.br/dataset/pld_horario"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "pld_ccee.sqlite")

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 🔧 Fallback opcional: se o site bloquear listagem do dataset,
# você pode fixar aqui os resource UUIDs por ano.
# Exemplo (você já me mandou o de 2026):
# RESOURCE_ID_BY_YEAR = {2026: "3f279d6b-1069-42f7-9b0a-217b084729c4"}
RESOURCE_ID_BY_YEAR: Dict[int, str] = {}


def make_session():
    if cloudscraper is not None:
        s = cloudscraper.create_scraper()
    else:
        s = requests.Session()
    s.headers.update(
        {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Connection": "keep-alive",
        }
    )
    return s


def years_to_update():
    y = date.today().year
    return [y - 1, y]


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


def _get_text(sess, url: str, timeout: int = 60) -> str:
    r = sess.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text


def resource_page_url_from_dataset_html(html: str, year: int) -> str:
    # procura link do resource do ano
    pattern = re.compile(
        rf'href="(/dataset/pld_horario/resource/[a-f0-9-]+)".*?>\s*pld_horario_{year}\s*<',
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(html)
    if not m:
        raise RuntimeError(f"Não achei link do resource pld_horario_{year} na página do dataset.")
    return "https://dadosabertos.ccee.org.br" + m.group(1)


def extract_pda_download_url(resource_page_html: str) -> str:
    # URL direta costuma ser pda-download.../content
    m = re.search(r"(https://pda-download\.ccee\.org\.br/[A-Za-z0-9_\-]+/content)", resource_page_html)
    if not m:
        raise RuntimeError("Não achei link pda-download.../content na página do resource.")
    return m.group(1)


def get_year_csv_url(year: int, sess) -> str:
    # 1) Se você fixou resource id por ano, usa direto (sem depender da listagem do dataset)
    if year in RESOURCE_ID_BY_YEAR:
        rid = RESOURCE_ID_BY_YEAR[year]
        resource_page = f"https://dadosabertos.ccee.org.br/dataset/pld_horario/resource/{rid}"
        html_res = _get_text(sess, resource_page, timeout=60)
        return extract_pda_download_url(html_res)

    # 2) Caso contrário, tenta descobrir via página do dataset (precisa não estar bloqueada)
    html = _get_text(sess, DATASET_PAGE, timeout=60)
    res_page = resource_page_url_from_dataset_html(html, year)
    html_res = _get_text(sess, res_page, timeout=60)
    return extract_pda_download_url(html_res)


def load_csv_to_sqlite(csv_url: str, sess) -> None:
    print("Baixando CSV:", csv_url)
    resp = sess.get(csv_url, timeout=180)
    resp.raise_for_status()

    sample = resp.text[:2000]
    sep = ";" if sample.count(";") > sample.count(",") else ","

    df = pd.read_csv(io.StringIO(resp.text), sep=sep)
    df.columns = [c.strip().upper() for c in df.columns]
    print("Colunas:", df.columns.tolist())

    # Esperado no CSV do PLD horário:
    # DIA (DD/MM/AAAA), HORA, SUBMERCADO, PLD_HORA
    for c in ["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]:
        if c not in df.columns:
            raise RuntimeError(f"Coluna {c} não encontrada no CSV.")

    df2 = df[["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]].copy()

    df2["DATA"] = pd.to_datetime(df2["DIA"].astype(str).str.strip(), dayfirst=True, errors="coerce")
    df2 = df2.dropna(subset=["DATA"])
    df2["DIA"] = df2["DATA"].dt.strftime("%Y-%m-%d")

    df2["HORA"] = pd.to_numeric(df2["HORA"], errors="coerce").fillna(0).astype(int)
    df2["SUBMERCADO"] = df2["SUBMERCADO"].astype(str).str.strip().str.lower()

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

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    ensure_tables(con)
    cur = con.cursor()

    min_dia = df2["DIA"].min()
    max_dia = df2["DIA"].max()
    print(f"Atualizando intervalo {min_dia} → {max_dia}")

    cur.execute("DELETE FROM pld_horario WHERE DIA BETWEEN ? AND ?", (min_dia, max_dia))
    df2.to_sql("pld_horario", con, if_exists="append", index=False)

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


def main():
    sess = make_session()

    # garante que o DB existe e tem as tabelas
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    ensure_tables(con)
    con.close()
    print("DB_PATH (update):", DB_PATH)

    updated_any = False

    for y in years_to_update():
        print(f"\n=== Atualizando PLD horário ano {y} ===")
        try:
            csv_url = get_year_csv_url(y, sess)
        except Exception as e:
            print(f"⚠️ Não consegui obter URL do ano {y}: {e}")
            continue

        before = _count_rows(DB_PATH, "pld_horario")
        load_csv_to_sqlite(csv_url, sess)
        after = _count_rows(DB_PATH, "pld_horario")

        if after > before:
            updated_any = True

    if not updated_any:
        raise SystemExit("❌ Nenhum dado novo foi carregado (pld_horario não aumentou). Verifique download/URL.")


def _count_rows(db_path: str, table: str) -> int:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    try:
        n = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except Exception:
        n = 0
    con.close()
    return int(n)



if __name__ == "__main__":
    main()

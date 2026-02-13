import base64
import json
import os
import re
import sqlite3
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

POWERBI_VIEW_URL = os.environ.get(
    "POWERBI_VIEW_URL",
    "https://app.powerbi.com/view?r=eyJrIjoiNjk2NzUyNmEtNGZkMy00NDZhLWI4ZjgtMzEyMzhiMDA4NGRkIiwidCI6ImQ3YzNlNTA2LWVmODUtNDM4Ni04ZTU0LTJkZmNkYzgwMTdkMCJ9"
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "pld_ccee.sqlite")

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

def merge_headers(base: Dict[str, str], extra: Dict[str, str]) -> Dict[str, str]:
    h = dict(base)
    h.update(extra)
    return h

def _b64url_decode(s: str) -> bytes:
    s = s.strip()
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode("utf-8"))

def resource_key_from_r_token(view_url: str) -> str:
    m = re.search(r"[?&]r=([^&]+)", view_url)
    if not m:
        raise RuntimeError("Não achei parâmetro r= na URL do Power BI.")
    raw = _b64url_decode(m.group(1))
    payload = json.loads(raw.decode("utf-8"))
    k = str(payload.get("k") or "").strip()
    if len(k) < 4:
        raise RuntimeError(f"Não consegui extrair 'k' do r-token. Payload={payload}")
    # padrão que você já vinha usando
    return k[:4].lower()

def get_view_html(session: requests.Session, url: str) -> str:
    r = session.get(url, timeout=60)
    r.raise_for_status()
    return r.text

def extract_cluster_from_html(html: str) -> Optional[str]:
    # Preferir host -api se existir no HTML
    api = re.findall(r"https://wabi-[a-z0-9\-]+-api\.analysis\.windows\.net", html, flags=re.I)
    if api:
        return api[0]

    # Senão pega o redirect mesmo (a normalização vai corrigir)
    cands = re.findall(r"https://wabi-[a-z0-9\-]+(?:-primary-redirect)?\.analysis\.windows\.net", html, flags=re.I)
    return cands[0] if cands else None


def normalize_cluster_to_api(cluster: str) -> str:
    """
    Converte qualquer host wabi-* para o host API correto.

    Ex:
      https://wabi-brazil-south-b-primary-redirect.analysis.windows.net
    vira:
      https://wabi-brazil-south-b-api.analysis.windows.net
    """
    m = re.match(r"^https://(wabi-[a-z0-9\-]+)\.analysis\.windows\.net$", cluster, flags=re.I)
    if not m:
        # se vier algo inesperado, tenta um fallback simples
        return cluster.replace("-primary-redirect.analysis.windows.net", "-api.analysis.windows.net")

    host = m.group(1)

    # remove sufixo de redirect, se existir
    if host.endswith("-primary-redirect"):
        host = host[: -len("-primary-redirect")]

    # garante sufixo -api
    if not host.endswith("-api"):
        host = host + "-api"

    return f"https://{host}.analysis.windows.net"


def get_models_and_exploration(session: requests.Session, cluster_api: str, resource_key: str) -> Dict[str, Any]:
    url = f"{cluster_api}/public/reports/modelsAndExploration"
    r = session.get(
        url,
        params={"preferReadOnlySession": "true"},
        headers=merge_headers(
            BASE_HEADERS,
            {
                "X-PowerBI-ResourceKey": resource_key,
                "Origin": "https://app.powerbi.com",
                "Referer": POWERBI_VIEW_URL,
            },
        ),
        timeout=60,
    )
    r.raise_for_status()
    return r.json()

def get_model_id(session: requests.Session, cluster: str, resource_key: str) -> str:
    """
    Alguns reports públicos retornam 403 em modelsAndExploration.
    Então tentamos modelsAndExploration e, se falhar, usamos reportEmbedConfig.
    """

    headers = merge_headers(
        BASE_HEADERS,
        {
            "X-PowerBI-ResourceKey": resource_key,
            "Origin": "https://app.powerbi.com",
            "Referer": POWERBI_VIEW_URL,
        },
    )

    # 1) tenta modelsAndExploration
    url1 = f"{cluster}/public/reports/modelsAndExploration"
    r1 = session.get(url1, params={"preferReadOnlySession": "true"}, headers=headers, timeout=60)

    if r1.status_code == 200:
        j1 = r1.json()
        model_id = (j1.get("models") or [{}])[0].get("id")
        if model_id:
            return model_id

    print("modelsAndExploration status:", r1.status_code)

    # 2) fallback: reportEmbedConfig
    url2 = f"{cluster}/public/reports/reportEmbedConfig"
    r2 = session.get(url2, headers=headers, timeout=60)
    r2.raise_for_status()
    j2 = r2.json()

    model_id = (j2.get("models") or [{}])[0].get("id")
    if not model_id:
        raise RuntimeError(f"Não achei modelId no reportEmbedConfig. keys={list(j2.keys())}")

    return model_id

def get_conceptual_schema(session: requests.Session, cluster_api: str, resource_key: str, model_id: str) -> Dict[str, Any]:
    url = f"{cluster_api}/public/reports/conceptualschema"
    r = session.get(
        url,
        params={"modelId": model_id},
        headers=merge_headers(
            BASE_HEADERS,
            {
                "X-PowerBI-ResourceKey": resource_key,
                "Origin": "https://app.powerbi.com",
                "Referer": POWERBI_VIEW_URL,
            },
        ),
        timeout=60,
    )
    r.raise_for_status()
    return r.json()

def list_tables(schema: Dict[str, Any]) -> List[Tuple[str, List[str]]]:
    entities = schema.get("schema", {}).get("entities") or schema.get("entities") or []
    out: List[Tuple[str, List[str]]] = []
    for e in entities:
        tname = e.get("name") or ""
        cols = []
        for p in e.get("properties", []) or []:
            cname = p.get("name")
            if cname:
                cols.append(cname)
        if tname:
            out.append((tname, cols))
    return out

def find_pld_horario_table(tables: List[Tuple[str, List[str]]]) -> Tuple[str, Dict[str, str]]:
    def norm(x: str) -> str:
        return re.sub(r"[^a-z0-9_]", "", x.lower())

    best = None  # (score, table, mapping)
    for tname, cols in tables:
        nmap = {norm(c): c for c in cols}

        col_date = next((nmap[k] for k in nmap if k in ("dia", "data", "dt", "date")), None)
        col_hour = next((nmap[k] for k in nmap if k in ("hora", "hr", "hour", "horainicio", "hora_inicio")), None)
        col_sub  = next((nmap[k] for k in nmap if "submerc" in k or k in ("sbm", "submercado")), None)
        col_val  = next((nmap[k] for k in nmap if "pld" in k or "preco" in k or "valor" in k or "price" in k), None)

        score = 0
        score += 2 if col_date else 0
        score += 3 if col_hour else 0
        score += 2 if col_sub else 0
        score += 3 if col_val else 0

        tn = norm(tname)
        if "pld" in tn:
            score += 1
        if "hora" in tn or "horario" in tn:
            score += 1

        if col_date and col_hour and col_val and col_sub:
            if best is None or score > best[0]:
                best = (score, tname, {"date": col_date, "hour": col_hour, "sub": col_sub, "val": col_val})

    if best is None:
        raise RuntimeError("Não achei tabela com (data, hora, submercado, valor).")

    return best[1], best[2]

def query_table(session: requests.Session, cluster_api: str, resource_key: str, model_id: str,
                table: str, m: Dict[str, str], year: int) -> pd.DataFrame:
    url = f"{cluster_api}/public/reports/querydata?synchronous=true"

    col_date = m["date"]
    col_hour = m["hour"]
    col_sub  = m["sub"]
    col_val  = m["val"]

    selects = [
        {"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": col_date}, "Name": "DIA"},
        {"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": col_hour}, "Name": "HORA"},
        {"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": col_sub},  "Name": "SUBMERCADO"},
        {"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": col_val},  "Name": "PLD_HORA"},
    ]

    payload = {
        "version": "1.0.0",
        "queries": [{
            "Query": {
                "Commands": [{
                    "SemanticQueryDataShapeCommand": {
                        "Query": {
                            "Version": 2,
                            "From": [{"Name": "s", "Entity": table}],
                            "Select": selects,
                        },
                        "Binding": {
                            "Primary": {"Groupings": [{"Projections": list(range(len(selects)))}]},
                            "DataReduction": {"DataVolume": 3, "Primary": {"Window": {"Count": 250000}}},
                        }
                    }
                }]
            }
        }],
        "modelId": model_id
    }

    r = session.post(
        url,
        headers=merge_headers(
            BASE_HEADERS,
            {
                "X-PowerBI-ResourceKey": resource_key,
                "Origin": "https://app.powerbi.com",
                "Referer": POWERBI_VIEW_URL,
            },
        ),
        json=payload,
        timeout=180,
    )
    r.raise_for_status()
    j = r.json()

    results = j.get("results") or j.get("Results") or []
    if not results:
        raise RuntimeError(f"querydata sem results. keys={list(j.keys())}")

    data = None
    for item in results:
        data = (item.get("result") or item.get("Result") or {}).get("data")
        if data:
            break
    if not data:
        raise RuntimeError("Não achei 'data' no retorno do Power BI.")

    dsr = data.get("dsr") or data.get("DSR") or {}
    ds_list = dsr.get("DS") or []
    if not ds_list:
        raise RuntimeError("Não achei dsr.DS no retorno do Power BI.")
    ph = ds_list[0].get("PH") or []
    if not ph:
        raise RuntimeError("Não achei DS[0].PH no retorno do Power BI.")
    dm0 = ph[0].get("DM0")
    if dm0 is None:
        raise RuntimeError("Não achei PH[0].DM0 no retorno do Power BI.")

    rows = [row.get("C") or [] for row in dm0]
    df = pd.DataFrame(rows, columns=["DIA", "HORA", "SUBMERCADO", "PLD_HORA"])

    df["DIA"] = df["DIA"].astype(str).str.slice(0, 10)
    df["HORA"] = pd.to_numeric(df["HORA"], errors="coerce").fillna(0).astype(int)
    df["PLD_HORA"] = pd.to_numeric(df["PLD_HORA"], errors="coerce")
    df["SUBMERCADO"] = df["SUBMERCADO"].astype(str).str.strip().str.lower()

    df = df.dropna(subset=["DIA", "HORA", "PLD_HORA"])

    y0 = year - 1
    df = df[df["DIA"].str.startswith(str(y0)) | df["DIA"].str.startswith(str(year))].copy()
    return df

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
        raise RuntimeError("Power BI retornou 0 linhas para PLD horário.")

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

def main() -> None:
    session = requests.Session()
    session.headers.update(BASE_HEADERS)

    year = date.today().year

    resource_key = resource_key_from_r_token(POWERBI_VIEW_URL)
    print("resource_key:", resource_key)

    html = get_view_html(session, POWERBI_VIEW_URL)
    cluster = extract_cluster_from_html(html)
    if not cluster:
        raise RuntimeError("Não consegui descobrir o cluster no HTML do report.")
    print("cluster:", cluster)


    model_id = get_model_id(session, cluster, resource_key)
    print("model_id:", model_id)


    schema = get_conceptual_schema(session, cluster, resource_key, model_id)
    tables = list_tables(schema)
    print("tabelas encontradas:", len(tables))

    table, mapping = find_pld_horario_table(tables)
    print("tabela escolhida:", table)
    print("mapeamento:", mapping)

    df = query_table(session, cluster, resource_key, model_id, table, mapping, year)
    print("linhas retornadas:", len(df))

    df = df[["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]].copy()
    write_sqlite(df)

if __name__ == "__main__":
    main()

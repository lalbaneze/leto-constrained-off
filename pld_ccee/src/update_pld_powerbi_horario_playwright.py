import base64
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
    return k[:4].lower()


def extract_cluster_from_html(html: str) -> str:
    # Mantemos o primary-redirect (resolve DNS). Não forçamos -api.
    cands = re.findall(r"https://wabi-[a-z0-9\-]+(?:-primary-redirect)?\.analysis\.windows\.net", html, flags=re.I)
    if not cands:
        raise RuntimeError("Não achei cluster wabi-...analysis.windows.net no HTML.")
    return cands[0]


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

    best = None
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

        if col_date and col_hour and col_sub and col_val:
            if best is None or score > best[0]:
                best = (score, tname, {"date": col_date, "hour": col_hour, "sub": col_sub, "val": col_val})

    if best is None:
        raise RuntimeError("Não achei tabela com (data, hora, submercado, valor).")
    return best[1], best[2]


def parse_querydata_rows(resp_json: Dict[str, Any], out_cols: List[str]) -> pd.DataFrame:
    results = resp_json.get("results") or resp_json.get("Results") or []
    if not results:
        raise RuntimeError("querydata sem results.")
    data = None
    for item in results:
        data = (item.get("result") or item.get("Result") or {}).get("data")
        if data:
            break
    if not data:
        raise RuntimeError("querydata sem data.")

    dsr = data.get("dsr") or data.get("DSR") or {}
    ds_list = dsr.get("DS") or []
    if not ds_list:
        raise RuntimeError("querydata sem dsr.DS.")
    ph = ds_list[0].get("PH") or []
    if not ph:
        raise RuntimeError("querydata sem PH.")
    dm0 = ph[0].get("DM0")
    if dm0 is None:
        raise RuntimeError("querydata sem DM0.")

    rows = [row.get("C") or [] for row in dm0]
    return pd.DataFrame(rows, columns=out_cols)


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


def main() -> None:
    rk = resource_key_from_r_token(POWERBI_VIEW_URL)
    print("resource_key:", rk)

    year = date.today().year
    y0 = year - 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()

        # Abre o report (isso setta cookies/sessão)
        page.goto(POWERBI_VIEW_URL, wait_until="networkidle", timeout=120000)
        html = page.content()

        cluster = extract_cluster_from_html(html)
        print("cluster:", cluster)

        headers = {
            "X-PowerBI-ResourceKey": rk,
            "Origin": "https://app.powerbi.com",
            "Referer": POWERBI_VIEW_URL,
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
        }

        # Agora chamamos pelos cookies da sessão do browser (ctx.request)
        # 1) modelsAndExploration
        url_me = f"{cluster}/public/reports/modelsAndExploration?preferReadOnlySession=true"
        r_me = ctx.request.get(url_me, headers=headers, timeout=60000)

        if r_me.status != 200:
            # tenta reportEmbedConfig
            print("modelsAndExploration status:", r_me.status)
            url_ec = f"{cluster}/public/reports/reportEmbedConfig"
            r_ec = ctx.request.get(url_ec, headers=headers, timeout=60000)
            if r_ec.status != 200:
                raise RuntimeError(
                    f"PowerBI bloqueou endpoints mesmo via browser session. "
                    f"modelsAndExploration={r_me.status}, reportEmbedConfig={r_ec.status}"
                )
            j = r_ec.json()
        else:
            j = r_me.json()

        model_id = (j.get("models") or [{}])[0].get("id")
        if not model_id:
            raise RuntimeError("Não achei modelId no retorno do Power BI.")
        print("model_id:", model_id)

        # 2) conceptualschema
        url_schema = f"{cluster}/public/reports/conceptualschema?modelId={model_id}"
        r_sc = ctx.request.get(url_schema, headers=headers, timeout=60000)
        if r_sc.status != 200:
            raise RuntimeError(f"conceptualschema status={r_sc.status}")
        schema = r_sc.json()

        tables = list_tables(schema)
        print("tabelas encontradas:", len(tables))

        table, m = find_pld_horario_table(tables)
        print("tabela escolhida:", table)
        print("mapeamento:", m)

        # 3) querydata
        url_qd = f"{cluster}/public/reports/querydata?synchronous=true"
        selects = [
            {"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": m["date"]}, "Name": "DIA"},
            {"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": m["hour"]}, "Name": "HORA"},
            {"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": m["sub"]},  "Name": "SUBMERCADO"},
            {"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": m["val"]},  "Name": "PLD_HORA"},
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

        r_qd = ctx.request.post(url_qd, headers=headers, data=json.dumps(payload), timeout=180000)
        if r_qd.status != 200:
            raise RuntimeError(f"querydata status={r_qd.status}")

        df = parse_querydata_rows(r_qd.json(), ["DIA", "HORA", "SUBMERCADO", "PLD_HORA"])

        # normaliza
        df["DIA"] = df["DIA"].astype(str).str.slice(0, 10)
        df["HORA"] = pd.to_numeric(df["HORA"], errors="coerce").fillna(0).astype(int)
        df["PLD_HORA"] = pd.to_numeric(df["PLD_HORA"], errors="coerce")
        df["SUBMERCADO"] = df["SUBMERCADO"].astype(str).str.strip().str.lower()
        df = df.dropna(subset=["DIA", "HORA", "PLD_HORA"])

        df = df[df["DIA"].str.startswith(str(y0)) | df["DIA"].str.startswith(str(year))].copy()
        if df.empty:
            raise RuntimeError("Power BI retornou 0 linhas após filtro de ano.")

        print("linhas retornadas:", len(df))

        write_sqlite(df[["DIA", "HORA", "SUBMERCADO", "PLD_HORA"]].copy())

        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()

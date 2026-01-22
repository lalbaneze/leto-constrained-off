import os
import re
import glob
import requests
import pandas as pd

# =========================
# CONFIG (ONS / CKAN)
# =========================
DATASET_ID = "restricao_coff_eolica_usi"
CKAN_API = "https://dados.ons.org.br/api/3/action/package_show"

START_YM = "2025-01"               # só a partir daqui
ALWAYS_REFRESH_LAST_N = 2          # rebaixa últimos N meses (ONS revisa)

# intervalos ONS TM (30 min)
INTERVAL_HOURS = 0.5

# só estes contam como "tem restrição"
RESTR_CODES = {"CNF", "ENE", "REL"}

# =========================
# PATHS (relativos ao repo)
# =========================
# Este script fica em .../dashboard/scripts/
DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../dashboard
DATA_DIR = os.path.join(DASHBOARD_DIR, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
ONS_CACHE_DIR = os.path.join(RAW_DIR, "ons_restricao_coff_eolica_usi")

OUT_MONTHLY_TEST = os.path.join(DATA_DIR, "coff_eolica_monthly_test.csv")
OUT_RAW_TEST = os.path.join(RAW_DIR, "coff_eolica_raw_citi_test.csv")

# =========================
# HELPERS
# =========================
def sorted_yms(yms):
    return sorted(yms, key=lambda s: (int(s[:4]), int(s[5:7])))

def month_from_filename(path: str) -> str:
    # RESTRICAO_COFF_EOLICA_YYYY_MM.csv -> YYYY-MM
    m = re.search(r"(\d{4})_(\d{2})", os.path.basename(path))
    return f"{m.group(1)}-{m.group(2)}" if m else ""

def norm_cols(cols):
    return [str(c).strip().lower() for c in cols]

def read_csv_robust(path):
    encodings = ["utf-8", "latin-1"]
    seps = [",", ";"]
    last_err = None

    for enc in encodings:
        for sep in seps:
            try:
                df = pd.read_csv(
                    path,
                    sep=sep,
                    encoding=enc,
                    engine="python",
                    on_bad_lines="skip",
                )
                if df.shape[1] <= 1:
                    continue
                return df
            except Exception as e:
                last_err = e

    raise RuntimeError(f"Falha ao ler {path}: {last_err}")

def to_num(series):
    return pd.to_numeric(series, errors="coerce").fillna(0.0)

def list_ons_monthly_csv_urls():
    resp = requests.get(CKAN_API, params={"id": DATASET_ID}, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"CKAN success=false: {data}")

    resources = data["result"].get("resources", [])
    ym_to_url = {}

    for r in resources:
        name = (r.get("name") or "").strip()
        fmt = (r.get("format") or "").strip().lower()
        url = (r.get("url") or "").strip()

        is_csv = (fmt == "csv") or url.lower().endswith(".csv")

        # padrão comum no ONS: "Restricoes_coff_eolicas-YYYY-MM"
        m = re.search(r"Restricoes?_coff_eolicas-(\d{4})-(\d{2})", name, flags=re.IGNORECASE)
        if is_csv and m and url:
            ym = f"{m.group(1)}-{m.group(2)}"
            ym_to_url[ym] = url

    if not ym_to_url:
        sample = [(r.get("name"), r.get("format"), r.get("url")) for r in resources[:12]]
        raise RuntimeError(
            "Não achei resources mensais CSV no CKAN. Exemplos (primeiros 12):\n"
            + "\n".join([str(x) for x in sample])
        )

    # filtra 2025+
    yms = [ym for ym in sorted_yms(ym_to_url.keys()) if ym >= START_YM]
    if not yms:
        raise RuntimeError(f"Nenhum mês >= {START_YM} encontrado no dataset.")
    return yms, ym_to_url

def download_months(yms, ym_to_url):
    os.makedirs(ONS_CACHE_DIR, exist_ok=True)

    last_n = set(yms[-ALWAYS_REFRESH_LAST_N:]) if ALWAYS_REFRESH_LAST_N > 0 else set()
    downloaded = 0

    for ym in yms:
        yyyy, mm = ym.split("-")
        out_name = f"RESTRICAO_COFF_EOLICA_{yyyy}_{mm}.csv"
        out_path = os.path.join(ONS_CACHE_DIR, out_name)

        if os.path.exists(out_path) and (ym not in last_n):
            continue

        print(f"Baixando {ym} -> {out_name}")
        r = requests.get(ym_to_url[ym], timeout=120)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(r.content)
        downloaded += 1

    return downloaded

def build_monthly_from_cached_csvs():
    pattern = os.path.join(ONS_CACHE_DIR, "RESTRICAO_COFF_EOLICA_*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        raise RuntimeError(f"Não achei CSVs baixados em {ONS_CACHE_DIR}")

    parts = []
    fail = 0

    for f in files:
        try:
            df = read_csv_robust(f)
            df.columns = norm_cols(df.columns)

            required = {
                "nom_usina",
                "val_geracao",
                "val_geracaoreferencia",
                "val_disponibilidade",
                "cod_razaorestricao",
            }
            missing = required - set(df.columns)
            if missing:
                raise RuntimeError(f"Faltam colunas {sorted(list(missing))}")

            mes = month_from_filename(f)
            if not mes:
                raise RuntimeError("Não consegui extrair mês do filename")

            df["mes"] = mes
            df["nom_usina"] = df["nom_usina"].astype(str).str.strip()

            df["val_geracao"] = to_num(df["val_geracao"])
            df["val_geracaoreferencia"] = to_num(df["val_geracaoreferencia"])
            df["val_disponibilidade"] = to_num(df["val_disponibilidade"])

            df["cod_razaorestricao"] = df["cod_razaorestricao"].astype(str).str.strip().str.upper()

            # timestamp opcional
            time_col = None
            for cand in ["din_instante", "instante", "datahora", "data_hora", "datetime"]:
                if cand in df.columns:
                    time_col = cand
                    break
            if time_col:
                df["instante"] = pd.to_datetime(df[time_col], errors="coerce")
            else:
                df["instante"] = pd.NaT

            # ======= CÁLCULO CITI-LIKE =======
            cap_mw = df[["val_disponibilidade", "val_geracaoreferencia"]].min(axis=1)
            term_mw = (cap_mw - df["val_geracao"]).clip(lower=0.0)

            restr = df["cod_razaorestricao"].isin(list(RESTR_CODES))

            df["curtailment_mwh"] = 0.0
            df.loc[restr, "curtailment_mwh"] = term_mw.loc[restr] * INTERVAL_HOURS

            df["generation_mwh"] = cap_mw.clip(lower=0.0) * INTERVAL_HOURS
            df["_cap_mw"] = cap_mw

            keep = [
                "mes",
                "instante",
                "nom_usina",
                "cod_razaorestricao",
                "val_geracao",
                "val_geracaoreferencia",
                "val_disponibilidade",
                "_cap_mw",
                "curtailment_mwh",
                "generation_mwh",
            ]
            parts.append(df[keep])

        except Exception as e:
            fail += 1
            print("⚠️ Erro em", os.path.basename(f), "->", e)

    if not parts:
        raise RuntimeError("Nenhum CSV foi processado com sucesso.")

    raw = pd.concat(parts, ignore_index=True)

    monthly = (
        raw.groupby(["mes", "nom_usina", "cod_razaorestricao"], as_index=False)
           .agg({
               "curtailment_mwh": "sum",
               "generation_mwh": "sum",
               "instante": "max",
           })
    )

    monthly["pct_curtailment"] = monthly.apply(
        lambda r: (r.curtailment_mwh / r.generation_mwh) if r.generation_mwh > 0 else 0.0,
        axis=1
    )

    monthly["last_instante"] = monthly["instante"].astype("datetime64[ns]").dt.strftime("%Y-%m-%d %H:%M:%S")
    monthly = monthly.drop(columns=["instante"])

    os.makedirs(RAW_DIR, exist_ok=True)
    raw.to_csv(OUT_RAW_TEST, index=False, encoding="utf-8")
    monthly.to_csv(OUT_MONTHLY_TEST, index=False, encoding="utf-8")

    print("\n✅ OK")
    print("Gerados:")
    print(" -", OUT_MONTHLY_TEST)
    print(" -", OUT_RAW_TEST)
    print("Falhas:", fail)
    print("Linhas raw:", len(raw))
    print("Linhas monthly:", len(monthly))

def main():
    print("Consultando ONS (CKAN)...")
    yms, ym_to_url = list_ons_monthly_csv_urls()
    print(f"Meses (filtrado): {yms[0]} -> {yms[-1]} (n={len(yms)})")

    dl = download_months(yms, ym_to_url)
    print(f"Download concluído. Arquivos baixados/atualizados nesta rodada: {dl}")

    print("Construindo monthly TESTE (Citi-like)...")
    build_monthly_from_cached_csvs()

if __name__ == "__main__":
    main()

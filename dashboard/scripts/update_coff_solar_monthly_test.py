import os
import re
import glob
import requests
import pandas as pd
from datetime import datetime

# =========================
# CONFIG
# =========================
START_YM = "2025-01"
ALWAYS_REFRESH_LAST_N = 13

INTERVAL_HOURS = 0.5
RESTR_CODES = {"CNF", "ENE", "REL"}

BASE_URL = "https://ons-aws-prod-opendata.s3.amazonaws.com/dataset/restricao_coff_fotovoltaica_tm"

# =========================
# PATHS (robustos)
# =========================
DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../dashboard
DATA_DIR = os.path.join(DASHBOARD_DIR, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
ONS_CACHE_DIR = os.path.join(RAW_DIR, "ons_restricao_coff_fotovoltaica_tm")

OUT_MONTHLY_TEST = os.path.join(DATA_DIR, "coff_solar_monthly_test.csv")
OUT_RAW_TEST = os.path.join(RAW_DIR, "coff_solar_raw_citi_test.csv")

os.makedirs(ONS_CACHE_DIR, exist_ok=True)
os.makedirs(RAW_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# =========================
# HELPERS
# =========================
def sorted_yms(yms):
    return sorted(yms, key=lambda s: (int(s[:4]), int(s[5:7])))

def yms_between(start_ym: str, end_ym: str):
    ys, ms = map(int, start_ym.split("-"))
    ye, me = map(int, end_ym.split("-"))
    y, m = ys, ms
    while (y < ye) or (y == ye and m <= me):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m == 13:
            m = 1
            y += 1

def month_from_filename(path: str) -> str:
    # RESTRICAO_COFF_FOTOVOLTAICA_YYYY_MM.csv -> YYYY-MM
    m = re.search(r"(\d{4})_(\d{2})", os.path.basename(path))
    return f"{m.group(1)}-{m.group(2)}" if m else ""

def norm_cols(cols):
    return [str(c).strip().lower() for c in cols]

def read_csv_robust(path):
    encodings = ["utf-8", "latin-1"]
    seps = [";", ","]
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
    # aceita vírgula decimal também
    s = series.astype(str).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce").fillna(0.0)

def build_url(ym: str) -> str:
    y, m = ym.split("-")
    return f"{BASE_URL}/RESTRICAO_COFF_FOTOVOLTAICA_{y}_{m}.csv"

def download_months():
    today = datetime.today()
    end_ym = f"{today.year:04d}-{today.month:02d}"

    yms = [ym for ym in yms_between(START_YM, end_ym)]
    last_n = set(yms[-ALWAYS_REFRESH_LAST_N:]) if ALWAYS_REFRESH_LAST_N > 0 else set()

    downloaded = 0
    for ym in yms:
        yyyy, mm = ym.split("-")
        out_name = f"RESTRICAO_COFF_FOTOVOLTAICA_{yyyy}_{mm}.csv"
        out_path = os.path.join(ONS_CACHE_DIR, out_name)

        if os.path.exists(out_path) and (ym not in last_n):
            continue

        url = build_url(ym)
        print(f"Baixando {ym} -> {out_name}")
        r = requests.get(url, timeout=120)
        if r.status_code != 200:
            print(f"⚠️ SKIP {ym}: HTTP {r.status_code}")
            continue

        with open(out_path, "wb") as f:
            f.write(r.content)
        downloaded += 1

    return downloaded

def build_monthly_from_cached_csvs():
    pattern = os.path.join(ONS_CACHE_DIR, "RESTRICAO_COFF_FOTOVOLTAICA_*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        raise RuntimeError(f"Não achei CSVs baixados em {ONS_CACHE_DIR}")

    parts = []
    fail = 0

    for f in files:
        try:
            df = read_csv_robust(f)
            df.columns = norm_cols(df.columns)

            required_base = {"nom_usina", "val_geracao", "val_geracaoreferencia", "cod_razaorestricao"}
            missing = required_base - set(df.columns)
            if missing:
                raise RuntimeError(f"Faltam colunas {sorted(list(missing))}")

            mes = month_from_filename(f)
            if not mes:
                raise RuntimeError("Não consegui extrair mês do filename")

            df["mes"] = mes
            df["nom_usina"] = df["nom_usina"].astype(str).str.strip()

            df["val_geracao"] = to_num(df["val_geracao"])
            df["val_geracaoreferencia"] = to_num(df["val_geracaoreferencia"])

            # disponibilidade pode ou não existir no solar
            if "val_disponibilidade" in df.columns:
                df["val_disponibilidade"] = to_num(df["val_disponibilidade"])
                cap_mw = df[["val_disponibilidade", "val_geracaoreferencia"]].min(axis=1)
            else:
                cap_mw = df["val_geracaoreferencia"]

            # cod motivo: padroniza igual eólica
            df["cod_razaorestricao"] = df["cod_razaorestricao"].fillna("NAN")
            df["cod_razaorestricao"] = df["cod_razaorestricao"].astype(str).str.strip().str.upper()
            df.loc[df["cod_razaorestricao"].isin(["", "NONE", "NULL", "<NA>"]), "cod_razaorestricao"] = "NAN"

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
                "_cap_mw",
                "curtailment_mwh",
                "generation_mwh",
            ]
            # inclui disponibilidade se existir
            if "val_disponibilidade" in df.columns:
                keep.insert(6, "val_disponibilidade")

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
    print("Baixando meses (S3 ONS)...")
    dl = download_months()
    print(f"Download concluído. Arquivos baixados/atualizados nesta rodada: {dl}")

    print("Construindo monthly TESTE (Citi-like)...")
    build_monthly_from_cached_csvs()

if __name__ == "__main__":
    main()

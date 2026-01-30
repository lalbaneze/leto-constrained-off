import os
import pandas as pd
from datetime import datetime

# ---- paths robustos (independente de onde roda) ----
DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../dashboard
DATA_DIR = os.path.join(DASHBOARD_DIR, "data")

OUT_CSV = os.path.join(DATA_DIR, "coff_solar_monthly_test.csv")
RAW_DIR = os.path.join(DATA_DIR, "raw", "solar_test")

BASE_URL = "https://ons-aws-prod-opendata.s3.amazonaws.com/dataset/restricao_coff_fotovoltaica_tm"

os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
os.makedirs(RAW_DIR, exist_ok=True)

# -------------------------------
# helpers
# -------------------------------
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

def build_url(ym: str) -> str:
    y, m = ym.split("-")
    return f"{BASE_URL}/RESTRICAO_COFF_FOTOVOLTAICA_{y}_{m}.csv"

def download_month(ym: str) -> str:
    url = build_url(ym)
    local = os.path.join(RAW_DIR, f"RESTRICAO_COFF_FOTOVOLTAICA_{ym.replace('-', '_')}.csv")
    df = pd.read_csv(url, sep=";", encoding="utf-8", low_memory=False)
    df.to_csv(local, index=False, sep=";")
    return local

def compute_dt_hours(df: pd.DataFrame) -> pd.Series:
    df = df.sort_values("din_instante")
    t = pd.to_datetime(df["din_instante"], errors="coerce")
    dt = (t.shift(-1) - t).dt.total_seconds() / 3600.0

    dt = dt.where((dt > 0) & (dt <= 6))
    med = dt.median()
    if pd.isna(med) or med <= 0:
        med = 0.5
    return dt.fillna(med)

# -------------------------------
# core aggregation
# -------------------------------
def monthly_aggregate_one_month(df: pd.DataFrame, ym: str) -> pd.DataFrame:
    df = df.copy()

    df["din_instante"] = pd.to_datetime(df["din_instante"], errors="coerce")

    df["val_geracao"] = pd.to_numeric(df["val_geracao"], errors="coerce").fillna(0)
    df["val_geracaolimitada"] = pd.to_numeric(df["val_geracaolimitada"], errors="coerce")

    ref_col = "val_geracaoreferencia"
    df[ref_col] = pd.to_numeric(df[ref_col], errors="coerce").fillna(0)

    df["dt_h"] = compute_dt_hours(df)

    has_limit = df["val_geracaolimitada"].notna()
    corte_mw = (df[ref_col] - df["val_geracao"]).clip(lower=0)

    df["curtailment_mwh"] = (corte_mw.where(has_limit, 0)) * df["dt_h"]
    df["generation_mwh"]  = df[ref_col] * df["dt_h"]

    last_inst = df["din_instante"].max()
    if pd.isna(last_inst):
        last_inst_str = ""
    else:
        last_inst = pd.to_datetime(last_inst, errors="coerce")
        last_inst_str = "" if pd.isna(last_inst) else last_inst.strftime("%Y-%m-%d %H:%M:%S")

    g = (
        df.groupby(["nom_usina", "cod_razaorestricao"], dropna=False)
          .agg(
              curtailment_mwh=("curtailment_mwh", "sum"),
              generation_mwh=("generation_mwh", "sum"),
          )
          .reset_index()
    )

    g["mes"] = ym
    g["last_instante"] = last_inst_str
    g["pct_curtail"] = g.apply(
        lambda r: (r["curtailment_mwh"] / r["generation_mwh"]) if r["generation_mwh"] > 0 else 0,
        axis=1
    )

    g["nom_usina"] = g["nom_usina"].astype(str).str.strip()
    g["cod_razaorestricao"] = g["cod_razaorestricao"].astype(str).str.strip().str.upper()

    return g[
        ["mes","nom_usina","cod_razaorestricao",
         "curtailment_mwh","generation_mwh","pct_curtail","last_instante"]
    ]

# -------------------------------
# main
# -------------------------------
def main():
    start_ym = "2025-01"
    today = datetime.today()
    end_ym = f"{today.year:04d}-{today.month:02d}"

    frames = []
    for ym in yms_between(start_ym, end_ym):
        try:
            path = download_month(ym)
            df = pd.read_csv(path, sep=";")
            out = monthly_aggregate_one_month(df, ym)
            frames.append(out)

            print(f"[OK] {ym} | linhas: {len(out)} | corte_mwh: {out['curtailment_mwh'].sum():,.2f}")
        except Exception as e:
            print(f"[SKIP] {ym}: {e}")

    final = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["mes","nom_usina","cod_razaorestricao","curtailment_mwh","generation_mwh","pct_curtail","last_instante"]
    )

    final.to_csv(OUT_CSV, index=False)
    print(f"\n✅ Gerado: {OUT_CSV} | linhas: {len(final)} | meses: {final['mes'].nunique() if len(final) else 0}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

# matplotlib headless
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether
)
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.pdfgen import canvas as rlcanvas


# =========================
# THEME
# =========================
NAVY = "#0b1d3a"
GREEN = "#22c55e"
GRAY_DARK = "#6b7280"
GRAY_LIGHT = "#d1d5db"
WHITE = colors.white

PAGE_W, PAGE_H = A4

# Table outline stronger
GRID_COLOR = colors.HexColor("#334155")   # slate-ish
GRID_W_OUTER = 1.2
GRID_W_INNER = 0.8


# =========================
# HELPERS
# =========================
def br_int(x) -> str:
    try:
        return f"{int(round(float(x))):,}".replace(",", ".")
    except Exception:
        return "—"

def br_float(x, nd=2) -> str:
    try:
        s = f"{float(x):,.{nd}f}"
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
        return s
    except Exception:
        return "—"

def br_pct(x, nd=2) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{br_float(100.0*float(x), nd)}%"

def gwh_from_mwh(mwh) -> float:
    return float(mwh) / 1000.0

def month_pt(ym: str) -> str:
    # ym: "YYYY-MM"
    months = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho","Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
    y, m = ym.split("-")
    mi = int(m)
    return f"{months[mi-1]}/{y}"

def ym_from_cli(fechamento_cli: str) -> str:
    # accepts "2025-12" or "2025_12"
    x = fechamento_cli.strip().replace("_", "-")
    if len(x) == 7 and x[4] == "-":
        return x
    raise ValueError("Use --fechamento YYYY-MM (ex: 2025-12)")

def norm_key(s: str) -> str:
    if s is None:
        return ""
    return (
        str(s).strip().upper()
        .replace("\u00A0", " ")
    )

def norm_reason(x) -> str:
    if pd.isna(x):
        return "SEM"
    v = str(x).strip().upper()
    if v in ("ENE","REL","CNF","CONF"):
        return "CNF" if v == "CONF" else v
    return "SEM"

def safe_num(x) -> float:
    if x is None:
        return 0.0
    s = str(x).strip()
    if s == "" or s.lower() in ("nan","none","null"):
        return 0.0
    # pt-BR number
    if "," in s:
        try:
            return float(s.replace(".", "").replace(",", "."))
        except Exception:
            return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


# =========================
# DATA LOAD
# =========================
def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    # normalize expected columns
    # monthly files should have:
    # mes, nom_usina, curtailment_mwh, generation_mwh, last_instante, cod_razaorestricao
    for col in ["mes","nom_usina","curtailment_mwh","generation_mwh"]:
        if col not in df.columns:
            raise ValueError(f"CSV sem coluna obrigatória: {col} ({path})")
    if "cod_razaorestricao" not in df.columns:
        df["cod_razaorestricao"] = "SEM"
    if "last_instante" not in df.columns:
        df["last_instante"] = ""
    return df

def load_mapping(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    # build normalized-key mapping too
    norm = {}
    for k, v in raw.items():
        nk = norm_key(k)
        if nk and nk not in norm:
            norm[nk] = v
    return {"raw": raw, "norm": norm}

def map_empresa_tipo(df: pd.DataFrame, mapping: Dict, tipo_default: str) -> pd.DataFrame:
    raw = mapping["raw"]
    norm = mapping["norm"]

    def get_emp(usina):
        m = raw.get(usina)
        if m is None:
            m = norm.get(norm_key(usina))
        if not m:
            return "Não Mapeada"
        emp = m.get("empresa", "Não Mapeada")
        if emp is None:
            return "Não Mapeada"
        emp = str(emp).strip()
        return emp if emp else "Não Mapeada"

    def get_tipo(usina):
        m = raw.get(usina)
        if m is None:
            m = norm.get(norm_key(usina))
        if not m:
            return tipo_default
        t = m.get("tipo", tipo_default)
        if t is None:
            return tipo_default
        t = str(t).strip().upper()
        return t if t else tipo_default

    out = df.copy()
    out["cod_razaorestricao"] = out["cod_razaorestricao"].apply(norm_reason)
    out["curtailment_mwh"] = out["curtailment_mwh"].apply(safe_num)
    out["generation_mwh"] = out["generation_mwh"].apply(safe_num)
    out["empresa"] = out["nom_usina"].map(get_emp)
    out["tipo_map"] = out["nom_usina"].map(get_tipo)
    return out

def load_pld_monthly(path: str) -> Dict[str, float]:
    with open(path, "r", encoding="utf-8") as f:
        j = json.load(f)
    out = {}
    for k, v in j.items():
        if v in (None, ""):
            continue
        try:
            out[str(k)] = float(v)
        except Exception:
            continue
    return out


# =========================
# AGGREGATIONS
# =========================
def last_inst_by_month(df: pd.DataFrame) -> pd.Series:
    s = df.groupby("mes")["last_instante"].max()
    return s

def month_label(df_monthly_row) -> str:
    # Use last_instante_max if parseable
    li = getattr(df_monthly_row, "last_instante_max", "")
    mes = getattr(df_monthly_row, "mes", "")
    if isinstance(li, str) and li.strip():
        try:
            d = pd.to_datetime(li.strip().replace(" ", "T"))
            return d.strftime("%Y-%m-%d")
        except Exception:
            pass
    return mes

def agg_monthly(df: pd.DataFrame) -> pd.DataFrame:
    piv = (
        df.pivot_table(
            index="mes",
            columns="cod_razaorestricao",
            values="curtailment_mwh",
            aggfunc="sum",
        ).fillna(0)
    )
    out = df.groupby("mes", as_index=False).agg(
        corte=("curtailment_mwh", "sum"),
        ref=("generation_mwh", "sum"),
    )
    out = out.merge(piv.reset_index(), on="mes", how="left").fillna(0)

    for rr in ["ENE","CNF","REL","SEM"]:
        if rr not in out.columns:
            out[rr] = 0.0

    out["pct"] = np.where(out["ref"] > 0, out["corte"]/out["ref"], 0.0)

    li = df.groupby("mes")["last_instante"].max().reset_index().rename(columns={"last_instante":"last_instante_max"})
    out = out.merge(li, on="mes", how="left").sort_values("mes")
    return out

def impact_rs(df: pd.DataFrame, pld_monthly: Dict[str,float]) -> float:
    corte_by_month = df.groupby("mes")["curtailment_mwh"].sum()
    total = 0.0
    for ym, corte in corte_by_month.items():
        p = pld_monthly.get(str(ym))
        if p is None:
            continue
        total += float(corte) * float(p)
    return total

def yoy_pp_last_month(monthly: pd.DataFrame, fechamento_ym: str) -> float:
    # yoy delta in percentage points for "pct" of fechamento vs same month prior year
    # If missing, return np.nan
    try:
        cur = monthly.loc[monthly["mes"] == fechamento_ym, "pct"].values
        if len(cur) == 0:
            return np.nan
        y, m = fechamento_ym.split("-")
        prev = f"{int(y)-1:04d}-{m}"
        prevv = monthly.loc[monthly["mes"] == prev, "pct"].values
        if len(prevv) == 0:
            return np.nan
        return (float(cur[0]) - float(prevv[0])) * 100.0  # pp (already *100)
    except Exception:
        return np.nan

def kpis(df: pd.DataFrame, pld: Dict[str,float]) -> Tuple[float,float,float,float]:
    corte = float(df["curtailment_mwh"].sum())
    ref = float(df["generation_mwh"].sum())
    pct = (corte/ref) if ref > 0 else 0.0
    imp = impact_rs(df, pld) / 1e6  # R$ mm
    return corte, ref, pct, imp

def top_bottom_companies_period(all_df: pd.DataFrame, pld: Dict[str,float], fechamento_ym: str) -> Dict[str, List[Tuple[str, float]]]:
    # compute per company over entire period up to fechamento
    df = all_df.copy()
    df = df[df["mes"] <= fechamento_ym].copy()
    grp = df.groupby("empresa", as_index=False).agg(
        corte=("curtailment_mwh","sum"),
        ref=("generation_mwh","sum")
    )
    grp["pct"] = np.where(grp["ref"]>0, grp["corte"]/grp["ref"], np.nan)

    def clean_name(x):
        return str(x).strip()

    # exclude
    bad = set(["Kroma","nan","Não Mapeada","Nao Mapeada","NÃO MAPEADA","N├úO MAPEADA"])
    grp = grp[~grp["empresa"].apply(lambda x: clean_name(x) in bad)].copy()

    grp_pct = grp.dropna(subset=["pct"]).copy()
    top_pct = grp_pct.sort_values("pct", ascending=False).head(3)[["empresa","pct"]].values.tolist()
    bot_pct = grp_pct.sort_values("pct", ascending=True).head(3)[["empresa","pct"]].values.tolist()

    top_vol = grp.sort_values("corte", ascending=False).head(3)[["empresa","corte"]].values.tolist()
    bot_vol = grp.sort_values("corte", ascending=True).head(3)[["empresa","corte"]].values.tolist()

    # same for last month only
    last = df[df["mes"] == fechamento_ym].copy()
    grpL = last.groupby("empresa", as_index=False).agg(
        corte=("curtailment_mwh","sum"),
        ref=("generation_mwh","sum")
    )
    grpL["pct"] = np.where(grpL["ref"]>0, grpL["corte"]/grpL["ref"], np.nan)
    grpL = grpL[~grpL["empresa"].apply(lambda x: clean_name(x) in bad)].copy()
    grpL_pct = grpL.dropna(subset=["pct"]).copy()
    top_pct_L = grpL_pct.sort_values("pct", ascending=False).head(3)[["empresa","pct"]].values.tolist()
    bot_pct_L = grpL_pct.sort_values("pct", ascending=True).head(3)[["empresa","pct"]].values.tolist()

    return {
        "top_pct": [(a, float(b)) for a,b in top_pct],
        "bot_pct": [(a, float(b)) for a,b in bot_pct],
        "top_vol": [(a, float(b)) for a,b in top_vol],
        "bot_vol": [(a, float(b)) for a,b in bot_vol],
        "top_pct_L": [(a, float(b)) for a,b in top_pct_L],
        "bot_pct_L": [(a, float(b)) for a,b in bot_pct_L],
    }


# =========================
# CHARTS
# =========================
def make_chart_monthly(df_monthly: pd.DataFrame, title: str, outpath: str, compact=False):
    x = [month_label(r) for r in df_monthly.itertuples(index=False)]
    corte_gwh = df_monthly["corte"].values / 1e3
    pct = df_monthly["pct"].values * 100.0

    # slightly taller to avoid "amassado"
    h = 3.25 if compact else 3.55
    fig, ax1 = plt.subplots(figsize=(10.8, h), dpi=160)

    bars = ax1.bar(x, corte_gwh)
    for b in bars:
        b.set_color(GREEN)

    ax1.set_ylabel("Corte (GWh)")
    ax1.tick_params(axis="x", rotation=45, labelsize=8)
    ax1.grid(True, axis="y", alpha=0.25)

    ax2 = ax1.twinx()
    ln = ax2.plot(x, pct, marker="o", linewidth=2)
    for l in ln:
        l.set_color(NAVY)
    ax2.set_ylabel("% Curtailment")
    ax2.set_ylim(0, max(5, float(pct.max())*1.15 if len(pct) else 5))

    ax1.set_title(title, loc="left", fontsize=12, fontweight="bold")
    fig.tight_layout()
    fig.savefig(outpath, bbox_inches="tight")
    plt.close(fig)

def make_chart_build_up_pp(df_monthly: pd.DataFrame, title: str, outpath: str, hide_sem_legend=True, compact=False):
    x = [month_label(r) for r in df_monthly.itertuples(index=False)]
    ref = df_monthly["ref"].values

    def pp(rr):
        return np.where(ref > 0, df_monthly[rr].values / ref * 100.0, 0.0)

    ene, cnf, rel, sem = pp("ENE"), pp("CNF"), pp("REL"), pp("SEM")

    h = 3.10 if compact else 3.35
    fig, ax = plt.subplots(figsize=(10.8, h), dpi=160)

    bottom = np.zeros_like(ene)
    b1 = ax.bar(x, ene, label="ENE")
    bottom += ene
    b2 = ax.bar(x, cnf, bottom=bottom, label="CNF")
    bottom += cnf
    b3 = ax.bar(x, rel, bottom=bottom, label="REL")
    bottom += rel
    b4 = ax.bar(x, sem, bottom=bottom, label="SEM")

    for bars, col in [(b1, GREEN), (b2, NAVY), (b3, GRAY_DARK), (b4, GRAY_LIGHT)]:
        for r in bars:
            r.set_color(col)

    ax.set_ylabel("% corte (pp)")
    ax.tick_params(axis="x", rotation=45, labelsize=8)
    ax.grid(True, axis="y", alpha=0.25)
    ax.set_title(title, loc="left", fontsize=12, fontweight="bold")

    handles, labels = ax.get_legend_handles_labels()
    if hide_sem_legend and "SEM" in labels:
        i = labels.index("SEM")
        handles.pop(i)
        labels.pop(i)

    ax.legend(handles, labels, ncol=4, fontsize=9, frameon=True)
    fig.tight_layout()
    fig.savefig(outpath, bbox_inches="tight")
    plt.close(fig)


# =========================
# PDF STYLES
# =========================
def styles():
    ss = getSampleStyleSheet()
    base = ss["Normal"]
    base.fontName = "Helvetica"
    base.fontSize = 10
    base.leading = 12

    h1 = ParagraphStyle("H1", parent=base, fontName="Helvetica-Bold", fontSize=16, leading=18, spaceAfter=8)
    h2 = ParagraphStyle("H2", parent=base, fontName="Helvetica-Bold", fontSize=12, leading=14, spaceAfter=6)
    small = ParagraphStyle("SMALL", parent=base, fontSize=8.5, leading=10.5, textColor=colors.HexColor("#475569"))
    link = ParagraphStyle("LINK", parent=base, fontSize=11, leading=13, textColor=colors.HexColor(NAVY))
    link_bold = ParagraphStyle("LINKB", parent=base, fontSize=11, leading=13, textColor=colors.HexColor(NAVY), fontName="Helvetica-Bold")

    return {"base": base, "h1": h1, "h2": h2, "small": small, "link": link, "link_bold": link_bold}

def table_style_strong(header_rows=1):
    # Header font SAME size as body, only bold. Stronger outline.
    return TableStyle([
        ("GRID", (0,0), (-1,-1), GRID_W_INNER, GRID_COLOR),
        ("BOX", (0,0), (-1,-1), GRID_W_OUTER, GRID_COLOR),

        ("BACKGROUND", (0,0), (-1,header_rows-1), colors.HexColor(NAVY)),
        ("TEXTCOLOR", (0,0), (-1,header_rows-1), colors.white),
        ("FONTNAME", (0,0), (-1,header_rows-1), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,header_rows-1), 10),

        ("FONTNAME", (0,header_rows), (-1,-1), "Helvetica"),
        ("FONTSIZE", (0,header_rows), (-1,-1), 10),

        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ])

def table_style_light_box():
    return TableStyle([
        ("GRID", (0,0), (-1,-1), GRID_W_INNER, GRID_COLOR),
        ("BOX", (0,0), (-1,-1), GRID_W_OUTER, GRID_COLOR),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING", (0,0), (-1,-1), 7),
        ("BOTTOMPADDING", (0,0), (-1,-1), 7),
    ])


# =========================
# PAGE DECORATORS
# =========================
def draw_page_bg(c: rlcanvas.Canvas, fill_hex: str | None):
    """
    fill_hex:
      - string tipo "#RRGGBB" para pintar o fundo
      - None para fundo branco
    """
    c.saveState()
    if fill_hex:
        c.setFillColor(colors.HexColor(fill_hex))
        c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    else:
        c.setFillColor(colors.white)
        c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    c.restoreState()

def header_bar(c: rlcanvas.Canvas, title_left="JGP Crédito"):
    # top-left header on white pages
    c.saveState()
    c.setFillColor(colors.HexColor(NAVY))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(2*cm, PAGE_H - 1.2*cm, title_left)
    c.restoreState()

def on_cover(c: rlcanvas.Canvas, _doc, fechamento_label: str):
    # Fundo verde na capa
    draw_page_bg(c, GREEN)

    c.saveState()
    # Texto preto na capa
    c.setFillColor(colors.black)

    c.setFont("Helvetica-Bold", 26)
    c.drawString(2.0*cm, PAGE_H - 5.0*cm, "JGP Crédito")

    c.setFont("Helvetica-Bold", 26)
    c.drawString(2.0*cm, PAGE_H - 8.7*cm, "Relatório de Constrained-Off")

    c.setFont("Helvetica", 12)
    c.drawString(2.0*cm, PAGE_H - 10.2*cm, fechamento_label)

    c.restoreState()

def on_later_pages(c: rlcanvas.Canvas, _doc):
    # páginas internas brancas
    draw_page_bg(c, None)
    header_bar(c, "JGP Crédito")


# =========================
# BUILD PAGES
# =========================
@dataclass
class Paths:
    eolica: str
    solar: str
    mapping: str
    pld: str
    out_pdf: str

def make_overview_page(sty, fechamento_ym: str, all_df: pd.DataFrame, monthly_all: pd.DataFrame,
                       charts_top: str, charts_bottom: str, pld: Dict[str,float]) -> List:
    # KPIs (period)
    corte, ref, pct, imp = kpis(all_df, pld)
    yoy_pp = yoy_pp_last_month(monthly_all, fechamento_ym)

    fechamento_label = month_pt(fechamento_ym)

    # Top lists
    tb = top_bottom_companies_period(all_df, pld, fechamento_ym)

    # KPI table (strong outline)
    kpi_header = ["Energia cortada\nMWh", "Geração\nMWh", "% Curtailment\n%", "Impacto financeiro\nR$ mm", "YoY (% corte)\n∆ pp vs mesmo mês"]
    kpi_row = [br_int(corte), br_int(ref), br_pct(pct), br_float(imp, 2), ("—" if np.isnan(yoy_pp) else f"{br_float(yoy_pp,1)}")]
    kpi_tbl = Table([kpi_header, kpi_row], colWidths=[3.2*cm, 3.2*cm, 3.0*cm, 3.5*cm, 4.5*cm])
    kpi_tbl.setStyle(table_style_strong(header_rows=1))

    # 2x2 bullet boxes + 2x2 bullet boxes (outline stronger)
    def bullet_box(title: str, items: List[str]) -> Table:
        # Title + bullets in one cell table with stronger border
        lines = [f"<b>{title}</b>"] + [f"• {x}" for x in items]
        p = Paragraph("<br/>".join(lines), sty["base"])
        t = Table([[p]], colWidths=[8.2*cm])
        t.setStyle(table_style_light_box())
        return t

    # format bullets
    box1 = bullet_box("Maior % de corte (histórico)",
                      [f"{a}: {br_float(b*100,1)}%" for a, b in tb["top_pct"]])
    box2 = bullet_box("Menor % de corte (histórico)",
                      [f"{a}: {br_float(b*100,1)}%" for a, b in tb["bot_pct"]])
    box3 = bullet_box("Maior volume cortado (histórico)",
                      [f"{a}: {br_float(gwh_from_mwh(v),3)} GWh" for a, v in tb["top_vol"]])
    box4 = bullet_box("Menor volume cortado (histórico)",
                      [f"{a}: {br_int(v)} MWh" for a, v in tb["bot_vol"]])

    box5 = bullet_box(f"Maior % de corte (último mês — {fechamento_label})",
                      [f"{a}: {br_float(b*100,1)}%" for a, b in tb["top_pct_L"]])
    box6 = bullet_box(f"Menor % de corte (último mês — {fechamento_label})",
                      [f"{a}: {br_float(b*100,1)}%" for a, b in tb["bot_pct_L"]])

    grid = Table(
        [
            [box1, box2],
            [box5, box6],
            [box3, box4],
        ],
        colWidths=[8.2*cm, 8.2*cm],
        hAlign="LEFT"
    )
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    # Images sized to avoid "amassado"
    img_top = Image(charts_top, width=17.2*cm, height=5.6*cm)
    img_bot = Image(charts_bottom, width=17.2*cm, height=5.3*cm)

    foot = Paragraph(
        f"* Histórico desde Jan/2025 * Fechamento considerando: {fechamento_label} "
        f"<br/>* Impacto financeiro estimado assumindo PLD médio mensal."
        f"<br/>* Ranking restrito às empresas acompanhadas internamente; os demais dados referem-se à base completa do mercado.",
        sty["small"]
    )


    story = []
    story.append(Paragraph("Visão geral", sty["h1"]))
    story.append(Spacer(1, 6))
    # Keep everything together to avoid title alone on prior page
    story.append(KeepTogether([
        kpi_tbl,
        Spacer(1, 8),
        grid,
        Spacer(1, 10),
        img_top,
        Spacer(1, 8),
        img_bot,
        Spacer(1, 6),
        foot
    ]))
    story.append(PageBreak())
    return story

def make_tech_page(sty, titulo: str, fechamento_ym: str, df_tipo: pd.DataFrame, pld: Dict[str,float],
                   monthly_tipo: pd.DataFrame, chart_top: str, chart_bottom: str) -> List:
    fechamento_label = month_pt(fechamento_ym)

    # Period vs Month tables
    corte, ref, pct, imp = kpis(df_tipo, pld)

    # month-only
    last_df = df_tipo[df_tipo["mes"] == fechamento_ym].copy()
    corte_m, ref_m, pct_m, imp_m = kpis(last_df, pld) if not last_df.empty else (0,0,0,0)

    yoy_pp = yoy_pp_last_month(monthly_tipo, fechamento_ym)

    data = [
        ["Período\n(até fechamento)", "Energia cortada\nMWh", "% Curtailment\n%", "Impacto financeiro\nR$ mm", "YoY último mês\n∆ pp"],
        ["Histórico", br_int(corte), br_pct(pct), br_float(imp,2), ("—" if (yoy_pp is None or (isinstance(yoy_pp, float) and np.isnan(yoy_pp))) else br_float(yoy_pp, 1))],
        [fechamento_label, br_int(corte_m), br_pct(pct_m), br_float(imp_m,2), ("—" if np.isnan(yoy_pp) else br_float(yoy_pp,1))],
    ]
    tbl = Table(data, colWidths=[4.4*cm, 3.2*cm, 3.0*cm, 3.6*cm, 3.0*cm])
    tbl.setStyle(table_style_strong(header_rows=1))

    img1 = Image(chart_top, width=17.2*cm, height=6.3*cm)
    img2 = Image(chart_bottom, width=17.2*cm, height=5.8*cm)

    story = []
    story.append(Paragraph(titulo, sty["h1"]))
    story.append(Spacer(1, 10))
    story.append(tbl)
    story.append(Spacer(1, 10))
    story.append(img1)
    story.append(Spacer(1, 10))
    story.append(img2)
    story.append(PageBreak())
    return story

def company_anchor_name(emp: str) -> str:
    # safe anchor
    return "EMP_" + "".join([c for c in emp.upper() if c.isalnum()])

def make_companies_index_page(sty, empresas: List[str]) -> List:
    story = []
    story.append(Paragraph("Empresas", sty["h1"]))
    story.append(Paragraph("Clique em uma empresa para ir direto para a página.", sty["base"]))
    story.append(Spacer(1, 12))

    # Create grid of clickable links (3 columns)
    cols = 3
    rows = int(np.ceil(len(empresas)/cols))
    data = []
    idx = 0
    for r in range(rows):
        row = []
        for c in range(cols):
            if idx < len(empresas):
                emp = empresas[idx]
                anchor = company_anchor_name(emp)
                row.append(Paragraph(f'<link href="#{anchor}"><u><b>{emp.upper()}</b></u></link>', sty["link_bold"]))
            else:
                row.append("")
            idx += 1
        data.append(row)

    t = Table(data, colWidths=[5.4*cm, 5.4*cm, 5.4*cm], rowHeights=[1.2*cm]*rows)
    t.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), GRID_W_INNER, GRID_COLOR),
        ("BOX", (0,0), (-1,-1), GRID_W_OUTER, GRID_COLOR),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("BACKGROUND", (0,0), (-1,-1), colors.whitesmoke),
    ]))
    story.append(t)
    story.append(PageBreak())
    return story

def make_company_page(sty, emp: str, fechamento_ym: str, df_emp: pd.DataFrame, pld: Dict[str,float],
                      monthly_emp: pd.DataFrame, chart_top: str, chart_bottom: str) -> List:
    fechamento_label = month_pt(fechamento_ym)

    # period
    corte, ref, pct, imp = kpis(df_emp, pld)

    # reimbursement: REL + CNF impact for whole period
    df_relcnf = df_emp[df_emp["cod_razaorestricao"].isin(["REL","CNF"])].copy()
    reimb = impact_rs(df_relcnf, pld) / 1e6  # R$ mm

    # month
    df_m = df_emp[df_emp["mes"] == fechamento_ym].copy()
    corte_m, ref_m, pct_m, imp_m = kpis(df_m, pld) if not df_m.empty else (0,0,0,0)
    df_m_relcnf = df_m[df_m["cod_razaorestricao"].isin(["REL","CNF"])].copy()
    reimb_m = (impact_rs(df_m_relcnf, pld) / 1e6) if not df_m_relcnf.empty else 0.0

    yoy_pp = yoy_pp_last_month(monthly_emp, fechamento_ym)

    # Titles
    story = []

    # add bookmark anchor for internal link
    anchor = company_anchor_name(emp)
    story.append(Paragraph(f'<a name="{anchor}"/>', sty["base"]))
    story.append(Paragraph(emp, sty["h1"]))

    # more spacing between title and tables (as you asked)
    story.append(Spacer(1, 14))

        # Historical table (prefer style like "bottom" in your print)
    hist = [
        ["% Curtailment",
         "Energia cortada\n(MWh)",
         "Impacto financeiro\n(R$ mm)",
         "Reembolso est.\n(R$ mm)"],
        [br_pct(pct), br_int(corte), br_float(imp, 2), br_float(reimb, 2)],
    ]
    hist_tbl = Table(hist, colWidths=[3.6*cm, 5.2*cm, 4.2*cm, 4.2*cm])
    hist_tbl.setStyle(table_style_strong(header_rows=1))

    story.append(Paragraph("Análise Histórica", sty["h2"]))
    story.append(Spacer(1, 6))
    story.append(hist_tbl)

    # Month table
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Análise {fechamento_label}", sty["h2"]))
    story.append(Spacer(1, 6))

    m = [
        ["% Curtailment",
         "Energia cortada\n(MWh)",
         "Impacto financeiro\n(R$ mm)",
         "Reembolso est.\n(R$ mm)",
         "YoY último mês\n(pp)"],
        [br_pct(pct_m),
         br_int(corte_m),
         br_float(imp_m, 2),
         br_float(reimb_m, 2),
         ("—" if (yoy_pp is None or (isinstance(yoy_pp, float) and np.isnan(yoy_pp))) else br_float(yoy_pp, 1))],
    ]

    m_tbl = Table(m, colWidths=[3.1*cm, 4.2*cm, 3.7*cm, 3.7*cm, 3.5*cm])
    m_tbl.setStyle(table_style_strong(header_rows=1))
    story.append(m_tbl)  # <<< ESSENCIAL (senão a tabela some)

    # Charts
    story.append(Spacer(1, 12))
    img1 = Image(chart_top, width=17.2*cm, height=6.1*cm)
    img2 = Image(chart_bottom, width=17.2*cm, height=5.7*cm)
    story.append(img1)
    story.append(Spacer(1, 10))
    story.append(img2)

    story.append(PageBreak())
    return story


# =========================
# MAIN PDF BUILD
# =========================
def build_pdf(fechamento_ym: str, out_pdf: str, eol: pd.DataFrame, sol: pd.DataFrame,
              all_df: pd.DataFrame, pld: Dict[str,float]):

    sty = styles()
    fechamento_label = month_pt(fechamento_ym)

    # Prepare charts dir
    out_dir = os.path.dirname(out_pdf) or "."
    charts_dir = os.path.join(out_dir, f"_charts_{fechamento_ym}")
    os.makedirs(charts_dir, exist_ok=True)

    # Monthly aggs
    monthly_all = agg_monthly(all_df)
    monthly_eol = agg_monthly(eol)
    monthly_sol = agg_monthly(sol)

    # Charts: total
    top_all = os.path.join(charts_dir, "total_top.png")
    bot_all = os.path.join(charts_dir, "total_bottom.png")
    make_chart_monthly(monthly_all, "Curtailment total — Corte (GWh) e %", top_all, compact=True)
    make_chart_build_up_pp(monthly_all, "Quebra por modalidade — build-up (% p.p.)", bot_all, hide_sem_legend=True, compact=True)

    # Charts: eol
    top_e = os.path.join(charts_dir, "eol_top.png")
    bot_e = os.path.join(charts_dir, "eol_bottom.png")
    make_chart_monthly(monthly_eol, "Curtailment eólico — Corte (GWh) e %", top_e)
    make_chart_build_up_pp(monthly_eol, "Quebra por modalidade — build-up (% p.p.)", bot_e, hide_sem_legend=True)

    # Charts: sol
    top_s = os.path.join(charts_dir, "sol_top.png")
    bot_s = os.path.join(charts_dir, "sol_bottom.png")
    make_chart_monthly(monthly_sol, "Curtailment solar — Corte (GWh) e %", top_s)
    make_chart_build_up_pp(monthly_sol, "Quebra por modalidade — build-up (% p.p.)", bot_s, hide_sem_legend=True)

    # Companies list for index
    bad = set(["Kroma","nan","Não Mapeada","Nao Mapeada","NÃO MAPEADA","N├úO MAPEADA"])
    empresas = sorted([e for e in all_df["empresa"].dropna().unique().tolist() if str(e).strip() not in bad],
                      key=lambda x: str(x).upper())

    # Company charts
    comp_assets = {}
    for emp in empresas:
        df_emp = all_df[all_df["empresa"] == emp].copy()
        m_emp = agg_monthly(df_emp)
        ctop = os.path.join(charts_dir, f"{company_anchor_name(emp)}_top.png")
        cbot = os.path.join(charts_dir, f"{company_anchor_name(emp)}_bot.png")
        make_chart_monthly(m_emp, f"{emp} — Corte (GWh) e %", ctop)
        make_chart_build_up_pp(m_emp, f"{emp} — Quebra por modalidade (% p.p.)", cbot, hide_sem_legend=True)
        comp_assets[emp] = (df_emp, m_emp, ctop, cbot)

    # PDF doc
    doc = SimpleDocTemplate(
        out_pdf,
        pagesize=A4,
        leftMargin=2.0*cm,
        rightMargin=2.0*cm,
        topMargin=1.6*cm,
        bottomMargin=1.6*cm,
        title="Relatório de Constrained-Off"
    )

    story = []

    # cover (canvas-only) -> add dummy + pagebreak
    story.append(Spacer(1, 1))
    story.append(PageBreak())

    # overview (single page)
    story += make_overview_page(sty, fechamento_ym, all_df, monthly_all, top_all, bot_all, pld)

    # eol page
    story += make_tech_page(sty, "Curtailment Eólico", fechamento_ym, eol, pld, monthly_eol, top_e, bot_e)

    # sol page
    story += make_tech_page(sty, "Curtailment Solar", fechamento_ym, sol, pld, monthly_sol, top_s, bot_s)

    # companies index (clickable)
    story += make_companies_index_page(sty, empresas)

    # each company
    for emp in empresas:
        df_emp, m_emp, ctop, cbot = comp_assets[emp]
        story += make_company_page(sty, emp, fechamento_ym, df_emp, pld, m_emp, ctop, cbot)

    # build with callbacks
    def _cover(c, d):
        on_cover(c, d, fechamento_label)

    doc.build(story, onFirstPage=_cover, onLaterPages=on_later_pages)

    print(f"OK: gerado {out_pdf}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fechamento", required=True, help="YYYY-MM (ex: 2025-12)")
    ap.add_argument("--eolica", default="dashboard/data/coff_eolica_monthly.csv")
    ap.add_argument("--solar", default="dashboard/data/coff_solar_monthly.csv")
    ap.add_argument("--mapping", default="dashboard/data/mapping_citi.json")
    ap.add_argument("--pld", default="dashboard/data/pld_monthly_avg.json")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    fechamento_ym = ym_from_cli(args.fechamento)
    fechamento_label = month_pt(fechamento_ym)

    if args.out is None:
        os.makedirs("reports", exist_ok=True)
        args.out = os.path.join("reports", f"Relatorio_ConstrainedOff_{fechamento_ym}.pdf")

    # load
    eol = load_csv(args.eolica)
    sol = load_csv(args.solar)
    mapping = load_mapping(args.mapping)
    pld = load_pld_monthly(args.pld)

    eol = map_empresa_tipo(eol, mapping, "EOL")
    sol = map_empresa_tipo(sol, mapping, "SOL")

    # filter to fechamento
    eol = eol[eol["mes"] <= fechamento_ym].copy()
    sol = sol[sol["mes"] <= fechamento_ym].copy()
    all_df = pd.concat([eol, sol], ignore_index=True)

    if all_df.empty:
        raise SystemExit(f"Sem dados para fechamento <= {fechamento_ym}")

    # generate
    build_pdf(fechamento_ym, args.out, eol, sol, all_df, pld)


if __name__ == "__main__":
    main()

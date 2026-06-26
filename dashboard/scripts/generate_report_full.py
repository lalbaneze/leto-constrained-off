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
import matplotlib.patches as mpatches

from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether
)
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.pdfgen import canvas as rlcanvas
from reportlab.graphics import renderPDF
from reportlab.graphics.shapes import Drawing, Path, Group
from svglib.svglib import svg2rlg


# =========================
# THEME — Leto Capital
# =========================
# Cores principais
BLACK       = "#000000"   # preto Leto
GREEN_LETO  = "#D8EEA9"   # verde Leto (accent capa + destaques)
GRAY_LETO   = "#BDB7A7"   # cinza Leto
BROWN_LETO  = "#956A49"   # marrom Leto

# Paleta de dados categórica (brand book)
C_ENE       = "#D8EEA9"   # verde Leto claro
C_CNF       = "#7F9657"   # verde médio (sucesso semântico)
C_REL       = "#956A49"   # marrom Leto
C_SEM       = "#505050"   # neutro 700

# Neutros
N_900       = "#1A1A1A"
N_700       = "#505050"
N_500       = "#7D7D7D"
N_300       = "#BDB7A7"
N_100       = "#E8E5DD"

# Paleta comparativa (12 categorias)
PALETTE_COMP = [
    "#000000", "#7B776C", "#BDB7A7", "#7F9657",
    "#A7C878", "#D8EEA9", "#956A49", "#C08B63",
    "#65798F", "#94A6BA", "#8C89A7", "#CFCBDA"
]

# Cores semânticas
SEM_SUCCESS = "#7F9657"
SEM_ERROR   = "#B75C52"
SEM_WARN    = "#D9A441"
SEM_INFO    = "#65798F"

WHITE = colors.white
PAGE_W, PAGE_H = A4

GRID_COLOR  = colors.HexColor("#2e2d29")
GRID_W_OUTER = 1.2
GRID_W_INNER = 0.5

# Matplotlib style — fundo escuro consistente com o brand
MPL_BG      = "#1A1A18"
MPL_TEXT    = "#F0EDE6"
MPL_MUTED   = "#BDB7A7"
MPL_GRID    = "#2e2d29"


# =========================
# HELPERS  (inalterados)
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
    months = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho","Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
    y, m = ym.split("-")
    mi = int(m)
    return f"{months[mi-1]}/{y}"

def ym_from_cli(fechamento_cli: str) -> str:
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
# DATA LOAD  (inalterado)
# =========================
def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
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
# AGGREGATIONS  (inalteradas)
# =========================
def last_inst_by_month(df: pd.DataFrame) -> pd.Series:
    s = df.groupby("mes")["last_instante"].max()
    return s

def month_label(df_monthly_row) -> str:
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
    try:
        cur = monthly.loc[monthly["mes"] == fechamento_ym, "pct"].values
        if len(cur) == 0:
            return np.nan
        y, m = fechamento_ym.split("-")
        prev = f"{int(y)-1:04d}-{m}"
        prevv = monthly.loc[monthly["mes"] == prev, "pct"].values
        if len(prevv) == 0:
            return np.nan
        return (float(cur[0]) - float(prevv[0])) * 100.0
    except Exception:
        return np.nan

def kpis(df: pd.DataFrame, pld: Dict[str,float]) -> Tuple[float,float,float,float]:
    corte = float(df["curtailment_mwh"].sum())
    ref = float(df["generation_mwh"].sum())
    pct = (corte/ref) if ref > 0 else 0.0
    imp = impact_rs(df, pld) / 1e6
    return corte, ref, pct, imp

def top_bottom_companies_period(all_df: pd.DataFrame, pld: Dict[str,float], fechamento_ym: str) -> Dict[str, List[Tuple[str, float]]]:
    df = all_df.copy()
    df = df[df["mes"] <= fechamento_ym].copy()
    grp = df.groupby("empresa", as_index=False).agg(
        corte=("curtailment_mwh","sum"),
        ref=("generation_mwh","sum")
    )
    grp["pct"] = np.where(grp["ref"]>0, grp["corte"]/grp["ref"], np.nan)

    def clean_name(x):
        return str(x).strip()

    bad = set(["Kroma","nan","Não Mapeada","Nao Mapeada","NÃO MAPEADA","N├úO MAPEADA"])
    grp = grp[~grp["empresa"].apply(lambda x: clean_name(x) in bad)].copy()

    grp_pct = grp.dropna(subset=["pct"]).copy()
    top_pct = grp_pct.sort_values("pct", ascending=False).head(3)[["empresa","pct"]].values.tolist()
    bot_pct = grp_pct.sort_values("pct", ascending=True).head(3)[["empresa","pct"]].values.tolist()

    top_vol = grp.sort_values("corte", ascending=False).head(3)[["empresa","corte"]].values.tolist()
    bot_vol = grp.sort_values("corte", ascending=True).head(3)[["empresa","corte"]].values.tolist()

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
# CHARTS — paleta Leto
# =========================
def _apply_leto_style(fig, ax1, ax2=None):
    """Aplica fundo escuro e cores Leto em todos os elementos do gráfico."""
    fig.patch.set_facecolor(MPL_BG)
    for ax in ([ax1] + ([ax2] if ax2 else [])):
        ax.set_facecolor(MPL_BG)
        ax.tick_params(colors=MPL_MUTED, labelsize=8)
        ax.xaxis.label.set_color(MPL_MUTED)
        ax.yaxis.label.set_color(MPL_MUTED)
        ax.title.set_color(MPL_TEXT)
        for spine in ax.spines.values():
            spine.set_edgecolor(MPL_GRID)
        ax.grid(True, axis="y", color=MPL_GRID, alpha=0.7, linewidth=0.6)

def make_chart_monthly(df_monthly: pd.DataFrame, title: str, outpath: str, compact=False):
    x = [month_label(r) for r in df_monthly.itertuples(index=False)]
    corte_gwh = df_monthly["corte"].values / 1e3
    pct = df_monthly["pct"].values * 100.0

    h = 3.25 if compact else 3.55
    fig, ax1 = plt.subplots(figsize=(10.8, h), dpi=160)

    bars = ax1.bar(x, corte_gwh, color=C_ENE, zorder=3)
    ax1.set_ylabel("Corte (GWh)")
    ax1.tick_params(axis="x", rotation=45, labelsize=8)

    ax2 = ax1.twinx()
    ax2.plot(x, pct, marker="o", linewidth=2, color=GRAY_LETO, zorder=4)
    ax2.set_ylabel("% Curtailment")
    ax2.set_ylim(0, max(5, float(pct.max())*1.15 if len(pct) else 5))

    ax1.set_title(title, loc="left", fontsize=12, fontweight="bold")

    _apply_leto_style(fig, ax1, ax2)
    fig.tight_layout()
    fig.savefig(outpath, bbox_inches="tight", facecolor=MPL_BG)
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
    b1 = ax.bar(x, ene,  color=C_ENE,  label="ENE", zorder=3)
    bottom += ene
    b2 = ax.bar(x, cnf,  bottom=bottom, color=C_CNF,  label="CNF", zorder=3)
    bottom += cnf
    b3 = ax.bar(x, rel,  bottom=bottom, color=C_REL,  label="REL", zorder=3)
    bottom += rel
    b4 = ax.bar(x, sem,  bottom=bottom, color=C_SEM,  label="SEM", zorder=3)

    ax.set_ylabel("% corte (pp)")
    ax.tick_params(axis="x", rotation=45, labelsize=8)
    ax.set_title(title, loc="left", fontsize=12, fontweight="bold")

    handles, labels = ax.get_legend_handles_labels()
    if hide_sem_legend and "SEM" in labels:
        i = labels.index("SEM")
        handles.pop(i)
        labels.pop(i)

    ax.legend(handles, labels, ncol=4, fontsize=9, frameon=True,
              facecolor=MPL_BG, edgecolor=MPL_GRID,
              labelcolor=MPL_TEXT)

    _apply_leto_style(fig, ax)
    fig.tight_layout()
    fig.savefig(outpath, bbox_inches="tight", facecolor=MPL_BG)
    plt.close(fig)


# =========================
# PDF STYLES — Leto
# =========================
def styles():
    ss = getSampleStyleSheet()
    base = ss["Normal"]
    base.fontName = "Helvetica"
    base.fontSize = 10
    base.leading = 12

    h1 = ParagraphStyle("H1", parent=base, fontName="Helvetica-Bold", fontSize=16, leading=18, spaceAfter=8)
    h2 = ParagraphStyle("H2", parent=base, fontName="Helvetica-Bold", fontSize=12, leading=14, spaceAfter=6)
    small = ParagraphStyle("SMALL", parent=base, fontSize=8.5, leading=10.5, textColor=colors.HexColor(N_500))
    link = ParagraphStyle("LINK", parent=base, fontSize=11, leading=13, textColor=colors.HexColor(N_900))
    link_bold = ParagraphStyle("LINKB", parent=base, fontSize=11, leading=13, textColor=colors.HexColor(N_900), fontName="Helvetica-Bold")

    return {"base": base, "h1": h1, "h2": h2, "small": small, "link": link, "link_bold": link_bold}

def table_style_strong(header_rows=1):
    return TableStyle([
        ("GRID",       (0,0), (-1,-1), GRID_W_INNER, GRID_COLOR),
        ("BOX",        (0,0), (-1,-1), GRID_W_OUTER, GRID_COLOR),

        # Header: fundo preto, texto verde Leto
        ("BACKGROUND", (0,0), (-1,header_rows-1), colors.HexColor(BLACK)),
        ("TEXTCOLOR",  (0,0), (-1,header_rows-1), colors.HexColor(GREEN_LETO)),
        ("FONTNAME",   (0,0), (-1,header_rows-1), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,header_rows-1), 10),

        # Body
        ("FONTNAME",   (0,header_rows), (-1,-1), "Helvetica"),
        ("FONTSIZE",   (0,header_rows), (-1,-1), 10),
        ("TEXTCOLOR",  (0,header_rows), (-1,-1), colors.HexColor(N_900)),

        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN",      (0,0), (-1,-1), "CENTER"),
        ("LEFTPADDING",(0,0), (-1,-1), 6),
        ("RIGHTPADDING",(0,0),(-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1),5),
    ])

def table_style_light_box():
    return TableStyle([
        ("GRID",       (0,0), (-1,-1), GRID_W_INNER, GRID_COLOR),
        ("BOX",        (0,0), (-1,-1), GRID_W_OUTER, GRID_COLOR),
        ("VALIGN",     (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING",(0,0), (-1,-1), 8),
        ("RIGHTPADDING",(0,0),(-1,-1), 8),
        ("TOPPADDING", (0,0), (-1,-1), 7),
        ("BOTTOMPADDING",(0,0),(-1,-1),7),
    ])


# =========================
# PAGE DECORATORS — Leto
# =========================
def draw_page_bg(c: rlcanvas.Canvas, fill_hex: str | None):
    c.saveState()
    if fill_hex:
        c.setFillColor(colors.HexColor(fill_hex))
    else:
        c.setFillColor(colors.white)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    c.restoreState()

def header_bar(c: rlcanvas.Canvas, title_left="Leto Capital"):
    """Header nas páginas internas: fundo branco, texto preto."""
    c.saveState()
    c.setFillColor(colors.HexColor(N_900))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(2*cm, PAGE_H - 1.2*cm, title_left)
    c.restoreState()

def _draw_logo_on_cover(c: rlcanvas.Canvas, x: float, y: float, scale: float = 0.55):
    """
    Desenha o logo Leto diretamente no canvas via paths SVG.
    Paths do logo (fill original: black) → renderizados em GREEN_LETO na capa.
    x, y: ponto inferior-esquerdo em pontos ReportLab.
    scale: fator de escala (SVG viewBox 638×159).
    """
    logo_color = colors.HexColor(GREEN_LETO)

    # Todos os paths do logo SVG, convertidos para coordenadas ReportLab
    # ReportLab: y cresce para cima; SVG: y cresce para baixo.
    # Transformação: rl_y = svg_h - svg_y  (svg_h = 159)
    SVG_H = 159.0

    def t(px, py):
        """Transforma ponto SVG → ReportLab (espelhado em y), depois escala e translada."""
        return (x + px * scale, y + (SVG_H - py) * scale)

    c.saveState()
    c.setFillColor(logo_color)
    c.setStrokeColor(logo_color)

    # Em vez de recodificar cada path bezier manualmente,
    # usamos svglib para renderizar o SVG como Drawing e desenhamos no canvas.
    # Isso é mais robusto.
    svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo_preto.svg")
    if os.path.exists(svg_path):
        drawing = svg2rlg(svg_path)
        if drawing:
            # Recolore todos os paths para GREEN_LETO
            _recolor_drawing(drawing, colors.HexColor(GREEN_LETO))
            sx = scale
            sy = scale
            drawing.width  *= sx
            drawing.height *= sy
            drawing.transform = (sx, 0, 0, sy, x, y)
            renderPDF.draw(drawing, c, 0, 0)
    c.restoreState()

def _recolor_drawing(drawing, new_color):
    """Recursivamente recolore fillColor de todos os shapes."""
    from reportlab.graphics.shapes import Group, Path, Rect, Circle, Ellipse
    for item in drawing.contents:
        if hasattr(item, "fillColor"):
            item.fillColor = new_color
        if hasattr(item, "strokeColor") and item.strokeColor is not None:
            item.strokeColor = new_color
        if hasattr(item, "contents"):
            _recolor_drawing(item, new_color)

def on_cover(c: rlcanvas.Canvas, _doc, fechamento_label: str):
    """Capa preta com logo Leto em verde Leto e texto branco."""
    draw_page_bg(c, BLACK)

    c.saveState()

    # Logo Leto no topo esquerdo (tenta SVG, fallback para texto)
    svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo_preto.svg")
    logo_drawn = False
    if os.path.exists(svg_path):
        try:
            from svglib.svglib import svg2rlg
            from reportlab.graphics import renderPDF
            drawing = svg2rlg(svg_path)
            if drawing:
                _recolor_drawing(drawing, colors.HexColor(GREEN_LETO))
                scale = 0.30
                drawing.width  *= scale
                drawing.height *= scale
                drawing.transform = (scale, 0, 0, scale, 2.0*cm, PAGE_H - 3.8*cm)
                renderPDF.draw(drawing, c, 0, 0)
                logo_drawn = True
        except Exception as e:
            print(f"Aviso: não foi possível renderizar o SVG do logo: {e}")

    if not logo_drawn:
        # Fallback: texto "Leto Capital" em verde
        c.setFont("Helvetica-Bold", 28)
        c.setFillColor(colors.HexColor(GREEN_LETO))
        c.drawString(2.0*cm, PAGE_H - 3.5*cm, "Leto Capital")

    # Título do relatório em branco
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 26)
    c.drawString(2.0*cm, PAGE_H - 8.7*cm, "Relatório de Constrained-Off")

    # Data de fechamento em cinza Leto
    c.setFont("Helvetica", 12)
    c.setFillColor(colors.HexColor(GRAY_LETO))
    c.drawString(2.0*cm, PAGE_H - 10.2*cm, fechamento_label)

    c.restoreState()

def on_later_pages(c: rlcanvas.Canvas, _doc):
    """Páginas internas: fundo branco, header discreto."""
    draw_page_bg(c, None)
    header_bar(c, "Leto Capital")


# =========================
# BUILD PAGES  (lógica inalterada, textos atualizados)
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
    corte, ref, pct, imp = kpis(all_df, pld)
    yoy_pp = yoy_pp_last_month(monthly_all, fechamento_ym)
    fechamento_label = month_pt(fechamento_ym)
    tb = top_bottom_companies_period(all_df, pld, fechamento_ym)

    kpi_header = ["Energia cortada\nMWh", "Geração\nMWh", "% Curtailment\n%", "Impacto financeiro\nR$ mm", "YoY (% corte)\n∆ pp vs mesmo mês"]
    kpi_row = [br_int(corte), br_int(ref), br_pct(pct), br_float(imp, 2), ("—" if np.isnan(yoy_pp) else f"{br_float(yoy_pp,1)}")]
    kpi_tbl = Table([kpi_header, kpi_row], colWidths=[3.2*cm, 3.2*cm, 3.0*cm, 3.5*cm, 4.5*cm])
    kpi_tbl.setStyle(table_style_strong(header_rows=1))

    def bullet_box(title: str, items: List[str]) -> Table:
        lines = [f"<b>{title}</b>"] + [f"• {x}" for x in items]
        p = Paragraph("<br/>".join(lines), sty["base"])
        t = Table([[p]], colWidths=[8.2*cm])
        t.setStyle(table_style_light_box())
        return t

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
        [[box1, box2], [box5, box6], [box3, box4]],
        colWidths=[8.2*cm, 8.2*cm],
        hAlign="LEFT"
    )
    grid.setStyle(TableStyle([
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ("RIGHTPADDING",  (0,0), (-1,-1), 6),
        ("TOPPADDING",    (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))

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

    corte, ref, pct, imp = kpis(df_tipo, pld)

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
    return "EMP_" + "".join([c for c in emp.upper() if c.isalnum()])

def make_companies_index_page(sty, empresas: List[str]) -> List:
    story = []
    story.append(Paragraph("Empresas", sty["h1"]))
    story.append(Paragraph("Clique em uma empresa para ir direto para a página.", sty["base"]))
    story.append(Spacer(1, 12))

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
        ("GRID",       (0,0), (-1,-1), GRID_W_INNER, GRID_COLOR),
        ("BOX",        (0,0), (-1,-1), GRID_W_OUTER, GRID_COLOR),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN",      (0,0), (-1,-1), "CENTER"),
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor(N_100)),
    ]))
    story.append(t)
    story.append(PageBreak())
    return story

def make_company_page(sty, emp: str, fechamento_ym: str, df_emp: pd.DataFrame, pld: Dict[str,float],
                      monthly_emp: pd.DataFrame, chart_top: str, chart_bottom: str) -> List:
    fechamento_label = month_pt(fechamento_ym)

    corte, ref, pct, imp = kpis(df_emp, pld)

    df_relcnf = df_emp[df_emp["cod_razaorestricao"].isin(["REL","CNF"])].copy()
    reimb = impact_rs(df_relcnf, pld) / 1e6

    df_m = df_emp[df_emp["mes"] == fechamento_ym].copy()
    corte_m, ref_m, pct_m, imp_m = kpis(df_m, pld) if not df_m.empty else (0,0,0,0)
    df_m_relcnf = df_m[df_m["cod_razaorestricao"].isin(["REL","CNF"])].copy()
    reimb_m = (impact_rs(df_m_relcnf, pld) / 1e6) if not df_m_relcnf.empty else 0.0

    yoy_pp = yoy_pp_last_month(monthly_emp, fechamento_ym)

    story = []

    anchor = company_anchor_name(emp)
    story.append(Paragraph(f'<a name="{anchor}"/>', sty["base"]))
    story.append(Paragraph(emp, sty["h1"]))
    story.append(Spacer(1, 14))

    hist = [
        ["% Curtailment", "Energia cortada\n(MWh)", "Impacto financeiro\n(R$ mm)", "Reembolso est.\n(R$ mm)"],
        [br_pct(pct), br_int(corte), br_float(imp, 2), br_float(reimb, 2)],
    ]
    hist_tbl = Table(hist, colWidths=[3.6*cm, 5.2*cm, 4.2*cm, 4.2*cm])
    hist_tbl.setStyle(table_style_strong(header_rows=1))

    story.append(Paragraph("Análise Histórica", sty["h2"]))
    story.append(Spacer(1, 6))
    story.append(hist_tbl)

    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Análise {fechamento_label}", sty["h2"]))
    story.append(Spacer(1, 6))

    m = [
        ["% Curtailment", "Energia cortada\n(MWh)", "Impacto financeiro\n(R$ mm)", "Reembolso est.\n(R$ mm)", "YoY último mês\n(pp)"],
        [br_pct(pct_m), br_int(corte_m), br_float(imp_m, 2), br_float(reimb_m, 2),
         ("—" if (yoy_pp is None or (isinstance(yoy_pp, float) and np.isnan(yoy_pp))) else br_float(yoy_pp, 1))],
    ]
    m_tbl = Table(m, colWidths=[3.1*cm, 4.2*cm, 3.7*cm, 3.7*cm, 3.5*cm])
    m_tbl.setStyle(table_style_strong(header_rows=1))
    story.append(m_tbl)

    story.append(Spacer(1, 12))
    img1 = Image(chart_top, width=17.2*cm, height=6.1*cm)
    img2 = Image(chart_bottom, width=17.2*cm, height=5.7*cm)
    story.append(img1)
    story.append(Spacer(1, 10))
    story.append(img2)

    story.append(PageBreak())
    return story


# =========================
# MAIN PDF BUILD  (inalterado)
# =========================
def build_pdf(fechamento_ym: str, out_pdf: str, eol: pd.DataFrame, sol: pd.DataFrame,
              all_df: pd.DataFrame, pld: Dict[str,float]):

    sty = styles()
    fechamento_label = month_pt(fechamento_ym)

    out_dir = os.path.dirname(out_pdf) or "."
    charts_dir = os.path.join(out_dir, f"_charts_{fechamento_ym}")
    os.makedirs(charts_dir, exist_ok=True)

    monthly_all = agg_monthly(all_df)
    monthly_eol = agg_monthly(eol)
    monthly_sol = agg_monthly(sol)

    top_all = os.path.join(charts_dir, "total_top.png")
    bot_all = os.path.join(charts_dir, "total_bottom.png")
    make_chart_monthly(monthly_all, "Curtailment total — Corte (GWh) e %", top_all, compact=True)
    make_chart_build_up_pp(monthly_all, "Quebra por modalidade — build-up (% p.p.)", bot_all, hide_sem_legend=True, compact=True)

    top_e = os.path.join(charts_dir, "eol_top.png")
    bot_e = os.path.join(charts_dir, "eol_bottom.png")
    make_chart_monthly(monthly_eol, "Curtailment eólico — Corte (GWh) e %", top_e)
    make_chart_build_up_pp(monthly_eol, "Quebra por modalidade — build-up (% p.p.)", bot_e, hide_sem_legend=True)

    top_s = os.path.join(charts_dir, "sol_top.png")
    bot_s = os.path.join(charts_dir, "sol_bottom.png")
    make_chart_monthly(monthly_sol, "Curtailment solar — Corte (GWh) e %", top_s)
    make_chart_build_up_pp(monthly_sol, "Quebra por modalidade — build-up (% p.p.)", bot_s, hide_sem_legend=True)

    bad = set(["Kroma","nan","Não Mapeada","Nao Mapeada","NÃO MAPEADA","N├úO MAPEADA"])
    empresas = sorted([e for e in all_df["empresa"].dropna().unique().tolist() if str(e).strip() not in bad],
                      key=lambda x: str(x).upper())

    comp_assets = {}
    for emp in empresas:
        df_emp = all_df[all_df["empresa"] == emp].copy()
        m_emp = agg_monthly(df_emp)
        ctop = os.path.join(charts_dir, f"{company_anchor_name(emp)}_top.png")
        cbot = os.path.join(charts_dir, f"{company_anchor_name(emp)}_bot.png")
        make_chart_monthly(m_emp, f"{emp} — Corte (GWh) e %", ctop)
        make_chart_build_up_pp(m_emp, f"{emp} — Quebra por modalidade (% p.p.)", cbot, hide_sem_legend=True)
        comp_assets[emp] = (df_emp, m_emp, ctop, cbot)

    doc = SimpleDocTemplate(
        out_pdf,
        pagesize=A4,
        leftMargin=2.0*cm,
        rightMargin=2.0*cm,
        topMargin=1.6*cm,
        bottomMargin=1.6*cm,
        title="Relatório de Constrained-Off — Leto Capital"
    )

    story = []
    story.append(Spacer(1, 1))
    story.append(PageBreak())

    story += make_overview_page(sty, fechamento_ym, all_df, monthly_all, top_all, bot_all, pld)
    story += make_tech_page(sty, "Curtailment Eólico", fechamento_ym, eol, pld, monthly_eol, top_e, bot_e)
    story += make_tech_page(sty, "Curtailment Solar", fechamento_ym, sol, pld, monthly_sol, top_s, bot_s)
    story += make_companies_index_page(sty, empresas)

    for emp in empresas:
        df_emp, m_emp, ctop, cbot = comp_assets[emp]
        story += make_company_page(sty, emp, fechamento_ym, df_emp, pld, m_emp, ctop, cbot)

    def _cover(c, d):
        on_cover(c, d, fechamento_label)

    doc.build(story, onFirstPage=_cover, onLaterPages=on_later_pages)

    print(f"OK: gerado {out_pdf}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fechamento", required=True, help="YYYY-MM (ex: 2025-12)")
    ap.add_argument("--eolica",  default="dashboard/data/coff_eolica_monthly.csv")
    ap.add_argument("--solar",   default="dashboard/data/coff_solar_monthly.csv")
    ap.add_argument("--mapping", default="dashboard/data/mapping_citi.json")
    ap.add_argument("--pld",     default="dashboard/data/pld_monthly_avg.json")
    ap.add_argument("--out",     default=None)
    args = ap.parse_args()

    fechamento_ym = ym_from_cli(args.fechamento)
    fechamento_label = month_pt(fechamento_ym)

    if args.out is None:
        os.makedirs("reports", exist_ok=True)
        args.out = os.path.join("reports", f"Relatorio_ConstrainedOff_{fechamento_ym}.pdf")

    eol     = load_csv(args.eolica)
    sol     = load_csv(args.solar)
    mapping = load_mapping(args.mapping)
    pld     = load_pld_monthly(args.pld)

    eol = map_empresa_tipo(eol, mapping, "EOL")
    sol = map_empresa_tipo(sol, mapping, "SOL")

    eol = eol[eol["mes"] <= fechamento_ym].copy()
    sol = sol[sol["mes"] <= fechamento_ym].copy()
    all_df = pd.concat([eol, sol], ignore_index=True)

    if all_df.empty:
        raise SystemExit(f"Sem dados para fechamento <= {fechamento_ym}")

    build_pdf(fechamento_ym, args.out, eol, sol, all_df, pld)


if __name__ == "__main__":
    main()

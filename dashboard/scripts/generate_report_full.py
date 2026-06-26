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

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether
)
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.pdfgen import canvas as rlcanvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# =========================
# FONT SETUP
# =========================
# Diretório das fontes — relativo ao script
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_FONT_DIR = os.path.join(_SCRIPT_DIR, "fonts")

def _register_fonts():
    """
    Registra as fontes Leto no ReportLab e no Matplotlib.

    Estrutura esperada em <script_dir>/fonts/:
      ppneuemontreal-book.otf      (fonte principal — ReportLab não suporta CFF/OTF,
      ppneuemontreal-bold.otf       então usamos Google Sans Flex TTF para o PDF;
      ppneuemontreal-medium.otf     PP Neue Montreal é usada só no matplotlib)
      GoogleSansFlex_36pt-Regular.ttf
      GoogleSansFlex_36pt-Bold.ttf
      logo_preto.svg                (logo Leto)

    Se as fontes não forem encontradas, o script faz fallback para Helvetica/sans-serif.
    """
    global FONT_REGULAR, FONT_BOLD, FONT_MEDIUM, MPL_FONT

    # --- ReportLab: Google Sans Flex (TTF, suportado) ---
    gsf_reg  = os.path.join(_FONT_DIR, "GoogleSansFlex_36pt-Regular.ttf")
    gsf_bold = os.path.join(_FONT_DIR, "GoogleSansFlex_36pt-Bold.ttf")

    if os.path.exists(gsf_reg) and os.path.exists(gsf_bold):
        try:
            pdfmetrics.registerFont(TTFont("LetoRegular", gsf_reg))
            pdfmetrics.registerFont(TTFont("LetoBold",    gsf_bold))
            FONT_REGULAR = "LetoRegular"
            FONT_BOLD    = "LetoBold"
            FONT_MEDIUM  = "LetoRegular"
            print("ReportLab: Google Sans Flex registrada.")
        except Exception as e:
            print(f"Aviso: não foi possível registrar Google Sans Flex: {e}")
            FONT_REGULAR = "Helvetica"
            FONT_BOLD    = "Helvetica-Bold"
            FONT_MEDIUM  = "Helvetica"
    else:
        print(f"Aviso: fontes TTF não encontradas em {_FONT_DIR}. Usando Helvetica.")
        FONT_REGULAR = "Helvetica"
        FONT_BOLD    = "Helvetica-Bold"
        FONT_MEDIUM  = "Helvetica"

    # --- Matplotlib: PP Neue Montreal (OTF — suportado nativamente) ---
    nm_book = os.path.join(_FONT_DIR, "ppneuemontreal-book.otf")
    nm_bold = os.path.join(_FONT_DIR, "ppneuemontreal-bold.otf")
    nm_med  = os.path.join(_FONT_DIR, "ppneuemontreal-medium.otf")

    mpl_font_name = "sans-serif"
    for path in [nm_book, nm_bold, nm_med]:
        if os.path.exists(path):
            try:
                fm.fontManager.addfont(path)
                mpl_font_name = "PP Neue Montreal"
            except Exception as e:
                print(f"Aviso matplotlib font: {e}")

    MPL_FONT = mpl_font_name
    matplotlib.rcParams["font.family"] = MPL_FONT
    if MPL_FONT != "sans-serif":
        print(f"Matplotlib: usando '{MPL_FONT}'.")

# Inicializa (sobrescrito por _register_fonts)
FONT_REGULAR = "Helvetica"
FONT_BOLD    = "Helvetica-Bold"
FONT_MEDIUM  = "Helvetica"
MPL_FONT     = "sans-serif"


# =========================
# THEME — Leto Capital
# =========================
BLACK       = "#000000"
GREEN_LETO  = "#D8EEA9"   # verde Leto — accent capa
GRAY_LETO   = "#BDB7A7"   # cinza Leto
BROWN_LETO  = "#956A49"   # marrom Leto

# Paleta de dados — fundo BRANCO, barras em cores escuras
C_ENE       = "#7F9657"   # verde médio (legível no branco)
C_CNF       = "#44617B"   # azul acinzentado
C_REL       = "#956A49"   # marrom Leto
C_SEM       = "#BDB7A7"   # cinza Leto (mais claro, menor destaque)

# Linha de % corte nos gráficos
C_LINE_PCT  = "#1A1A1A"   # preto/quase-preto

# Neutros
N_900 = "#1A1A1A"
N_700 = "#505050"
N_500 = "#7D7D7D"
N_300 = "#BDB7A7"
N_100 = "#E8E5DD"

WHITE = colors.white
PAGE_W, PAGE_H = A4

GRID_COLOR   = colors.HexColor("#d0cdc8")
GRID_W_OUTER = 1.0
GRID_W_INNER = 0.4

# Matplotlib — fundo branco
MPL_BG   = "#FFFFFF"
MPL_TEXT = "#1A1A1A"
MPL_MUTED= "#7D7D7D"
MPL_GRID = "#E8E5DD"


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
    months = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho",
              "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
    y, m = ym.split("-")
    return f"{months[int(m)-1]}/{y}"

def ym_from_cli(fechamento_cli: str) -> str:
    x = fechamento_cli.strip().replace("_", "-")
    if len(x) == 7 and x[4] == "-":
        return x
    raise ValueError("Use --fechamento YYYY-MM (ex: 2025-12)")

def norm_key(s: str) -> str:
    if s is None:
        return ""
    return str(s).strip().upper().replace("\u00A0", " ")

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
        m = raw.get(usina) or norm.get(norm_key(usina))
        if not m:
            return "Não Mapeada"
        emp = str(m.get("empresa", "Não Mapeada") or "").strip()
        return emp or "Não Mapeada"

    def get_tipo(usina):
        m = raw.get(usina) or norm.get(norm_key(usina))
        if not m:
            return tipo_default
        t = str(m.get("tipo", tipo_default) or "").strip().upper()
        return t or tipo_default

    out = df.copy()
    out["cod_razaorestricao"] = out["cod_razaorestricao"].apply(norm_reason)
    out["curtailment_mwh"]   = out["curtailment_mwh"].apply(safe_num)
    out["generation_mwh"]    = out["generation_mwh"].apply(safe_num)
    out["empresa"]   = out["nom_usina"].map(get_emp)
    out["tipo_map"]  = out["nom_usina"].map(get_tipo)
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
def month_label(df_monthly_row) -> str:
    li  = getattr(df_monthly_row, "last_instante_max", "")
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
        df.pivot_table(index="mes", columns="cod_razaorestricao",
                       values="curtailment_mwh", aggfunc="sum").fillna(0)
    )
    out = df.groupby("mes", as_index=False).agg(
        corte=("curtailment_mwh","sum"), ref=("generation_mwh","sum"))
    out = out.merge(piv.reset_index(), on="mes", how="left").fillna(0)
    for rr in ["ENE","CNF","REL","SEM"]:
        if rr not in out.columns:
            out[rr] = 0.0
    out["pct"] = np.where(out["ref"] > 0, out["corte"]/out["ref"], 0.0)
    li = (df.groupby("mes")["last_instante"].max()
            .reset_index().rename(columns={"last_instante":"last_instante_max"}))
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
    ref   = float(df["generation_mwh"].sum())
    pct   = (corte/ref) if ref > 0 else 0.0
    imp   = impact_rs(df, pld) / 1e6
    return corte, ref, pct, imp

def top_bottom_companies_period(all_df, pld, fechamento_ym):
    df  = all_df[all_df["mes"] <= fechamento_ym].copy()
    grp = df.groupby("empresa", as_index=False).agg(
        corte=("curtailment_mwh","sum"), ref=("generation_mwh","sum"))
    grp["pct"] = np.where(grp["ref"]>0, grp["corte"]/grp["ref"], np.nan)
    bad = {"Kroma","nan","Não Mapeada","Nao Mapeada","NÃO MAPEADA","N├úO MAPEADA"}
    grp = grp[~grp["empresa"].apply(lambda x: str(x).strip() in bad)].copy()
    gp  = grp.dropna(subset=["pct"]).copy()
    top_pct = gp.sort_values("pct", ascending=False).head(3)[["empresa","pct"]].values.tolist()
    bot_pct = gp.sort_values("pct", ascending=True ).head(3)[["empresa","pct"]].values.tolist()
    top_vol = grp.sort_values("corte", ascending=False).head(3)[["empresa","corte"]].values.tolist()
    bot_vol = grp.sort_values("corte", ascending=True ).head(3)[["empresa","corte"]].values.tolist()
    last  = df[df["mes"] == fechamento_ym].copy()
    grpL  = last.groupby("empresa", as_index=False).agg(
        corte=("curtailment_mwh","sum"), ref=("generation_mwh","sum"))
    grpL["pct"] = np.where(grpL["ref"]>0, grpL["corte"]/grpL["ref"], np.nan)
    grpL  = grpL[~grpL["empresa"].apply(lambda x: str(x).strip() in bad)].copy()
    gpL   = grpL.dropna(subset=["pct"]).copy()
    top_pct_L = gpL.sort_values("pct", ascending=False).head(3)[["empresa","pct"]].values.tolist()
    bot_pct_L = gpL.sort_values("pct", ascending=True ).head(3)[["empresa","pct"]].values.tolist()
    return {
        "top_pct":   [(a,float(b)) for a,b in top_pct],
        "bot_pct":   [(a,float(b)) for a,b in bot_pct],
        "top_vol":   [(a,float(b)) for a,b in top_vol],
        "bot_vol":   [(a,float(b)) for a,b in bot_vol],
        "top_pct_L": [(a,float(b)) for a,b in top_pct_L],
        "bot_pct_L": [(a,float(b)) for a,b in bot_pct_L],
    }


# =========================
# CHARTS — fundo branco, paleta Leto escura
# =========================
def _apply_leto_style(fig, ax1, ax2=None):
    """Fundo branco, tipografia PP Neue Montreal, grid sutil."""
    fig.patch.set_facecolor(MPL_BG)
    for ax in ([ax1] + ([ax2] if ax2 else [])):
        ax.set_facecolor(MPL_BG)
        ax.tick_params(colors=MPL_MUTED, labelsize=8)
        ax.xaxis.label.set_color(MPL_MUTED)
        ax.yaxis.label.set_color(MPL_MUTED)
        ax.title.set_color(MPL_TEXT)
        for spine in ax.spines.values():
            spine.set_edgecolor(MPL_GRID)
        ax.grid(True, axis="y", color=MPL_GRID, alpha=1.0, linewidth=0.6)
        ax.set_axisbelow(True)

def make_chart_monthly(df_monthly: pd.DataFrame, title: str, outpath: str, compact=False):
    x = [month_label(r) for r in df_monthly.itertuples(index=False)]
    corte_gwh = df_monthly["corte"].values / 1e3
    pct       = df_monthly["pct"].values * 100.0

    h = 3.25 if compact else 3.55
    fig, ax1 = plt.subplots(figsize=(10.8, h), dpi=160)

    ax1.bar(x, corte_gwh, color=C_ENE, zorder=3)
    ax1.set_ylabel("Corte (GWh)", color=MPL_MUTED, fontsize=9)
    ax1.tick_params(axis="x", rotation=45, labelsize=8)

    ax2 = ax1.twinx()
    ax2.plot(x, pct, marker="o", linewidth=2, color=C_LINE_PCT, zorder=4)
    ax2.set_ylabel("% Curtailment", color=MPL_MUTED, fontsize=9)
    ax2.set_ylim(0, max(5, float(pct.max())*1.15 if len(pct) else 5))
    ax2.tick_params(colors=MPL_MUTED, labelsize=8)
    for spine in ax2.spines.values():
        spine.set_edgecolor(MPL_GRID)

    ax1.set_title(title, loc="left", fontsize=11, fontweight="bold", color=MPL_TEXT, pad=8)
    _apply_leto_style(fig, ax1)  # ax2 styled manually above

    fig.tight_layout()
    fig.savefig(outpath, bbox_inches="tight", facecolor=MPL_BG, dpi=160)
    plt.close(fig)

def make_chart_build_up_pp(df_monthly: pd.DataFrame, title: str, outpath: str,
                            hide_sem_legend=True, compact=False):
    x   = [month_label(r) for r in df_monthly.itertuples(index=False)]
    ref = df_monthly["ref"].values

    def pp(rr):
        return np.where(ref > 0, df_monthly[rr].values / ref * 100.0, 0.0)

    ene, cnf, rel, sem = pp("ENE"), pp("CNF"), pp("REL"), pp("SEM")

    h = 3.10 if compact else 3.35
    fig, ax = plt.subplots(figsize=(10.8, h), dpi=160)

    bottom = np.zeros_like(ene)
    ax.bar(x, ene, color=C_ENE, label="ENE", zorder=3)
    bottom += ene
    ax.bar(x, cnf, bottom=bottom, color=C_CNF, label="CNF", zorder=3)
    bottom += cnf
    ax.bar(x, rel, bottom=bottom, color=C_REL, label="REL", zorder=3)
    bottom += rel
    ax.bar(x, sem, bottom=bottom, color=C_SEM, label="SEM", zorder=3)

    ax.set_ylabel("% corte (pp)", color=MPL_MUTED, fontsize=9)
    ax.tick_params(axis="x", rotation=45, labelsize=8)
    ax.set_title(title, loc="left", fontsize=11, fontweight="bold", color=MPL_TEXT, pad=8)

    handles, labels = ax.get_legend_handles_labels()
    if hide_sem_legend and "SEM" in labels:
        i = labels.index("SEM")
        handles.pop(i); labels.pop(i)

    ax.legend(handles, labels, ncol=4, fontsize=9, frameon=True,
              facecolor=MPL_BG, edgecolor=MPL_GRID, labelcolor=MPL_TEXT)

    _apply_leto_style(fig, ax)
    fig.tight_layout()
    fig.savefig(outpath, bbox_inches="tight", facecolor=MPL_BG, dpi=160)
    plt.close(fig)


# =========================
# PDF STYLES — Leto
# =========================
def styles():
    ss   = getSampleStyleSheet()
    base = ss["Normal"]
    base.fontName = FONT_REGULAR
    base.fontSize = 10
    base.leading  = 13

    h1 = ParagraphStyle("H1", parent=base, fontName=FONT_BOLD,
                         fontSize=16, leading=20, spaceAfter=8)
    h2 = ParagraphStyle("H2", parent=base, fontName=FONT_BOLD,
                         fontSize=12, leading=15, spaceAfter=6)
    small = ParagraphStyle("SMALL", parent=base, fontName=FONT_REGULAR,
                            fontSize=8.5, leading=11,
                            textColor=colors.HexColor(N_500))
    link  = ParagraphStyle("LINK",  parent=base, fontName=FONT_REGULAR,
                            fontSize=11, leading=13,
                            textColor=colors.HexColor(N_900))
    link_bold = ParagraphStyle("LINKB", parent=base, fontName=FONT_BOLD,
                                fontSize=11, leading=13,
                                textColor=colors.HexColor(N_900))
    return {"base":base, "h1":h1, "h2":h2, "small":small,
            "link":link, "link_bold":link_bold}

def table_style_strong(header_rows=1):
    """Header: fundo preto, texto verde Leto. Body: fundo branco, texto escuro."""
    return TableStyle([
        ("GRID",        (0,0), (-1,-1), GRID_W_INNER, GRID_COLOR),
        ("BOX",         (0,0), (-1,-1), GRID_W_OUTER, GRID_COLOR),
        ("BACKGROUND",  (0,0), (-1,header_rows-1), colors.HexColor(BLACK)),
        ("TEXTCOLOR",   (0,0), (-1,header_rows-1), colors.HexColor(GREEN_LETO)),
        ("FONTNAME",    (0,0), (-1,header_rows-1), FONT_BOLD),
        ("FONTSIZE",    (0,0), (-1,header_rows-1), 10),
        ("FONTNAME",    (0,header_rows), (-1,-1), FONT_REGULAR),
        ("FONTSIZE",    (0,header_rows), (-1,-1), 10),
        ("TEXTCOLOR",   (0,header_rows), (-1,-1), colors.HexColor(N_900)),
        ("BACKGROUND",  (0,header_rows), (-1,-1), colors.white),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN",       (0,0), (-1,-1), "CENTER"),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING",(0,0), (-1,-1), 6),
        ("TOPPADDING",  (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
    ])

def table_style_light_box():
    return TableStyle([
        ("GRID",        (0,0), (-1,-1), GRID_W_INNER, GRID_COLOR),
        ("BOX",         (0,0), (-1,-1), GRID_W_OUTER, GRID_COLOR),
        ("BACKGROUND",  (0,0), (-1,-1), colors.white),
        ("VALIGN",      (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING",(0,0), (-1,-1), 8),
        ("TOPPADDING",  (0,0), (-1,-1), 7),
        ("BOTTOMPADDING",(0,0),(-1,-1), 7),
    ])


# =========================
# PAGE DECORATORS — Leto
# =========================
def _draw_logo_on_canvas(c: rlcanvas.Canvas, x: float, y: float,
                          target_w: float, fill_color: colors.Color):
    """Renderiza o SVG do logo Leto no canvas, recolorido."""
    svg_path = os.path.join(_SCRIPT_DIR, "logo_preto.svg")
    if not os.path.exists(svg_path):
        # fallback: texto
        c.setFont(FONT_BOLD, 22)
        c.setFillColor(fill_color)
        c.drawString(x, y, "Leto Capital")
        return
    try:
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPDF

        drawing = svg2rlg(svg_path)
        if drawing is None:
            raise ValueError("svg2rlg retornou None")

        # Recolore
        _recolor_drawing(drawing, fill_color)

        scale = target_w / drawing.width
        drawing.width  *= scale
        drawing.height *= scale
        drawing.transform = (scale, 0, 0, scale, x, y)
        renderPDF.draw(drawing, c, 0, 0)
    except Exception as e:
        print(f"Aviso logo: {e}")
        c.setFont(FONT_BOLD, 22)
        c.setFillColor(fill_color)
        c.drawString(x, y, "Leto Capital")

def _recolor_drawing(drawing, new_color):
    for item in getattr(drawing, "contents", []):
        if hasattr(item, "fillColor") and item.fillColor is not None:
            item.fillColor = new_color
        if hasattr(item, "strokeColor") and item.strokeColor is not None:
            item.strokeColor = new_color
        _recolor_drawing(item, new_color)

def on_cover(c: rlcanvas.Canvas, _doc, fechamento_label: str):
    """Capa preta. Logo Leto em verde Leto no topo. Título branco."""
    # Fundo preto
    c.setFillColor(colors.HexColor(BLACK))
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    c.saveState()

    # Logo Leto (~7 cm de largura) no topo esquerdo
    logo_w = 7.0 * cm
    logo_y = PAGE_H - 4.0 * cm
    _draw_logo_on_canvas(c, 2.0*cm, logo_y,
                         logo_w, colors.HexColor(GREEN_LETO))

    # Título
    c.setFillColor(colors.white)
    c.setFont(FONT_BOLD, 26)
    c.drawString(2.0*cm, PAGE_H - 8.7*cm, "Relatório de Constrained-Off")

    # Data
    c.setFont(FONT_REGULAR, 12)
    c.setFillColor(colors.HexColor(GRAY_LETO))
    c.drawString(2.0*cm, PAGE_H - 10.2*cm, fechamento_label)

    c.restoreState()

def on_later_pages(c: rlcanvas.Canvas, _doc):
    """Páginas internas brancas com header discreto."""
    c.setFillColor(colors.white)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    c.saveState()
    # Logo pequena no header (preta, sobre fundo branco)
    logo_w = 3.5 * cm
    logo_y = PAGE_H - 1.4 * cm
    _draw_logo_on_canvas(c, 2.0*cm, logo_y,
                         logo_w, colors.HexColor(N_900))
    c.restoreState()


# =========================
# BUILD PAGES  (lógica inalterada)
# =========================
def make_overview_page(sty, fechamento_ym, all_df, monthly_all,
                       charts_top, charts_bottom, pld):
    corte, ref, pct, imp = kpis(all_df, pld)
    yoy_pp = yoy_pp_last_month(monthly_all, fechamento_ym)
    fechamento_label = month_pt(fechamento_ym)
    tb = top_bottom_companies_period(all_df, pld, fechamento_ym)

    kpi_header = ["Energia cortada\nMWh","Geração\nMWh","% Curtailment\n%",
                  "Impacto financeiro\nR$ mm","YoY (% corte)\n∆ pp vs mesmo mês"]
    kpi_row = [br_int(corte), br_int(ref), br_pct(pct), br_float(imp,2),
               "—" if np.isnan(yoy_pp) else br_float(yoy_pp,1)]
    kpi_tbl = Table([kpi_header, kpi_row],
                    colWidths=[3.2*cm,3.2*cm,3.0*cm,3.5*cm,4.5*cm])
    kpi_tbl.setStyle(table_style_strong(header_rows=1))

    def bullet_box(title, items):
        lines = [f"<b>{title}</b>"] + [f"• {x}" for x in items]
        p = Paragraph("<br/>".join(lines), sty["base"])
        t = Table([[p]], colWidths=[8.2*cm])
        t.setStyle(table_style_light_box())
        return t

    box1 = bullet_box("Maior % de corte (histórico)",
                      [f"{a}: {br_float(b*100,1)}%" for a,b in tb["top_pct"]])
    box2 = bullet_box("Menor % de corte (histórico)",
                      [f"{a}: {br_float(b*100,1)}%" for a,b in tb["bot_pct"]])
    box3 = bullet_box("Maior volume cortado (histórico)",
                      [f"{a}: {br_float(gwh_from_mwh(v),3)} GWh" for a,v in tb["top_vol"]])
    box4 = bullet_box("Menor volume cortado (histórico)",
                      [f"{a}: {br_int(v)} MWh" for a,v in tb["bot_vol"]])
    box5 = bullet_box(f"Maior % de corte (último mês — {fechamento_label})",
                      [f"{a}: {br_float(b*100,1)}%" for a,b in tb["top_pct_L"]])
    box6 = bullet_box(f"Menor % de corte (último mês — {fechamento_label})",
                      [f"{a}: {br_float(b*100,1)}%" for a,b in tb["bot_pct_L"]])

    grid = Table([[box1,box2],[box5,box6],[box3,box4]],
                 colWidths=[8.2*cm,8.2*cm], hAlign="LEFT")
    grid.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),6),  ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ]))

    img_top = Image(charts_top,  width=17.2*cm, height=5.6*cm)
    img_bot = Image(charts_bottom, width=17.2*cm, height=5.3*cm)

    foot = Paragraph(
        f"* Histórico desde Jan/2025 · Fechamento: {fechamento_label}<br/>"
        "* Impacto financeiro estimado assumindo PLD médio mensal.<br/>"
        "* Ranking restrito às empresas acompanhadas internamente.",
        sty["small"])

    story = [Paragraph("Visão geral", sty["h1"]), Spacer(1,6)]
    story.append(KeepTogether([
        kpi_tbl, Spacer(1,8), grid, Spacer(1,10),
        img_top, Spacer(1,8), img_bot, Spacer(1,6), foot
    ]))
    story.append(PageBreak())
    return story

def make_tech_page(sty, titulo, fechamento_ym, df_tipo, pld,
                   monthly_tipo, chart_top, chart_bottom):
    fechamento_label = month_pt(fechamento_ym)
    corte, ref, pct, imp = kpis(df_tipo, pld)
    last_df = df_tipo[df_tipo["mes"]==fechamento_ym].copy()
    corte_m, ref_m, pct_m, imp_m = kpis(last_df,pld) if not last_df.empty else (0,0,0,0)
    yoy_pp = yoy_pp_last_month(monthly_tipo, fechamento_ym)

    data = [
        ["Período\n(até fechamento)","Energia cortada\nMWh","% Curtailment\n%",
         "Impacto financeiro\nR$ mm","YoY último mês\n∆ pp"],
        ["Histórico", br_int(corte), br_pct(pct), br_float(imp,2),
         "—" if (yoy_pp is None or np.isnan(yoy_pp)) else br_float(yoy_pp,1)],
        [fechamento_label, br_int(corte_m), br_pct(pct_m), br_float(imp_m,2),
         "—" if np.isnan(yoy_pp) else br_float(yoy_pp,1)],
    ]
    tbl = Table(data, colWidths=[4.4*cm,3.2*cm,3.0*cm,3.6*cm,3.0*cm])
    tbl.setStyle(table_style_strong(header_rows=1))

    img1 = Image(chart_top,    width=17.2*cm, height=6.3*cm)
    img2 = Image(chart_bottom, width=17.2*cm, height=5.8*cm)

    story = [Paragraph(titulo, sty["h1"]), Spacer(1,10),
             tbl, Spacer(1,10), img1, Spacer(1,10), img2, PageBreak()]
    return story

def company_anchor_name(emp):
    return "EMP_" + "".join(c for c in emp.upper() if c.isalnum())

def make_companies_index_page(sty, empresas):
    story = [
        Paragraph("Empresas", sty["h1"]),
        Paragraph("Clique em uma empresa para ir direto para a página.", sty["base"]),
        Spacer(1,12),
    ]
    cols = 3
    rows = int(np.ceil(len(empresas)/cols))
    data, idx = [], 0
    for r in range(rows):
        row = []
        for c in range(cols):
            if idx < len(empresas):
                emp = empresas[idx]
                anchor = company_anchor_name(emp)
                row.append(Paragraph(
                    f'<link href="#{anchor}"><u><b>{emp.upper()}</b></u></link>',
                    sty["link_bold"]))
            else:
                row.append("")
            idx += 1
        data.append(row)

    t = Table(data, colWidths=[5.4*cm,5.4*cm,5.4*cm], rowHeights=[1.2*cm]*rows)
    t.setStyle(TableStyle([
        ("GRID",(0,0),(-1,-1),GRID_W_INNER,GRID_COLOR),
        ("BOX",(0,0),(-1,-1),GRID_W_OUTER,GRID_COLOR),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor(N_100)),
    ]))
    story += [t, PageBreak()]
    return story

def make_company_page(sty, emp, fechamento_ym, df_emp, pld, monthly_emp,
                      chart_top, chart_bottom):
    fechamento_label = month_pt(fechamento_ym)
    corte, ref, pct, imp = kpis(df_emp, pld)
    df_relcnf = df_emp[df_emp["cod_razaorestricao"].isin(["REL","CNF"])].copy()
    reimb = impact_rs(df_relcnf, pld) / 1e6
    df_m  = df_emp[df_emp["mes"]==fechamento_ym].copy()
    corte_m,ref_m,pct_m,imp_m = kpis(df_m,pld) if not df_m.empty else (0,0,0,0)
    df_m_rc = df_m[df_m["cod_razaorestricao"].isin(["REL","CNF"])].copy()
    reimb_m = (impact_rs(df_m_rc,pld)/1e6) if not df_m_rc.empty else 0.0
    yoy_pp  = yoy_pp_last_month(monthly_emp, fechamento_ym)

    anchor = company_anchor_name(emp)
    story  = [
        Paragraph(f'<a name="{anchor}"/>', sty["base"]),
        Paragraph(emp, sty["h1"]),
        Spacer(1,14),
    ]

    hist = [
        ["% Curtailment","Energia cortada\n(MWh)","Impacto financeiro\n(R$ mm)","Reembolso est.\n(R$ mm)"],
        [br_pct(pct), br_int(corte), br_float(imp,2), br_float(reimb,2)],
    ]
    hist_tbl = Table(hist, colWidths=[3.6*cm,5.2*cm,4.2*cm,4.2*cm])
    hist_tbl.setStyle(table_style_strong(header_rows=1))
    story += [Paragraph("Análise Histórica",sty["h2"]), Spacer(1,6), hist_tbl, Spacer(1,12)]

    m = [
        ["% Curtailment","Energia cortada\n(MWh)","Impacto financeiro\n(R$ mm)",
         "Reembolso est.\n(R$ mm)","YoY último mês\n(pp)"],
        [br_pct(pct_m), br_int(corte_m), br_float(imp_m,2), br_float(reimb_m,2),
         "—" if (yoy_pp is None or np.isnan(yoy_pp)) else br_float(yoy_pp,1)],
    ]
    m_tbl = Table(m, colWidths=[3.1*cm,4.2*cm,3.7*cm,3.7*cm,3.5*cm])
    m_tbl.setStyle(table_style_strong(header_rows=1))
    story += [Paragraph(f"Análise {fechamento_label}",sty["h2"]), Spacer(1,6), m_tbl, Spacer(1,12)]

    img1 = Image(chart_top,    width=17.2*cm, height=6.1*cm)
    img2 = Image(chart_bottom, width=17.2*cm, height=5.7*cm)
    story += [img1, Spacer(1,10), img2, PageBreak()]
    return story


# =========================
# MAIN PDF BUILD
# =========================
def build_pdf(fechamento_ym, out_pdf, eol, sol, all_df, pld):
    sty = styles()
    fechamento_label = month_pt(fechamento_ym)

    out_dir   = os.path.dirname(out_pdf) or "."
    charts_dir = os.path.join(out_dir, f"_charts_{fechamento_ym}")
    os.makedirs(charts_dir, exist_ok=True)

    monthly_all = agg_monthly(all_df)
    monthly_eol = agg_monthly(eol)
    monthly_sol = agg_monthly(sol)

    top_all = os.path.join(charts_dir, "total_top.png")
    bot_all = os.path.join(charts_dir, "total_bottom.png")
    make_chart_monthly(monthly_all, "Curtailment total — Corte (GWh) e %", top_all, compact=True)
    make_chart_build_up_pp(monthly_all, "Quebra por modalidade — build-up (% p.p.)", bot_all, compact=True)

    top_e = os.path.join(charts_dir, "eol_top.png")
    bot_e = os.path.join(charts_dir, "eol_bottom.png")
    make_chart_monthly(monthly_eol, "Curtailment eólico — Corte (GWh) e %", top_e)
    make_chart_build_up_pp(monthly_eol, "Quebra por modalidade — build-up (% p.p.)", bot_e)

    top_s = os.path.join(charts_dir, "sol_top.png")
    bot_s = os.path.join(charts_dir, "sol_bottom.png")
    make_chart_monthly(monthly_sol, "Curtailment solar — Corte (GWh) e %", top_s)
    make_chart_build_up_pp(monthly_sol, "Quebra por modalidade — build-up (% p.p.)", bot_s)

    bad = {"Kroma","nan","Não Mapeada","Nao Mapeada","NÃO MAPEADA","N├úO MAPEADA"}
    empresas = sorted(
        [e for e in all_df["empresa"].dropna().unique() if str(e).strip() not in bad],
        key=lambda x: str(x).upper())

    comp_assets = {}
    for emp in empresas:
        df_emp = all_df[all_df["empresa"]==emp].copy()
        m_emp  = agg_monthly(df_emp)
        ctop   = os.path.join(charts_dir, f"{company_anchor_name(emp)}_top.png")
        cbot   = os.path.join(charts_dir, f"{company_anchor_name(emp)}_bot.png")
        make_chart_monthly(m_emp, f"{emp} — Corte (GWh) e %", ctop)
        make_chart_build_up_pp(m_emp, f"{emp} — Quebra por modalidade (% p.p.)", cbot)
        comp_assets[emp] = (df_emp, m_emp, ctop, cbot)

    doc = SimpleDocTemplate(
        out_pdf, pagesize=A4,
        leftMargin=2.0*cm, rightMargin=2.0*cm,
        topMargin=1.8*cm, bottomMargin=1.6*cm,
        title="Relatório de Constrained-Off — Leto Capital")

    story = [Spacer(1,1), PageBreak()]
    story += make_overview_page(sty, fechamento_ym, all_df, monthly_all, top_all, bot_all, pld)
    story += make_tech_page(sty, "Curtailment Eólico", fechamento_ym, eol, pld, monthly_eol, top_e, bot_e)
    story += make_tech_page(sty, "Curtailment Solar",  fechamento_ym, sol, pld, monthly_sol, top_s, bot_s)
    story += make_companies_index_page(sty, empresas)
    for emp in empresas:
        df_emp, m_emp, ctop, cbot = comp_assets[emp]
        story += make_company_page(sty, emp, fechamento_ym, df_emp, pld, m_emp, ctop, cbot)

    doc.build(story,
              onFirstPage=lambda c,d: on_cover(c, d, fechamento_label),
              onLaterPages=on_later_pages)
    print(f"OK: gerado {out_pdf}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fechamento", required=True)
    ap.add_argument("--eolica",  default="dashboard/data/coff_eolica_monthly.csv")
    ap.add_argument("--solar",   default="dashboard/data/coff_solar_monthly.csv")
    ap.add_argument("--mapping", default="dashboard/data/mapping_citi.json")
    ap.add_argument("--pld",     default="dashboard/data/pld_monthly_avg.json")
    ap.add_argument("--out",     default=None)
    args = ap.parse_args()

    _register_fonts()

    fechamento_ym = ym_from_cli(args.fechamento)
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

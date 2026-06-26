/* ===============================
   CONFIG
================================ */
const CSV_PATH = "./data/coff_eolica_monthly.csv";   // eólico (já existe)
const CSV_SOLAR_PATH = "./data/coff_solar_monthly.csv"; // ✅ NOVO (solar)
const MAP_PATH = "./data/mapping_citi.json";



// ====== PLD (via arquivos estáticos gerados no build) ======
let _PLD_MONTHLY = null;
let _PLD_META = null;

async function _loadPLDMonthlyOnce(){
  if (_PLD_MONTHLY) return _PLD_MONTHLY;
  const resp = await fetch("./data/pld_monthly_avg.json", { cache:"no-store" });
  if (!resp.ok) throw new Error("Não achei pld_monthly_avg.json no site");
  _PLD_MONTHLY = await resp.json(); // { "2025-01": 123.45, ... }
  return _PLD_MONTHLY;
}

async function _loadPLDMetaOnce(){
  if (_PLD_META) return _PLD_META;
  const resp = await fetch("./data/pld_meta.json", { cache:"no-store" });
  if (!resp.ok) throw new Error("Não achei pld_meta.json no site");
  _PLD_META = await resp.json(); // { max_dia: "YYYY-MM-DD", updated_at: "..." }
  return _PLD_META;
}

async function buscarPLDMonthlyAvg(ym) {
  const j = await _loadPLDMonthlyOnce();
  const v = j[ym];
  if (v === null || v === undefined || v === "") return null;
  return Number(v);
}

async function buscarPLDMaxDia(){
  const meta = await _loadPLDMetaOnce();
  return meta.max_dia || null;
}

async function loadOptionalCSV(path) {
  try {
    const resp = await fetch(path, { cache:"no-store" });
    if (!resp.ok) return null;
    const text = await resp.text();

    const parsed = Papa.parse(text, {
      header: true,
      skipEmptyLines: true
    });

    return parsed.data || [];
  } catch (e) {
    console.warn("CSV opcional não carregado:", path, e);
    return null;
  }
}




/* ===============================
   HELPERS
================================ */


function fmtBRLmm(x){
  // x em R$ (reais)
  const mm = x / 1e6;
  return mm.toLocaleString("pt-BR", {minimumFractionDigits:2, maximumFractionDigits:2});
}

function isoDateFromLastInstante(lastInstante){
  // lastInstante: "YYYY-MM-DD HH:MM:SS"
  if(!lastInstante) return null;
  const iso = lastInstante.replace(" ", "T");
  const d = new Date(iso);
  if(isNaN(d)) return null;
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth()+1).padStart(2,"0");
  const dd = String(d.getDate()).padStart(2,"0");
  return `${yyyy}-${mm}-${dd}`;
}

function pldMediaDiariaFromHoras(pldRows){
  // pldRows: [{hora, pld_medio}, ...]
  if(!pldRows || !pldRows.length) return null;
  const s = pldRows.reduce((acc,r)=>acc + Number(r.pld_medio || 0), 0);
  return s / pldRows.length;
}

function toNum(x){
  if(x === null || x === undefined || x === "") return 0;
  const s = String(x).trim();

  // se tem vírgula, é pt-BR (1.234,56)
  if (s.includes(",")){
    const n = Number(s.replace(/\./g,"").replace(",", "."));
    return Number.isFinite(n) ? n : 0;
  }

  // senão, é decimal com ponto (1234.56)
  const n = Number(s);
  return Number.isFinite(n) ? n : 0;

}
function fmtPct(x){
  return (x*100).toLocaleString("pt-BR", {minimumFractionDigits:2, maximumFractionDigits:2}) + "%";
}
function fmtMWh(x){
  return x.toLocaleString("pt-BR", {maximumFractionDigits:0});
}
function uniq(arr){
  return [...new Set(arr)].filter(Boolean).sort((a,b)=>String(a).localeCompare(String(b)));
}
function reasonNorm(x){
  const v = (x ?? "").toString().trim().toUpperCase();

  if(!v || v === "NAN" || v === "NONE" || v === "NULL") return "SEM";

  if(v === "ENE") return "ENE";
  if(v === "REL") return "REL";

  // às vezes vem "CNF " ou "CONF"
  if(v === "CNF" || v === "CONF") return "CNF";

  return "SEM";
}



function monthLabelFromLastInstante(ym, lastInstante){
  // quer mostrar último dia disponível do mês (ou último instante)
  // lastInstante vem do build como string "YYYY-MM-DD HH:MM:SS" ou vazio.
  if(!lastInstante) return ym;
  const d = new Date(lastInstante.replace(" ", "T"));
  if(isNaN(d)) return ym;
  const dd = String(d.getDate()).padStart(2,"0");
  const mm = String(d.getMonth()+1).padStart(2,"0");
  const yy = d.getFullYear();
  return `${yy}-${mm}-${dd}`;
}
function lastDayOfMonthISO(ym){
  // ym = "2025-12"
  if(!ym || !/^\d{4}-\d{2}$/.test(ym)) return null;
  const [y, m] = ym.split("-").map(Number);
  // dia 0 do mês seguinte = último dia do mês atual
  const d = new Date(y, m, 0);
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth()+1).padStart(2,"0");
  const dd = String(d.getDate()).padStart(2,"0");
  return `${yyyy}-${mm}-${dd}`;
}

function getSelectedMulti(selectEl){
  return Array.from(selectEl.selectedOptions).map(o=>o.value);
}

function setStatus(msg){
  const el = document.getElementById("status");
  if(el) el.textContent = msg;
}

function setPLDUpdatedText(txt){
  const el = document.getElementById("pld-date");
  if(el) el.textContent = `PLD atualizado até: ${txt || "—"}`;
}

function setONSUpdatedText(txt){
  const el = document.getElementById("ons-date");
  if(el) el.textContent = `Dados ONS atualizados até: ${txt || "—"}`;
}

function safeGet(id){
  const el = document.getElementById(id);
  if(!el) console.warn("Elemento não encontrado:", id);
  return el;
}

function pickFirstKey(obj, patterns){
  if (!obj) return null;
  const keys = Object.keys(obj);
  for (const p of patterns){
    const re = new RegExp(p, "i");
    const k = keys.find(x => re.test(x));
    if (k) return k;
  }
  return null;
}


/* ===============================
   LOAD DATA (monthly + mapping)
================================ */
async function loadAll(){
  const [csvText, mapText, solarRowsRaw] = await Promise.all([
    fetch(CSV_PATH, {cache:"no-store"}).then(r => r.text()),
    fetch(MAP_PATH, {cache:"no-store"}).then(r => r.text()),
    loadOptionalCSV(CSV_SOLAR_PATH) // ✅ novo (pode vir null)
  ]);

  const parsed = Papa.parse(csvText, {
    header: true,
    skipEmptyLines: true
  });

  const rowsEolica = (parsed.data || []).map(r => {
  // Suporta 2 formatos:
  // (A) antigo: mes/nom_usina/curtailment_mwh/generation_mwh/last_instante/cod_razaorestricao
  // (B) novo (Actions): ym/empresa/coff_mwh/ger_mwh/coff_pct

  const mes = ((r.mes ?? r.ym) || "").toString().trim();

  // Se vier agregado por empresa (novo), usamos empresa como "nom_usina" só pra não sumir do dashboard
  const nom_usina = ((r.nom_usina ?? r.empresa) || "").toString().trim();

  const corte = toNum(r.curtailment_mwh ?? r.coff_mwh);
  const ger   = toNum(r.generation_mwh ?? r.ger_mwh);

  const last_inst = ((r.last_instante ?? "") || "").toString().trim();

  // No formato novo não existe razão -> SEM
  const razao = (r.cod_razaorestricao !== undefined) ? reasonNorm(r.cod_razaorestricao) : "SEM";

  return {
    mes,
    nom_usina,
    cod_razaorestricao: razao,
    curtailment_mwh: corte,
    generation_mwh: ger,
    last_instante: last_inst,
    tipo_map: "EOL"
  };
});



// ---------- SOLAR (opcional) ----------
const rowsSolar = (solarRowsRaw || []).map(r => ({
  mes: (r.mes || "").trim(),                  // igual ao eólico
  nom_usina: (r.nom_usina || "").trim(),      // igual ao eólico
  cod_razaorestricao: reasonNorm(r.cod_razaorestricao), // ✅ pega ENE/CNF/REL
  curtailment_mwh: toNum(r.curtailment_mwh),
  generation_mwh: toNum(r.generation_mwh),
  last_instante: (r.last_instante || "").trim(),
  tipo_map: "SOL"
}));

// corta solar a partir de 2025-01
const rowsSolar2025 = rowsSolar.filter(r => r.mes >= "2025-01");


  
  // junta (se não tiver solar, rowsSolar é [])
  const rows = rowsEolica.concat(rowsSolar2025);


  let mapping = {};
  try { mapping = JSON.parse(mapText); } catch(e){ mapping = {}; }

const mappingNorm = {};
for (const k in mapping) {
  const nk = normKey(k);
  if (!(nk in mappingNorm)) mappingNorm[nk] = mapping[k];
}


function normKey(s){
  return (s || "")
    .toString()
    .trim()
    .toUpperCase()
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "") // tira acentos
    .replace(/\s+/g, " "); // espaços múltiplos
}


rows.forEach(r => {
  const key = r.nom_usina;
  const m = mapping[key] || mappingNorm[normKey(key)];
  r.empresa = (m && m.empresa) ? m.empresa : "Não mapeada";

  // NÃO sobrescreve r.tipo_map (vem da fonte: EOL ou SOL)
});



  return rows;
}



/* ===============================
   AGGREGATE FOR CHARTS
   - soma mensal (corte/ref) por mês
   - e também por razão (ENE/CNF/REL/SEM)
================================ */
function aggregateMonthly(rows){
  const by = new Map();

  for(const r of rows){
    if(!r.mes) continue;
    const key = r.mes;

    if(!by.has(key)){
      by.set(key, {
        mes: r.mes,
        last_instante: r.last_instante || "",
        corte: 0,
        ref: 0,
        ENE: 0,
        CNF: 0,
        REL: 0,
        SEM: 0
     });

    }
    const agg = by.get(key);

    // guarda último instante do mês (para label/tooltip)
    if(r.last_instante && (!agg.last_instante || r.last_instante > agg.last_instante)){
      agg.last_instante = r.last_instante;
    }

    agg.corte += r.curtailment_mwh;
    agg.ref   += r.generation_mwh;

    const rr = reasonNorm(r.cod_razaorestricao);
    agg[rr] += r.curtailment_mwh;
  }

  const out = Array.from(by.values()).sort((a,b)=>a.mes.localeCompare(b.mes));
  out.forEach(d => d.pct = d.ref > 0 ? d.corte/d.ref : 0);
  return out;
}

/* ===============================
   UI POPULATION
================================ */
function fillSelectSingle(id, values, includeAll=true){
  const el = safeGet(id);
  if(!el) return;

  el.innerHTML = "";

  if(includeAll){
    const o = document.createElement("option");
    o.value = "ALL";
    o.textContent = "Todas";
    el.appendChild(o);
  }

  for(const v of values){
    const o = document.createElement("option");
    o.value = v;
    o.textContent = v;
    el.appendChild(o);
  }
}

function fillSelectMulti(id, values){
  const el = safeGet(id);
  if(!el) return;

  el.innerHTML = "";
  for(const v of values){
    const o = document.createElement("option");
    o.value = v;
    o.textContent = v;
    el.appendChild(o);
  }
}

/* ===============================
   FILTER LOGIC
================================ */
let RAW = [];

function currentFilterState(){
  const reason = safeGet("reason")?.value || "ALL";
  const tipo = safeGet("tipo")?.value || "ALL";
  const companies = getSelectedMulti(safeGet("company"));
  const usinas = getSelectedMulti(safeGet("idons"));

  const mi = safeGet("fromMonth")?.value || "";
  const mf = safeGet("toMonth")?.value || "";

  return {reason, tipo, companies, usinas, mi, mf};
}

function rowPasses(r, f){
  if(f.reason !== "ALL" && reasonNorm(r.cod_razaorestricao) !== f.reason) return false;
  if(f.tipo !== "ALL" && r.tipo_map !== f.tipo) return false;

  if(f.companies.length){
    if(!f.companies.includes(r.empresa)) return false;
  }
  if(f.usinas.length){
    if(!f.usinas.includes(r.nom_usina)) return false;
  }
  if(f.mi && r.mes < f.mi) return false;
  if(f.mf && r.mes > f.mf) return false;

  return true;
}
function aggregateMonthlyByCompanyWithReasons(rows){
  const by = new Map();
  const byMonthLast = new Map();

  for(const r of rows){
    const emp = r.empresa || "Não mapeada";
    const mes = r.mes;
    if(!mes) continue;

    const key = emp + "||" + mes;

    if(!by.has(key)){
      by.set(key, {
        empresa: emp,
        mes,
        last_instante: r.last_instante || "",
        corte: 0,
        ref: 0,
        ENE: 0,
        CNF: 0,
        REL: 0,
        SEM: 0
      });
    }
    const a = by.get(key);

    if(r.last_instante && (!a.last_instante || r.last_instante > a.last_instante)){
      a.last_instante = r.last_instante;
    }

    if(r.last_instante && (!byMonthLast.get(mes) || r.last_instante > byMonthLast.get(mes))){
      byMonthLast.set(mes, r.last_instante);
    }

    a.corte += r.curtailment_mwh;
    a.ref   += r.generation_mwh;

    const rr = reasonNorm(r.cod_razaorestricao);
    a[rr] += r.curtailment_mwh;
  }

  const companies = uniq(Array.from(new Set(Array.from(by.values()).map(x=>x.empresa))));
  const monthKeys = uniq(Array.from(new Set(Array.from(by.values()).map(x=>x.mes))));

  const months = monthKeys.map(m => monthLabelFromLastInstante(m, byMonthLast.get(m) || ""));

  const series = {};
  for(const emp of companies){
    series[emp] = monthKeys.map(m=>{
      const a = by.get(emp + "||" + m);
      const corte = a ? a.corte : 0;
      const ref   = a ? a.ref : 0;
      const pct   = ref > 0 ? corte/ref : 0;
      const last  = a ? a.last_instante : (byMonthLast.get(m) || "");
      const xLabel = monthLabelFromLastInstante(m, last);
      return {
        mesKey: m,
        xLabel,
        corte,
        ref,
        pct,
        ENE: a ? a.ENE : 0,
        CNF: a ? a.CNF : 0,
        REL: a ? a.REL : 0,
        SEM: a ? a.SEM : 0
      };
    });
  }

  return { months, monthKeys, companies, series };
}

function aggregateMonthlyByCompany(rows){
  const byCompanyMonth = new Map();
  const byMonthTotal = new Map();

  for(const r of rows){
    const emp = r.empresa || "Não mapeada";
    const mes = r.mes;
    if(!mes) continue;

    const key = emp + "||" + mes;

    if(!byCompanyMonth.has(key)){
      byCompanyMonth.set(key, {
        empresa: emp,
        mes,
        last_instante: r.last_instante || "",
        corte: 0,
        ref: 0
      });
    }
    const a = byCompanyMonth.get(key);

    if(r.last_instante && (!a.last_instante || r.last_instante > a.last_instante)){
      a.last_instante = r.last_instante;
    }
    a.corte += r.curtailment_mwh;
    a.ref   += r.generation_mwh;

    if(!byMonthTotal.has(mes)){
      byMonthTotal.set(mes, { mes, last_instante: r.last_instante || "", corte:0, ref:0 });
    }
    const t = byMonthTotal.get(mes);
    if(r.last_instante && (!t.last_instante || r.last_instante > t.last_instante)){
      t.last_instante = r.last_instante;
    }
    t.corte += r.curtailment_mwh;
    t.ref   += r.generation_mwh;
  }

  const monthKeys = Array.from(byMonthTotal.keys()).sort();
  const months = monthKeys.map(m => monthLabelFromLastInstante(m, byMonthTotal.get(m).last_instante));

  const companies = uniq(Array.from(new Set(Array.from(byCompanyMonth.values()).map(x=>x.empresa))));

  const series = {};
  for(const c of companies){
    series[c] = monthKeys.map(m=>{
      const a = byCompanyMonth.get(c + "||" + m);
      const corte = a ? a.corte : 0;
      const ref   = a ? a.ref : 0;
      const pct   = ref>0 ? corte/ref : 0;
      const last  = a ? a.last_instante : (byMonthTotal.get(m)?.last_instante || "");
      const xLabel = monthLabelFromLastInstante(m, last);
      return { mesKey:m, xLabel, corte, ref, pct };
    });
  }

  const totalByMonth = monthKeys.map(m=>{
    const t = byMonthTotal.get(m);
    const pct = t.ref>0 ? t.corte/t.ref : 0;
    return {
      mesKey:m,
      xLabel: monthLabelFromLastInstante(m, t.last_instante),
      corte:t.corte,
      ref:t.ref,
      pct
    };
  });

  return { months, monthKeys, companies, series, totalByMonth };
}

async function applyFilters(){
  const f = currentFilterState();
  const filtered = RAW.filter(r => rowPasses(r, f));

  setStatus(`OK · ${filtered.length.toLocaleString("pt-BR")} linhas filtradas`);


  // KPIs (total no período)
  const totalCorte = filtered.reduce((s,r)=>s+r.curtailment_mwh,0);
  const totalRef   = filtered.reduce((s,r)=>s+r.generation_mwh,0);
  const pct = totalRef>0 ? totalCorte/totalRef : 0;

  const impactEl = safeGet("kpiImpact");
  const noteEl = safeGet("kpiImpactNote");

  if (impactEl) {
    try {
      const cortePorMes = new Map();
      for (const r of filtered) {
        if (!r.mes) continue;
        cortePorMes.set(r.mes, (cortePorMes.get(r.mes) || 0) + (r.curtailment_mwh || 0));
      }

      const meses = Array.from(cortePorMes.keys()).sort();

      if (!meses.length) {
        impactEl.textContent = "—";
        if (noteEl) noteEl.textContent = "Sem dados no filtro.";
      } else {
        let impactoR$ = 0;
        const mesesSemPLD = [];

        for (const ym of meses) {
          const corteMes = cortePorMes.get(ym) || 0;
          const pldMes = await buscarPLDMonthlyAvg(ym);

          if (pldMes === null || pldMes === undefined) {
            mesesSemPLD.push(ym);
            continue;
          }

          impactoR$ += corteMes * Number(pldMes);
        }

        impactEl.textContent = fmtBRLmm(impactoR$);

        if (noteEl) {
          if (mesesSemPLD.length) {
            noteEl.textContent =
              `PLD faltando em: ${mesesSemPLD.slice(0, 8).join(", ")}${mesesSemPLD.length > 8 ? "..." : ""}`;
          } else {
            noteEl.textContent = "Σ(corte mensal × PLD médio mensal)";
          }
        }
      }
    } catch (e) {
      impactEl.textContent = "—";
      if (noteEl) noteEl.textContent = "Erro ao buscar PLD mensal.";
      console.error(e);
    }
  }




  safeGet("kpiPct").textContent = fmtPct(pct);
  safeGet("kpiCut").textContent = fmtMWh(totalCorte);
  safeGet("kpiRef").textContent = fmtMWh(totalRef);

  const companiesSelected = f.companies || [];

 if(companiesSelected.length >= 2){
  const comp = aggregateMonthlyByCompanyWithReasons(filtered);
  drawCharts(comp, true);
} else {
  const monthly = aggregateMonthly(filtered);
  drawCharts(monthly, false);
}
}

/* ===============================
   CASCADE: empresa -> usinas
================================ */
function refreshUsinaOptionsByCompany(){
  const companies = getSelectedMulti(safeGet("company"));
  let rows = RAW;

  if(companies.length){
    rows = rows.filter(r => companies.includes(r.empresa));
  }
  const usinas = uniq(rows.map(r=>r.nom_usina));
  fillSelectMulti("idons", usinas);
  applySearchFilter();
}

function applySearchFilter(){
  const q = (safeGet("idSearch")?.value || "").trim().toUpperCase();
  const sel = safeGet("idons");
  if(!sel) return;

  for(const opt of sel.options){
    const show = !q || opt.value.toUpperCase().includes(q);
    opt.style.display = show ? "" : "none";
  }
}

/* ===============================
   CHARTS
   ── Paleta Leto Capital ──
   Fundo/papel : #1A1A18 (card dark)
   Grid/eixos  : #2e2d29 (border)
   Texto       : #F0EDE6 (off-white quente)
   Texto muted : #BDB7A7 (cinza Leto)

   Séries:
     ENE  → #D8EEA9  (verde Leto — principal, maior volume)
     CNF  → #7F9657  (verde médio — sucesso)
     REL  → #956A49  (marrom Leto)
     SEM  → #505050  (neutro 700)

   Linha % corte (modo normal) → #BDB7A7 (cinza Leto)

   Paleta comparativa (empresas):
     usa a paleta categórica do brand book
================================ */
function drawCharts(data, comparative){

  // ── Leto color tokens ──
  const C_BG       = "#1A1A18";   // papel / fundo do gráfico
  const C_GRID     = "#2e2d29";   // gridlines
  const C_TEXT     = "#F0EDE6";   // texto principal
  const C_MUTED    = "#BDB7A7";   // cinza Leto — texto secundário, linha %
  const C_BORDER   = "#3a3935";   // bordas leve

  // cores das razões (paleta categórica Leto)
  const C_ENE      = "#D8EEA9";   // verde Leto claro
  const C_CNF      = "#7F9657";   // verde médio
  const C_REL      = "#956A49";   // marrom Leto
  const C_SEM      = "#505050";   // neutro 700

  // paleta comparativa (12 categorias do brand book)
  const PALETTE_COMP = [
    "#000000",  // 01 preto
    "#7B776C",  // 02 neutro quente
    "#BDB7A7",  // 03 cinza Leto
    "#7F9657",  // 04 verde escuro
    "#A7C878",  // 05 verde médio
    "#D8EEA9",  // 06 verde Leto
    "#956A49",  // 07 marrom Leto
    "#C08B63",  // 08 marrom claro
    "#65798F",  // 09 azul acinzentado
    "#94A6BA",  // 10 azul claro
    "#8C89A7",  // 11 lilás
    "#CFCBDA"   // 12 lilás claro
  ];

  const baseLayout = {
    margin: { t: 32, r: 60, l: 60, b: 60 },
    paper_bgcolor: C_BG,
    plot_bgcolor:  C_BG,
    font: { color: C_TEXT, family: "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial" },
    xaxis: {
      title: "Fechamento do mês",
      type: "category",
      showgrid: true,
      gridcolor: C_GRID,
      zeroline: false,
      linecolor: C_BORDER,
      tickfont:  { color: C_MUTED },
      titlefont: { color: C_MUTED }
    },
    yaxis: {
      title: "MWh",
      showgrid: true,
      gridcolor: C_GRID,
      zeroline: false,
      linecolor: C_BORDER,
      tickfont:  { color: C_MUTED },
      titlefont: { color: C_MUTED }
    },
    hovermode: "x unified",
    hoverlabel: {
      bgcolor: "#111110",
      bordercolor: C_BORDER,
      font: { color: C_TEXT }
    },
    legend: {
      bgcolor: "rgba(26,26,24,0.85)",
      bordercolor: C_BORDER,
      borderwidth: 1,
      font: { color: C_TEXT }
    }
  };

  // ---------------------------
  // MODO NORMAL (0-1 empresa)
  // ---------------------------
  if(!comparative){
    const x = data.map(d => monthLabelFromLastInstante(d.mes, d.last_instante));

    Plotly.newPlot("chartMonthly", [
      {
        x,
        y: data.map(d => d.corte),
        type: "bar",
        name: "Corte (MWh)",
        marker: { color: C_ENE }
      },
      {
        x,
        y: data.map(d => d.pct * 100),
        yaxis: "y2",
        type: "scatter",
        mode: "lines+markers",
        name: "% corte",
        line:   { color: C_MUTED, width: 3 },
        marker: { color: C_MUTED, size: 7 }
      }
    ], {
      ...baseLayout,
      yaxis2: {
        title: "% corte",
        overlaying: "y",
        side: "right",
        showgrid: false,
        zeroline: false,
        linecolor: C_BORDER,
        tickfont:  { color: C_MUTED },
        titlefont: { color: C_MUTED }
      }
    }, { displayModeBar: false });

    Plotly.newPlot("chartReason", [
      {
        x,
        y: data.map(d => (d.ref > 0 ? (d.ENE / d.ref) * 100 : 0)),
        type: "bar",
        name: "ENE",
        marker: { color: C_ENE },
        hovertemplate: "ENE<br>%{x}<br>%{y:.2f} pp<extra></extra>"
      },
      {
        x,
        y: data.map(d => (d.ref > 0 ? (d.CNF / d.ref) * 100 : 0)),
        type: "bar",
        name: "CNF",
        marker: { color: C_CNF },
        hovertemplate: "CNF<br>%{x}<br>%{y:.2f} pp<extra></extra>"
      },
      {
        x,
        y: data.map(d => (d.ref > 0 ? (d.REL / d.ref) * 100 : 0)),
        type: "bar",
        name: "REL",
        marker: { color: C_REL },
        hovertemplate: "REL<br>%{x}<br>%{y:.2f} pp<extra></extra>"
      },
      {
        x,
        y: data.map(d => (d.ref > 0 ? (d.SEM / d.ref) * 100 : 0)),
        type: "bar",
        name: "SEM",
        marker: { color: C_SEM },
        hovertemplate: "SEM<br>%{x}<br>%{y:.2f} pp<extra></extra>"
      }
    ], {
      ...baseLayout,
      barmode: "stack",
      yaxis: { ...baseLayout.yaxis, title: "% corte (pp)", ticksuffix: "%" }
    }, { displayModeBar: false });

    return;
  }

  // ---------------------------
  // MODO COMPARATIVO (2+ empresas)
  // ---------------------------
  const x = data.months;

  const companyColor = {};
  data.companies.forEach((emp, i) => companyColor[emp] = PALETTE_COMP[i % PALETTE_COMP.length]);

  // Gráfico 1: barras corte + linhas % por empresa
  const tracesTop = [];

  data.companies.forEach(emp => {
    const s = data.series[emp];
    tracesTop.push({
      x,
      y: s.map(p => p.corte),
      type: "bar",
      name: emp,
      marker: { color: companyColor[emp] }
    });
  });

  data.companies.forEach(emp => {
    const s = data.series[emp];
    tracesTop.push({
      x,
      y: s.map(p => p.pct * 100),
      yaxis: "y2",
      type: "scatter",
      mode: "lines+markers",
      name: `% corte · ${emp}`,
      line:   { color: companyColor[emp], width: 2 },
      marker: { color: companyColor[emp], size: 6 }
    });
  });

  Plotly.newPlot("chartMonthly", tracesTop, {
    ...baseLayout,
    barmode: "group",
    yaxis2: {
      title: "% corte",
      overlaying: "y",
      side: "right",
      showgrid: false,
      zeroline: false,
      linecolor: C_BORDER,
      tickfont:  { color: C_MUTED },
      titlefont: { color: C_MUTED }
    }
  }, { displayModeBar: false });

  // Gráfico 2 (comparativo): build-up da % (pp) por razão
  const reasons = ["ENE", "CNF", "REL", "SEM"];
  const reasonColor = { ENE: C_ENE, CNF: C_CNF, REL: C_REL, SEM: C_SEM };

  const tracesBottom = [];

  const monthLabels = data.months;
  const monthKeys   = data.monthKeys;

  const empCode = {};
  data.companies.forEach((emp, idx) => { empCode[emp] = String(idx + 1); });

  reasons.forEach(rr => {
    const xMonth = [];
    const xEmpCode = [];
    const y = [];

    monthKeys.forEach((m, i) => {
      const mlab = monthLabels[i] || m;

      data.companies.forEach(emp => {
        const p = data.series[emp][i];
        const pp = (p && p.ref > 0) ? (((p[rr] || 0) / p.ref) * 100) : 0;

        xMonth.push(mlab);
        xEmpCode.push(empCode[emp]);
        y.push(pp);
      });
    });

    tracesBottom.push({
      type: "bar",
      name: rr,
      x: [xMonth, xEmpCode],
      y,
      marker: { color: reasonColor[rr] },
      hovertemplate: `${rr}<br>%{x}<br>%{y:.2f} pp<extra></extra>`
    });
  });

  Plotly.newPlot("chartReason", tracesBottom, {
    ...baseLayout,
    barmode: "relative",
    yaxis: { ...baseLayout.yaxis, title: "% corte (pp)", ticksuffix: "%" },
    xaxis: {
      ...baseLayout.xaxis,
      title: "Fechamento do mês",
      type: "multicategory",
      tickangle: -35
    },
    title: {
      text: data.companies
        .map(emp => `${empCode[emp]} - ${emp}`)
        .join("   ·   "),
      x: 0,
      xanchor: "left",
      font: { size: 13, color: C_MUTED }
    }
  }, { displayModeBar: false });
}

/* ===============================
   INIT
================================ */
async function init(){
  setStatus("Carregando…");

  RAW = await loadAll();

  // --- Datas de atualização (PLD / ONS) ---
  try {
    const maxDiaPLD = await buscarPLDMaxDia();
    setPLDUpdatedText(maxDiaPLD);
  } catch(e){
    console.warn("Não consegui ler PLD meta:", e);
    setPLDUpdatedText("—");
  }

  const lastInst = RAW
    .map(r => (r.last_instante || "").trim())
    .filter(Boolean)
    .sort()
    .pop();


// DEBUG: conferir soma Jan/25 no que o dashboard carregou
const solJan = RAW
  .filter(r => r.tipo_map === "SOL" && r.mes === "2025-01")
  .reduce((s,r)=>s + (r.curtailment_mwh||0), 0);

const eolJan = RAW
  .filter(r => r.tipo_map === "EOL" && r.mes === "2025-01")
  .reduce((s,r)=>s + (r.curtailment_mwh||0), 0);

console.log("DEBUG solJan", solJan);
console.log("DEBUG eolJan", eolJan);

  const lastONSDate = isoDateFromLastInstante(lastInst);

  setONSUpdatedText(lastONSDate || "—");


  // opções básicas
  const empresas = uniq(RAW.map(r=>r.empresa));
  const tipos = uniq(RAW.map(r=>r.tipo_map));
  const meses = uniq(RAW.map(r=>r.mes));

  fillSelectSingle("tipo", tipos, true);
  safeGet("tipo").value = "ALL";

  fillSelectMulti("company", empresas);
  fillSelectMulti("idons", uniq(RAW.map(r=>r.nom_usina)));

  fillSelectSingle("fromMonth", meses, false);
  fillSelectSingle("toMonth", meses, false);
  if(meses.length){
    safeGet("fromMonth").value = meses[0];
    safeGet("toMonth").value = meses[meses.length-1];
  }

  // eventos
  safeGet("apply").onclick = applyFilters;
  safeGet("clear").onclick = () => location.reload();
  safeGet("btnReload").onclick = () => location.reload();

  safeGet("company").addEventListener("change", refreshUsinaOptionsByCompany);
  safeGet("idSearch").addEventListener("input", applySearchFilter);
  


  setStatus(`OK · ${RAW.length.toLocaleString("pt-BR")} linhas carregadas`);
  applyFilters();
}

window.addEventListener("DOMContentLoaded", init);

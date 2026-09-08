"use strict";
const research = JSON.parse(document.getElementById("research-data").textContent);
const stock = document.getElementById("stock");
for (const row of research.stocks) { const o=document.createElement("option");o.value=row.ticker;o.textContent=row.ticker;stock.append(o); }
stock.value="VRT";
function scenario() {
  const row=research.stocks.find(r=>r.ticker===stock.value), g=Number(document.getElementById("growth").value)/100,
    pe=Number(document.getElementById("multiple").value), years=Number(document.getElementById("months").value)/12;
  const node=document.getElementById("scenario-result");
  if(!Number.isFinite(g)||g<=-1||!Number.isFinite(pe)||pe<=0){node.textContent="Enter an EPS growth rate above −100% and a positive exit multiple.";return;}
  const terminal=row.starting_eps_usd*Math.pow(1+g,years)*pe, ret=terminal/row.raw_close_usd-1, entry=terminal/Math.pow(1.10,years);
  node.replaceChildren();
  const line=document.createElement("b");line.textContent=`${row.ticker}: ${(ret*100).toFixed(1)}% designed price return`;node.append(line);
  const text=document.createElement("div");text.textContent=`Starting EPS $${row.starting_eps_usd.toFixed(2)} · Observed close $${row.raw_close_usd.toFixed(2)} · Terminal price $${terminal.toFixed(2)} · Entry for 10% annual price hurdle $${entry.toFixed(2)}`;text.className="small";node.append(text);
}
for(const id of ["stock","growth","multiple","months"])document.getElementById(id).addEventListener("input",scenario);
scenario();
const filter=document.getElementById("model-filter"), body=document.querySelector("#model-table tbody");
let displayed=[];
function renderModels(){
 const q=filter.value.toLowerCase();displayed=research.models.filter(r=>(r.scenario_label+" "+r.basket_label).toLowerCase().includes(q));
 body.replaceChildren();
 for(const r of displayed){const tr=document.createElement("tr");const vals=[r.scenario_label,r.basket_label,r.n_observations,r.beta_per_10pp===null?"—":(r.beta_per_10pp*10).toFixed(3),r.q_value_conservative_primary===null?"—":r.q_value_conservative_primary.toFixed(3),r.robustness_screen_status.replaceAll("_"," ")];
 for(const v of vals){const td=document.createElement("td");td.textContent=v;tr.append(td);}body.append(tr);}
 document.getElementById("model-count").textContent=`${displayed.length} of ${research.models.length} primary combinations. Market-conditioned baseline; results are observational. Full CSV exports include intervals, alternate gates and failure reasons.`;
}
filter.addEventListener("input",renderModels);renderModels();
document.getElementById("download-models").addEventListener("click",()=>{
 const keys=["scenario_label","basket_label","n_observations","beta_per_10pp","q_value_conservative_primary","robustness_screen_status"];
 const quote=v=>'"'+String(v??"").replaceAll('"','""')+'"';
 const text=[keys.map(quote).join(","),...displayed.map(r=>keys.map(k=>quote(r[k])).join(","))].join("\r\n");
 const url=URL.createObjectURL(new Blob([text],{type:"text/csv;charset=utf-8"}));const a=document.createElement("a");a.href=url;a.download="sensitivity_filtered_per_10pp.csv";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
// Wrap Markdown tables for narrow screens; generated data tables are already wrapped.
for(const t of document.querySelectorAll("main>table")){const wrap=document.createElement("div");wrap.className="table-wrap";t.before(wrap);wrap.append(t);}

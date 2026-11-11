'use strict';
const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const key = params.get('k') || '';
const url = endpoint => endpoint + '?k=' + encodeURIComponent(key);
let lastState = null;
let toastTimer;
let catalogPage = 1;
let catalogTotal = 0;
let catalogDebounce = null;
const CATALOG_PAGE_SIZE = 50;
const modeNames = {idle:'Sem ligação',finished:'Reprodução concluída',demo:'Demonstração',replay:'Reprodução CSV',bridge:'Ponte K-Line experimental',diagnostic:'Diagnóstico K-Line'};
const show = (id,value) => { $(id).textContent = value; };
const fmt = (v, decimals = 0) => Number.isFinite(v) ? v.toLocaleString('pt-PT',{minimumFractionDigits:decimals,maximumFractionDigits:decimals}) : '—';
function paintValue(id,value,decimals=0){const el=$(id);el.textContent=fmt(value,decimals);el.classList.toggle('is-empty',!Number.isFinite(value));}
function notice(t) { const el=$('toast'); el.textContent=t; el.classList.add('show'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>el.classList.remove('show'),3600); }
async function api(endpoint, options) { const res=await fetch(url('/api/'+endpoint),{cache:'no-store',...options}); const data=await res.json(); if(!res.ok) throw new Error(data.error || 'Erro de comunicação'); return data; }
const post = (endpoint,data) => api(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function drawChart(samples) {
 const g=$('chart-grid'), real=$('chart-real'), target=$('chart-target');
 g.replaceChildren();
 const vals=samples.flatMap(s=>[s.boost_actual_mbar,s.boost_requested_mbar]).filter(Number.isFinite);
 $('chart-empty').classList.toggle('hidden',vals.length>0);
 if (!vals.length){real.setAttribute('d','');target.setAttribute('d','');show('chart-range','—');return;}
 const low=Math.min(0,Math.floor((Math.min(...vals)-200)/500)*500);
 const high=Math.max(3000,Math.ceil((Math.max(...vals)+200)/500)*500);
 const L=42,R=829,T=9,B=195;
 for (let i=0;i<=4;i++){
  const y=T+i*(B-T)/4;
  const l=document.createElementNS('http://www.w3.org/2000/svg','line'); l.setAttribute('x1',L);l.setAttribute('x2',R);l.setAttribute('y1',y);l.setAttribute('y2',y);g.appendChild(l);
  const tx=document.createElementNS('http://www.w3.org/2000/svg','text');tx.setAttribute('x',0);tx.setAttribute('y',y+4);tx.textContent=((high-i*(high-low)/4)/1000).toFixed(1);g.appendChild(tx);
 }
 const y=v => B-(v-low)/(high-low)*(B-T);
 const mkPath = key => {
  let path='',open=false;
  samples.forEach((s,i)=>{
   const v=s[key];if(!Number.isFinite(v)){open=false;return;}
   const x=L+(samples.length===1 ? 0 : i/(samples.length-1)*(R-L));
   path+=(open?' L ':'M ')+x.toFixed(1)+' '+y(v).toFixed(1);open=true;
  });return path;
 };
 real.setAttribute('d',mkPath('boost_actual_mbar'));
 target.setAttribute('d',mkPath('boost_requested_mbar'));
 show('chart-range',`ESCALA ${(low/1000).toFixed(1)}–${(high/1000).toFixed(1)} bar abs.`);
}
function render(s){
 lastState=s;
 const v=s.values||{};
 show('mode-title',modeNames[s.mode]||s.mode);show('mode-desc',s.status||'');
 $('demo-banner').classList.toggle('hidden',s.mode!=='demo');
 const diagnostic=s.diagnostic||{};
 const monitor=diagnostic.monitor||{};
 const hasData=!!s.last_update&&Object.keys(v).length>0;
 $('telemetry-empty').classList.toggle('hidden',hasData);
 if(!hasData){
  const empty=diagnostic.error?'Sem leituras. Revê a porta série, a ligação e o baud selecionado.':diagnostic.ecu?'ECU identificada. Escolhe um grupo provisório para fazer uma leitura única.':s.mode==='diagnostic'?'A identificar ECU. Ainda não foram lidos grupos.':s.mode==='replay'?'O CSV não tem amostras disponíveis para este painel.':'Sem amostras. Escolhe uma demonstração, reproduz um CSV ou identifica a ECU.';
  show('telemetry-empty',empty);
 }
 const identified=!!diagnostic.ecu;
 const sessionReady=identified&&diagnostic.session_active!==false&&['identified','live'].includes(diagnostic.status);
 const monitoring=!!monitor.active;
 $('diagnostic-groups').disabled=!sessionReady||monitoring;
 $('diagnostic-read-group').disabled=!sessionReady||monitoring;
 $('monitor-groups').disabled=!sessionReady||monitoring;
 $('monitor-interval').disabled=!sessionReady||monitoring;
 $('monitor-start').disabled=!sessionReady||monitoring;
 $('monitor-stop').disabled=!monitoring;
 const monitorLabels={stopped:'Inativa',running:'Ativa',suspended:'Suspensa',error:'Erro'};
 $('monitor-status').textContent=monitorLabels[monitor.status]||'Inativa';
 $('monitor-status').dataset.state=monitor.status||'stopped';
 const monitorGroups=Array.isArray(monitor.groups)?monitor.groups:[];
 const groupFreshness=diagnostic.group_freshness||{};
 const sampleRows=monitorGroups.map(group=>{
  const key=String(group), sample=(diagnostic.group_samples||{})[key];
  const freshness=groupFreshness[key]||'indisponível';
  const age=sample&&Number.isFinite(sample.age_s)?` · ${fmt(sample.age_s,1)} s atrás`:'';
  const latency=sample&&Number.isFinite(sample.latency_s)?` · ${fmt(sample.latency_s*1000,0)} ms`:'';
  return `${String(group).padStart(3,'0')}: ${freshness}${age}${latency}`;
 });
 const current=monitor.current_group?` · a ler ${String(monitor.current_group).padStart(3,'0')}`:'';
 const period=Number.isFinite(monitor.min_period_s)?` · mínimo ${fmt(monitor.min_period_s,1)} s após resposta`:'';
 const monitorError=monitor.error&&monitor.error.message?` · ${monitor.error.message}`:'';
 $('monitor-freshness').textContent=monitor.status==='suspended'
  ? `Monitorização suspensa${monitorError}. Volta a identificar a ECU se a sessão terminou.`
  : monitoring
   ? `Sequência ${monitorGroups.map(group=>String(group).padStart(3,'0')).join(' → ')}${current}${period}. ${sampleRows.join(' · ')||'A aguardar primeira resposta.'}`
   : 'As leituras manuais são únicas. A monitorização só começa após ativação explícita.';
 const active=!!s.last_update && ['demo','replay','bridge'].includes(s.mode) || (s.mode==='diagnostic' && diagnostic.status==='live' && diagnostic.freshness==='fresh');
 $('connection-dot').className='connection-dot'+(active?(s.mode==='demo'?' sim':' live'):'');
 show('connection-status',s.mode==='demo'?'Simulação':s.mode==='replay'?'Reprodução CSV':s.mode==='bridge'?(active?'Ponte ligada':'Ponte em teste'):s.mode==='diagnostic'?(diagnostic.status==='live'&&diagnostic.freshness==='fresh'?'Diagnóstico ligado':diagnostic.status==='error'?'Erro no diagnóstico':'A aguardar hardware'):'Sem ligação');
 document.querySelectorAll('[data-mode]').forEach(b=>{b.classList.toggle('selected',b.dataset.mode===s.mode)});
 const a=Number.isFinite(v.boost_actual_mbar)?v.boost_actual_mbar/1000:null;
 const p=Number.isFinite(v.boost_requested_mbar)?v.boost_requested_mbar/1000:null;
 paintValue('boost-actual',a,2);paintValue('boost-requested',p,2);
 paintValue('boost-delta',Number.isFinite(v.boost_actual_mbar)&&Number.isFinite(v.boost_requested_mbar)?v.boost_actual_mbar-v.boost_requested_mbar:null);
 $('range-fill').style.width=(Number.isFinite(a)?Math.max(0,Math.min(100,a/3*100)):0)+'%';
 $('target-marker').style.left=(Number.isFinite(p)?Math.max(0,Math.min(100,p/3*100)):0)+'%';
 paintValue('rpm',v.rpm);paintValue('maf',v.maf_actual_mg);paintValue('maf-requested',v.maf_requested_mg);
 paintValue('iq',v.iq_mg,1);paintValue('coolant',v.coolant_c);paintValue('n75',v.n75_percent,1);
 const n=Number.isFinite(v.rpm)?Math.min(24,Math.round(v.rpm/6000*24)):0;
 [...$('rpm-bars').children].forEach((node,i)=>node.classList.toggle('on',i<n));
 show('session-source',modeNames[s.mode]||s.mode);
 show('session-file',s.file||'Nenhum');
 show('session-samples',s.mode==='replay'?`${s.position} / ${s.samples}`:fmt((s.history||[]).length));
 show('session-last',s.last_update?new Date(s.last_update*1000).toLocaleTimeString('pt-PT'):'—');
 show('ecu-part',diagnostic.ecu&&diagnostic.ecu.part_number?diagnostic.ecu.part_number:'Não identificada');
 show('ecu-freshness',diagnostic.freshness==='fresh'?'Dados recentes':diagnostic.freshness==='stale'?'Dados desatualizados':'Dados indisponíveis');
 const diagnosticStatus={unavailable:'Não disponível',stopped:'Parado',identifying:'A identificar',retrying:'A repetir ligação',live:'Ligado',error:'Erro'};
 show('diagnostic-status',diagnosticStatus[diagnostic.status]||diagnostic.status||'Não iniciado');
 show('diagnostic-raw',formatDiagnosticRaw(diagnostic.raw_values));
 const groupErrors=Object.entries(diagnostic.group_errors||{}).map(([group,error])=>`${group}: ${error.code}`).join(' · ');
 show('diagnostic-error', diagnostic.error ? `${diagnostic.error.code}: ${diagnostic.error.message}` : groupErrors || `Sem erros${diagnostic.retry_count ? ` · tentativa ${diagnostic.retry_count}` : ''}`);
 $('record-btn').textContent=s.recording?'Parar gravação':'Iniciar gravação';
 $('record-btn').classList.toggle('recording',s.recording);
 drawChart(s.history||[]);
}
async function refresh(){try{const s=await api('state');render(s)}catch(e){show('connection-status','SERVIDOR INDISPONÍVEL');$('connection-dot').className='connection-dot';}}
async function loadDiagnosticPorts(){
 const select=$('diagnostic-port');
 try{
  const data=await api('diagnostic/ports');
  select.replaceChildren();
  data.ports.forEach(port=>{
   const option=document.createElement('option');
   option.value=port.device;
   option.textContent=`${port.device} · ${port.description||'porta série'}`;
   select.appendChild(option);
  });
  if(!data.ports.length){
   const option=document.createElement('option');
   option.value='';
   option.textContent='Nenhuma porta encontrada';
   select.appendChild(option);
  }
 }catch(e){
  select.replaceChildren();
  const option=document.createElement('option');
  option.value='';
  option.textContent='Erro ao procurar portas';
  select.appendChild(option);
  throw e;
 }
}
async function loadDiagnosticGroups(){
  const select=$('diagnostic-groups');
  try{
   const data=await api('diagnostic/groups');
   const selected=select.value;
   select.replaceChildren(new Option('Escolher após identificação',''));
   data.groups.filter(group=>[1,3,11].includes(group.id)).forEach(group=>{
    const option=document.createElement('option');
    option.value=group.id;
    option.textContent=`${String(group.id).padStart(3,'0')} · ${group.label}${group.provisional?' · provisório':''}`;
    option.selected=selected===String(group.id);
    select.appendChild(option);
   });
  }catch(e){ notice(`Grupos indisponíveis: ${e.message}`); }
}
function catalogUrl(params){
 const query=new URLSearchParams({k:key,...params});
 return `/api/diagnostic/catalog?${query.toString()}`;
}
function appendCell(row,text,className){
 const cell=document.createElement('td');
 if(className)cell.className=className;
 cell.textContent=text||'—';
 row.appendChild(cell);
 return cell;
}
function renderCatalogRows(items){
 const body=$('catalog-results');body.replaceChildren();
 for(const item of items){
  const row=document.createElement('tr');
  appendCell(row,`${String(item.group_id).padStart(3,'0')} · ${String(item.field_id).padStart(3,'0')}`);
  const nameCell=document.createElement('td');
  const name=document.createElement('strong');name.className='catalog-name';name.textContent=item.name||'Campo sem descrição';nameCell.appendChild(name);
  if(item.description&&item.description!==item.name){const description=document.createElement('span');description.className='catalog-description';description.textContent=item.description;nameCell.appendChild(description)}
  row.appendChild(nameCell);
  appendCell(row,[item.unit,item.range_text].filter(Boolean).join(' · ')||'Sem unidade ou intervalo');
  const statusCell=document.createElement('td');
  const badge=document.createElement('span');badge.className='catalog-badge';badge.textContent='METADATA_ONLY';statusCell.appendChild(badge);
  const presentation=FISDiagnosticCatalog.candidatePresentation(item);
  for(const text of [presentation.compatibility,presentation.decoder]){const detail=document.createElement('span');detail.className='catalog-state-detail';detail.textContent=text;statusCell.appendChild(detail)}
  row.appendChild(statusCell);
  const sourceCell=document.createElement('td');
  const source=document.createElement('span');source.className='catalog-source';source.textContent=presentation.source;sourceCell.appendChild(source);
  if(item.source_sha256){const hash=document.createElement('span');hash.className='catalog-source';hash.textContent=`SHA-256 ${item.source_sha256.slice(0,12)}…`;sourceCell.appendChild(hash)}
  row.appendChild(sourceCell);body.appendChild(row);
 }
}
function updateCatalogGroupOptions(groups){
 const select=$('catalog-group'),selected=select.value;
 select.replaceChildren(new Option('Todos os grupos',''));
 for(const group of groups){
  const option=document.createElement('option');option.value=group.id;
  option.textContent=`${String(group.id).padStart(3,'0')} · ${group.label} · ${group.status}`;
  option.selected=selected===String(group.id);select.appendChild(option);
 }
}
async function loadCatalog({refresh=false,resetPage=true}={}){
 if(resetPage)catalogPage=1;
 const params={q:$('catalog-search').value.trim(),page:String(catalogPage),page_size:String(CATALOG_PAGE_SIZE)};
 if($('catalog-group').value)params.group=$('catalog-group').value;
 if($('catalog-status').value)params.status=$('catalog-status').value;
 if(refresh)params.refresh='1';
 const message=$('catalog-message');message.classList.remove('error');message.textContent='A ler metadados locais…';
 try{
  const response=await fetch(catalogUrl(params),{cache:'no-store'});const data=await response.json();
  if(!response.ok)throw new Error(data.error||'Não foi possível ler o catálogo');
  catalogTotal=data.total;updateCatalogGroupOptions(data.groups||[]);renderCatalogRows(data.items||[]);
  $('catalog-summary').textContent=FISDiagnosticCatalog.catalogSummary(data.summary);
  const pages=Math.max(1,Math.ceil(catalogTotal/CATALOG_PAGE_SIZE));
  $('catalog-page').textContent=`Página ${catalogPage} / ${pages} · ${fmt(catalogTotal)} resultados`;
  $('catalog-prev').disabled=catalogPage<=1;$('catalog-next').disabled=catalogPage>=pages;
  if(data.summary&&data.summary.files_scanned===0){message.textContent='A pasta Labels/ não está disponível. Adiciona os ficheiros localmente e atualiza.'}
  else if(!catalogTotal){message.textContent=$('catalog-status').value==='UNKNOWN'?'Grupo sem definição local; compatibilidade desconhecida.':'Sem parâmetros que correspondam aos filtros.'}
  else message.textContent=`A mostrar ${data.items.length} de ${fmt(catalogTotal)} candidatos. Nenhum é apresentado como valor live.`;
  if(data.summary&&data.summary.warning_count)message.textContent+=` ${data.summary.warning_count} avisos de leitura.`;
 }catch(error){message.textContent=error.message;message.classList.add('error');}
}
for(let i=0;i<24;i++){const b=document.createElement('i');if(i>=19)b.classList.add('warning');$('rpm-bars').appendChild(b)}
document.querySelectorAll('[data-mode]').forEach(b=>b.addEventListener('click',async()=>{try{
 let state;
 if(b.dataset.mode==='diagnostic'){
  await loadDiagnosticPorts();
  const device=$('diagnostic-port').value;
  if(!device)throw new Error('Seleciona uma porta K-Line antes de ligar');
  state=await post('diagnostic/connect',{device,baudrate:Number($('diagnostic-baud').value)});
 }else state=await post('mode',{mode:b.dataset.mode});
 render(state);
}catch(e){notice(e.message)}}));
$('refresh-ports').addEventListener('click',async()=>{try{await loadDiagnosticPorts();notice('Portas série atualizadas')}catch(e){notice(e.message)}});
$('diagnostic-read-group').addEventListener('click',async()=>{try{
 const groupValue=$('diagnostic-groups').value;
 if(!groupValue)throw new Error('Escolhe um grupo provisório');
 const selected=Number(groupValue);
 const state=await post('diagnostic/read-group',{group:selected});
 render(state);
}catch(e){notice(e.message)}});
$('monitor-start').addEventListener('click',async()=>{try{
 const selected=[...$('monitor-groups').querySelectorAll('input:checked')].map(input=>Number(input.value));
 const groups=FISDiagnosticCatalog.normalizeMonitorGroups(selected);
 const min_period_s=Number($('monitor-interval').value);
 const state=await post('diagnostic/monitor/start',{groups,min_period_s});
 render(state);notice('Monitorização contínua iniciada');
}catch(e){notice(e.message)}});
$('monitor-stop').addEventListener('click',async()=>{try{
 const state=await post('diagnostic/monitor/stop',{});render(state);notice('Monitorização parada');
}catch(e){notice(e.message)}});
$('catalog-search').addEventListener('input',()=>{clearTimeout(catalogDebounce);catalogDebounce=setTimeout(()=>loadCatalog(),220)});
$('catalog-group').addEventListener('change',()=>loadCatalog());
$('catalog-status').addEventListener('change',()=>loadCatalog());
$('catalog-refresh').addEventListener('click',async()=>{
 const button=$('catalog-refresh');button.disabled=true;button.textContent='A importar…';
 try{
  const result=await post('diagnostic/catalog/import',{});
  await loadCatalog({resetPage:false});
  const report=result.report||{};
  notice(`Importação: ${fmt(report.added_count||0)} novos · ${fmt(report.changed_count||0)} alterados · ${fmt(report.removed_count||0)} removidos · ${fmt(report.conflict_count||0)} conflitos`);
 }catch(error){notice(`Falha ao importar labels: ${error.message}`)}
 finally{button.disabled=false;button.textContent='Atualizar labels'}
});
$('catalog-prev').addEventListener('click',()=>{if(catalogPage>1){catalogPage--;loadCatalog({resetPage:false})}});
$('catalog-next').addEventListener('click',()=>{if(catalogPage*CATALOG_PAGE_SIZE<catalogTotal){catalogPage++;loadCatalog({resetPage:false})}});
$('csv-file').addEventListener('change',async e=>{const file=e.target.files[0];if(!file)return;if(file.size>12000000){notice('Ficheiro demasiado grande (máx. 12 MB)');return}try{const raw=await file.arrayBuffer();const res=await fetch(url('/api/upload'),{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Filename':file.name.normalize('NFD').replace(/[^\x20-\x7E]/g,'_')},body:raw});const j=await res.json();if(!res.ok)throw new Error(j.error);notice(`Carregado: ${j.samples} amostras · ${j.fields.length} parâmetros`);await refresh()}catch(err){notice(err.message)}finally{e.target.value=''}});
$('record-btn').addEventListener('click',async()=>{try{const v=await post('record',{active:!(lastState&&lastState.recording)});render(v);notice(v.recording?'Gravação iniciada':'Gravação parada')}catch(e){notice(e.message)}});
$('export-btn').addEventListener('click',async()=>{if(!(lastState&&lastState.recorded)){notice('Começa por gravar algumas amostras');return;}location.href=url('/api/export')});
$('trace-btn').addEventListener('click',()=>{location.href=url('/api/diagnostic/trace')});
loadDiagnosticPorts().catch(()=>{});
loadDiagnosticGroups();
loadCatalog();
refresh();setInterval(refresh,650);

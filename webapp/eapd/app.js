const meta = window.APD_METADATA;
const tableOptions = window.TABLE_OPTIONS || {};
const osyk = { kad: window.OSYK_KAD || [], eid: window.OSYK_EID || [], topic: [] };

function parsePercent(v){
  if(v==null) return 0;
  const s=String(v).trim().replace(',', '.').replace('%','');
  const n=Number(s);
  return Number.isFinite(n)?n:0;
}

function normalizeKpkValues(raw){
  const arr = Array.isArray(raw) ? raw : [];
  return arr.map((r)=>{
    if(Array.isArray(r)){
      const [code, description, emp, tot] = r;
      return { code:String(code??'').trim(), description:String(description??'').trim(), emp:parsePercent(emp), tot:parsePercent(tot) };
    }
    const code = String(r?.code ?? r?.kpk ?? r?.id ?? '').trim();
    const description = String(r?.description ?? r?.desc ?? r?.label ?? '').trim();
    return { code, description, emp:parsePercent(r?.emp), tot:parsePercent(r?.tot) };
  }).filter(x=>x.code);
}

const kpkValues = normalizeKpkValues(window.KPK_VALUES || globalThis.KPK_VALUES || []);

function normalizeEpidotiseisValues(raw){
  const arr = Array.isArray(raw) ? raw : [];
  return arr.map(r => ({
    code: String(r?.code ?? '').trim(),
    per: String(r?.per ?? r?.description ?? '').trim(),
    pemployee: parsePercent(r?.pemployee),
    vemployee: String(r?.vemployee ?? '').trim(),
    pcompany: parsePercent(r?.pcompany),
    vcompany: String(r?.vcompany ?? '').trim(),
  })).filter(x => x.code);
}
const epidotiseisValues = normalizeEpidotiseisValues(window.EPIDOTISEIS_VALUES || globalThis.EPIDOTISEIS_VALUES || []);
const topicValues = (window.TOPIK_VALUES || globalThis.TOPIK_VALUES || []).map(x => ({
  code: String(x?.code ?? '').trim(),
  per: String(x?.per ?? '').trim(),
})).filter(x => x.code);
osyk.topic = topicValues.map(x => ({ code: x.code, description: x.per }));

const recordsEl = document.getElementById('records');
let lookupTarget = null, lookupKind='kad';

// While true (file import in progress) no derived/auto value is written:
// the form must display the file contents exactly as they are.
let importing = false;

function setRecalcIndicator(ok){
  const el=document.getElementById('recalcIndicator');
  if(!el) return;
  el.className='recalc-indicator ' + (ok ? 'recalc-ok' : 'recalc-needed');
  el.textContent = ok ? 'Recalculated ✓' : 'Needs recalculation';
}
function markNeedsRecalc(){ setRecalcIndicator(false); }

const byNo = (line,no)=> (meta[line].columns.find(c=>String(c.no)===String(no))||{}).name;

function normalizeGreekUpperText(v){return String(v||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toUpperCase();}
function normalizeCode3(v){const d=String(v||'').replace(/\D/g,''); return d?String(parseInt(d,10)).padStart(3,'0'):String(v||'').trim();}
function normalizeNumericCode(v){
  const s=String(v??'').trim();
  if(s==='') return '';
  const n=Number(s);
  if(Number.isFinite(n)) return String(parseInt(s,10));
  const d=s.replace(/\D/g,'');
  return d ? String(parseInt(d,10)) : s;
}

function fieldLen(f){const t=f.dataset.typos||''; const raw=String(f.value||''); if(['9','V','F','F3','D'].includes(t)) return raw.replace(/\D/g,'').length; return raw.length;}
function isOverLen(f){const m=parseInt(f.dataset.maxlen||'0',10); return m>0 && fieldLen(f)>m;}

function allowsBlank(field,box){
  const no=String(field.dataset.no||'');
  if(['6','7','54','55','58'].includes(no)) return true;

  if(box?.dataset.line==='StoixeiaAsfalisis'){
    const f36=box.querySelector(':scope > .grid [data-no="36"]');
    const v36=normalizeNumericCode(f36?.value);
    if(v36==='14' && ['32','33','34','56','57'].includes(no)) return true;

    if(['40','41'].includes(no)){
      const f42=box.querySelector(':scope > .grid [data-no="42"]');
      return ['002','004','005','006'].includes(normalizeCode3(f42?.value));
    }
  }

  return false;
}

function setSingletonButtons(){document.getElementById('btnAddGenika').disabled=!!recordsEl.querySelector(':scope>.record[data-line="GenikaStoixeia"]'); document.getElementById('btnAddEof').disabled=!!recordsEl.querySelector(':scope>.record[data-line="TelosArxeiou"]');}

function openLookup(input,kind,title){lookupTarget=input;lookupKind=kind;document.getElementById('lookupTitle').textContent=title;document.getElementById('lookupModal').style.display='flex';const q=document.getElementById('lookupSearch');q.value=String(input.value||'');searchLookup(q.value);q.focus();q.select();}
function closeLookup(){document.getElementById('lookupModal').style.display='none';}
function normalizeSearch(s){return normalizeGreekUpperText(s).toLowerCase();}
function syncGenikaNo4FromNo3(){
  const genika = recordsEl.querySelector(':scope > .record[data-line="GenikaStoixeia"]');
  if(!genika) return;
  const f3 = genika.querySelector(':scope > .grid [data-no="3"]');
  const f4 = genika.querySelector(':scope > .grid [data-no="4"]');
  if(!f3 || !f4) return;

  if(importing) return;

  const code = String(f3.value||'').trim();
  const compact = Number.isNaN(Number(code)) ? code : String(parseInt(code,10));
  const row = topicValues.find(x => {
    const c = String(x.code||'').trim();
    if(c === code) return true;
    if(!Number.isNaN(Number(c)) && !Number.isNaN(Number(code)) && String(parseInt(c,10))===compact) return true;
    return false;
  });
  if(row) f4.value = normalizeGreekUpperText(row.per || '');
}

function searchLookup(q){const host=document.getElementById('lookupResults');host.innerHTML='';const nq=normalizeSearch(q);const rows=osyk[lookupKind].filter(r=>!nq || normalizeSearch(r.code).includes(nq)||normalizeSearch(r.description).includes(nq)).slice(0,120);rows.forEach(r=>{const b=document.createElement('button');b.className='secondary';b.textContent=`${r.code} — ${r.description}`;b.onclick=()=>{lookupTarget.value=r.code;lookupTarget.dispatchEvent(new Event('input',{bubbles:true}));lookupTarget.dispatchEvent(new Event('change',{bubbles:true}));if(lookupKind==='topic') syncGenikaNo4FromNo3();closeLookup();};host.appendChild(b);});}

function addRecord(lineName,preset={},returnOnly=false){
  if(lineName==='GenikaStoixeia' && !returnOnly && recordsEl.querySelector(':scope>.record[data-line="GenikaStoixeia"]')) return alert('Only one GenikaStoixeia');
  if(lineName==='TelosArxeiou' && !returnOnly && recordsEl.querySelector(':scope>.record[data-line="TelosArxeiou"]')) return alert('Only one TelosArxeiou');
  const def=meta[lineName]; if(!def) return;
  const box=document.createElement('div'); box.className=`record type-${lineName}`; box.dataset.line=lineName;
  const header=document.createElement('div'); header.className='row header-row';
  const titleMap={
    GenikaStoixeia:'Γενικά Στοιχεία',
    StoixeiaAsfalisis:'Στοιχεία Ασφάλισης',
    StoixeiaEpidotiseon:'Στοιχεία Επιδότησης',
    TelosArxeiou:'Τέλος Αρχείου',
  };
  const title=document.createElement('strong');
  title.textContent=(lineName==='StoixeiaAsfalismenou' ? '' : (titleMap[lineName] || lineName));
  const summary=document.createElement('span'); summary.style.fontSize='12px'; if(lineName==='StoixeiaAsfalismenou') summary.style.fontWeight='700';
  const pill=document.createElement('span'); pill.className='status-pill incomplete'; pill.textContent='Incomplete';
  const spacer=document.createElement('span'); spacer.className='spacer';
  const del=document.createElement('button'); del.className='secondary danger-soft remove-btn'; del.textContent='Remove'; del.onclick=()=>{
    if(!confirm('Are you sure you want to remove this section?')) return;
    const p=box.closest('.record[data-line="StoixeiaAsfalismenou"]');
    box.remove();
    if(p) setAsfalismenouSums(p);
    setSingletonButtons();
    markNeedsRecalc();
  };
  const tgl=document.createElement('button'); tgl.className='secondary fold-toggle'; tgl.textContent='−';
  header.append(title); if(lineName==='StoixeiaAsfalismenou'||lineName==='StoixeiaAsfalisis') header.append(summary); header.append(spacer,del,pill,tgl);
  const grid=document.createElement('div'); grid.className='grid'; const editable=[];

  def.columns.forEach(col=>{ if(lineName==='TelosArxeiou') return;
    const no=String(col.no), fixed=['*','#'].includes(col.vals)?false:!String(col.vals).includes(',');
    const optional=['6','7','54','55','58'].includes(no); const hdrAuto=lineName==='GenikaStoixeia'&&['16','17','18'].includes(no); const asfAuto=lineName==='StoixeiaAsfalisis'&&['48','49','50','51','52'].includes(no);
    const lab=document.createElement('label'); lab.append(document.createTextNode(`${no}. ${String(col.name).replaceAll('_',' ')} (${col.length}, ${col.typos})`));
    let f;
    // No 37 is a free code field (no dropdown): the code is shown as-is,
    // with the KPK description next to it when the code exists in kpk.js.
    const tOpts = no==='37'
        ? []
      : no==='60'
        ? (epidotiseisValues.length
            ? epidotiseisValues.map(x => ({ code: String(x.code), description: String(x.per || '') }))
            : (tableOptions['60'] || []))
        : (tableOptions[no] || []);
    const choices=tOpts.length?tOpts:(String(col.vals).includes(',')?String(col.vals).split(',').map(v=>({code:v.trim(),description:''})):[]);
    if(choices.length){
      f=document.createElement('select'); const b=document.createElement('option'); b.value=''; b.textContent='--'; f.appendChild(b);
      choices.forEach(o=>{const op=document.createElement('option'); op.value=o.code; op.textContent=o.description?`${o.code} - ${o.description}`:o.code; f.appendChild(op);});
      const pv=preset[col.name]==='__'?'':preset[col.name];
      const pvStr=(pv??'').toString();
      f.value=pvStr;
      const optionValues=[...f.options].map(o=>o.value);
      if(pvStr && !optionValues.includes(f.value)){
        // tolerate fixed-width parsed numeric codes (e.g. 00/0101) against table code (e.g. 0/101)
        const compact = normalizeNumericCode(pvStr);
        if(compact && optionValues.includes(compact)) {
          f.value=compact;
        } else if(compact) {
          const byDigits = optionValues.find(v => normalizeNumericCode(v) === compact);
          if(byDigits) f.value = byDigits;
        }
      }
      if(!pvStr && !importing && ['36','37'].includes(no)){
        if(optionValues.includes('0')) f.value='0';
        else if(choices[0]?.code) f.value=choices[0].code;
      }
      if(pvStr && !optionValues.includes(f.value)){
        // fallback only for No36; for No37 keep parsed value as custom so validation can flag it
        if(no==='36' && choices[0]?.code) f.value = choices[0].code;
        else {
          const custom=document.createElement('option'); custom.value=pvStr; custom.textContent=pvStr; custom.dataset.custom='1'; f.appendChild(custom); f.value=pvStr;
        }
      }
      if(!fixed && !optional){f.required=true; editable.push(f);}    
    } else {
      f=document.createElement('input'); const is3=no==='3', is31=no==='31', is35=no==='35';
      if(col.typos==='D') {
        // Greek locale-friendly editable mask format
        f.type='text';
        f.inputMode='numeric';
        f.placeholder='dd/mm/yyyy';
      } else if((col.typos==='F' || col.typos==='F3') && !is3 && !is31 && !is35) {
        // decimal numeric fields as text (supports Greek decimal comma input)
        f.type='text';
        f.inputMode='decimal';
      } else if((col.typos==='9' || col.typos==='V') && !is3 && !is31 && !is35) {
        // keep as text+numeric keypad to preserve leading zeros (e.g. No 36/37)
        f.type='text';
        f.inputMode='numeric';
      } else {
        f.type='text';
      }
      const pv=preset[col.name]==='__'?'':preset[col.name]; f.value=(pv??(fixed?col.vals:'')).toString();
      f.readOnly=fixed||hdrAuto||asfAuto; if(hdrAuto||asfAuto) f.placeholder='auto'; if(optional) f.placeholder='optional';
      if(!f.readOnly && !optional){f.required=true; editable.push(f);} 
      if(is3||is31||is35){
        f.readOnly=true;
        const row=document.createElement('div'); row.className='field-row';
        const btn=document.createElement('button'); btn.className='secondary toolbar-like ellipsis-btn'; btn.type='button'; btn.textContent='...';
        btn.onclick=()=>{
          if(is3) openLookup(f,'topic','Find TOPIK (No 3)');
          else if(is31) openLookup(f,'kad','Find KAD (No 31)');
          else openLookup(f,'eid','Find EID (No 35)');
        };
        row.append(f,btn);
        f.dataset.key=col.name;
        f.dataset.no=no;
        f.dataset.typos=col.typos;
        f.dataset.maxlen=col.length;
        lab.append(row); grid.append(lab);
        return;
      }
      if(no==='37'){
        const row=document.createElement('div'); row.className='field-row';
        const desc=document.createElement('span'); desc.className='code-desc';
        const updDesc=()=>{desc.textContent=kpkDescription(f.value);};
        f.addEventListener('input',updDesc); f.addEventListener('change',updDesc); updDesc();
        row.append(f,desc);
        f.dataset.key=col.name;
        f.dataset.no=no;
        f.dataset.typos=col.typos;
        f.dataset.maxlen=col.length;
        lab.append(row); grid.append(lab);
        return;
      }
    }
    f.dataset.key=col.name; f.dataset.no=no; f.dataset.typos=col.typos; f.dataset.maxlen=col.length; lab.append(f); grid.append(lab);
  });

  const refreshStatus=()=>{
    if(lineName==='TelosArxeiou'){pill.className='status-pill complete'; pill.textContent='Complete'; return;}
    if(lineName==='StoixeiaAsfalisis'){enforce40_41(box); validate40_41(box); enforce44(box); validate44(box);}    
    const ok=editable.every(f=>{const empty=String(f.value||'').trim()===''; if(empty&&allowsBlank(f,box)) return true; if(isOverLen(f)) return false; if(!f.checkValidity()) return false; return !empty;});
    pill.className='status-pill '+(ok?'complete':'incomplete'); pill.textContent=ok?'Complete':'Incomplete';
  };
  box.__refreshStatus=refreshStatus;
  editable.forEach(f=>{f.addEventListener('input',refreshStatus);f.addEventListener('change',refreshStatus);});

  box.append(header,grid); let children=null, childActions=null;
  if(lineName==='GenikaStoixeia'){
    const f3=grid.querySelector(':scope [data-no="3"]');
    const f4=grid.querySelector(':scope [data-no="4"]');
    if(f4){ f4.readOnly=true; f4.placeholder='auto'; }
    f3?.addEventListener('input', syncGenikaNo4FromNo3);
    f3?.addEventListener('change', syncGenikaNo4FromNo3);
    syncGenikaNo4FromNo3();
  }

  if(lineName==='StoixeiaAsfalismenou'){
    childActions=document.createElement('div'); childActions.className='row child-actions'; const b=document.createElement('button'); b.className='secondary toolbar-like'; b.textContent='+ Στοιχεία Ασφάλισης'; b.onclick=()=>addChildRecord(box,'StoixeiaAsfalisis'); childActions.append(b); box.append(childActions); children=document.createElement('div'); children.className='children-host'; box.append(children);
    const s=grid.querySelector('[data-key="Επώνυμο_Ασφαλισμένου"]'), n=grid.querySelector('[data-key="Όνομα_Ασφαλισμένου"]'); const upd=()=>summary.textContent=`${(s?.value||'-').trim()} ${(n?.value||'-').trim()}`; s?.addEventListener('input',upd); n?.addEventListener('input',upd); upd();
  }
  if(lineName==='StoixeiaAsfalisis'){
    const bw=document.createElement('div'); bw.className='row child-actions'; const be=document.createElement('button'); be.className='secondary toolbar-like'; be.textContent='+ Στοιχεία Επιδότησης'; be.onclick=()=>addEpidotisi(box); bw.append(be); box.append(bw); children=document.createElement('div'); children.className='children-host'; box.append(children);
    const f42=grid.querySelector('[data-no="42"]'); const upd=()=>{const v=(f42?.value||'').toString().trim(); let d=''; if(f42?.tagName==='SELECT'){const t=f42.options[f42.selectedIndex]?.textContent||''; d=t.includes(' - ')?t.split(' - ').slice(1).join(' - '):'';} summary.textContent=d?`No 42: ${v||'-'} (${d})`:`No 42: ${v||'-'}`}; f42?.addEventListener('input',upd); f42?.addEventListener('change',upd); upd();
    const f36=grid.querySelector('[data-no="36"]'), f46=grid.querySelector('[data-no="46"]'), f47=grid.querySelector('[data-no="47"]'), f53=grid.querySelector('[data-no="53"]'),f44=grid.querySelector('[data-no="44"]'),f56=grid.querySelector('[data-no="56"]');
    f36?.addEventListener('input',()=>{enforceBy36Optional(box); enforce44(box); validate44(box);});
    f36?.addEventListener('change',()=>{enforceBy36Optional(box); enforce44(box); validate44(box);});
    const upd48=()=>{recalc48(box); recalc51(box);};
    f46?.addEventListener('input',upd48); f46?.addEventListener('change',upd48);
    f47?.addEventListener('input',upd48); f47?.addEventListener('change',upd48);
    f53?.addEventListener('input',()=>recalc51(box)); f44?.addEventListener('input',()=>validate44(box));
    f56?.addEventListener('input',()=>{enforce44(box);validate44(box)}); f56?.addEventListener('change',()=>{enforce44(box);validate44(box)});
    f42?.addEventListener('input',()=>{enforce40_41(box);validate40_41(box)}); f42?.addEventListener('change',()=>{enforce40_41(box);validate40_41(box)});
    enforceBy36Optional(box); recalc48(box); recalc51(box); enforce40_41(box); validate40_41(box); enforce44(box); validate44(box);
  }

  const setCollapsed=(c)=>{grid.style.display=c?'none':'grid'; if(children) children.style.display=c?'none':'flex'; if(lineName==='StoixeiaAsfalismenou'){del.style.display=c?'none':'inline-block'; if(childActions) childActions.style.display=c?'none':'flex';} tgl.textContent=c?'+':'−';};
  box.__setCollapsed=setCollapsed; tgl.onclick=()=>setCollapsed(grid.style.display!=='none');

  if(returnOnly) return box;
  if(lineName==='StoixeiaAsfalisis'){const p=[...recordsEl.querySelectorAll('.record[data-line="StoixeiaAsfalismenou"]')].pop(); const host=p?.querySelector(':scope > .children-host'); if(!host) return alert('Need StoixeiaAsfalismenou first'); host.append(box); if(host.querySelectorAll(':scope > .record[data-line="StoixeiaAsfalisis"]').length>1) [...host.querySelectorAll(':scope > .record[data-line="StoixeiaAsfalisis"]')].forEach(r=>r.__setCollapsed?.(true));}
  else if(lineName==='StoixeiaEpidotiseon'){const a=[...recordsEl.querySelectorAll('.record[data-line="StoixeiaAsfalisis"]')].pop(); const host=a?.querySelector(':scope > .children-host'); if(!host) return alert('Need StoixeiaAsfalisis first'); host.append(box); setAsfalismenouSums(a.closest('.record[data-line="StoixeiaAsfalismenou"]'));}
  else if(lineName==='GenikaStoixeia') recordsEl.insertBefore(box, recordsEl.firstChild);
  else if(lineName==='TelosArxeiou') recordsEl.appendChild(box);
  else if(lineName==='StoixeiaAsfalismenou'){const eof=recordsEl.querySelector(':scope>.record[data-line="TelosArxeiou"]'); eof?recordsEl.insertBefore(box,eof):recordsEl.appendChild(box); const all=[...recordsEl.querySelectorAll(':scope>.record[data-line="StoixeiaAsfalismenou"]')]; if(all.length>1) all.forEach(r=>r.__setCollapsed?.(true));}
  else recordsEl.appendChild(box);
  setSingletonButtons(); refreshStatus(); markNeedsRecalc(); return box;
}

function addChildRecord(parent,line,preset={}){const c=addRecord(line,preset,true); const host=parent.querySelector(':scope > .children-host'); if(c&&host) host.append(c); if(line==='StoixeiaAsfalisis'){setAsfalismenouSums(parent);const rows=[...host.querySelectorAll(':scope > .record[data-line="StoixeiaAsfalisis"]')]; if(rows.length>1) rows.forEach(r=>r.__setCollapsed?.(true));} return c;}
function addEpidotisi(asf){
  const f32 = asf.querySelector(':scope > .grid [data-no="32"]');
  const f33 = asf.querySelector(':scope > .grid [data-no="33"]');
  const v32 = String(f32?.value||'').trim();
  const v33 = String(f33?.value||'').trim();
  const n32 = Number.isNaN(Number(v32)) ? v32 : String(parseInt(v32,10));
  const n33 = Number.isNaN(Number(v33)) ? v33 : String(parseInt(v33,10));

  if(!(n32==='1' && n33==='1')){
    alert('Δεν επιτρέπεται Στοιχεία Επιδότησης: απαιτείται No32=1 και No33=1 στο αντίστοιχο Στοιχεία Ασφάλισης.');
    return;
  }

  const host=asf.querySelector(':scope > .children-host');
  const e=addRecord('StoixeiaEpidotiseon',{},true);
  if(host&&e) host.append(e);

  const sync = () => {
    recalcEpidotisi(e);
    const p=asf.closest('.record[data-line="StoixeiaAsfalismenou"]');
    setAsfalismenouSums(p);
    e.__refreshStatus?.();
  };
  const f60 = e?.querySelector(':scope > .grid [data-no="60"]');
  const f45 = asf.querySelector(':scope > .grid [data-no="45"]');
  f60?.addEventListener('input', sync);
  f60?.addEventListener('change', sync);
  f45?.addEventListener('input', sync);
  f45?.addEventListener('change', sync);

  sync();
}
function recalc51(asf){const g=n=>parseFloat(asf.querySelector(`:scope > .grid [data-no="${n}"]`)?.value||0)||0; const f=asf.querySelector(':scope > .grid [data-no="51"]'); if(f){f.readOnly=true; if(importing) return; f.value=(g(48)-g(52)-g(53)).toFixed(2);}}

function round2(n){return Math.round((Number(n)||0)*100)/100;}

// Description of a KPK code (No 37) from kpk.js, '' when the code is unknown.
function kpkDescription(code){
  const raw = String(code||'').trim();
  if(!raw) return '';
  const compact = normalizeNumericCode(raw);
  const row = kpkValues.find(x => {
    const c = String(x.code||'').trim();
    return c === raw || (compact && normalizeNumericCode(c) === compact);
  });
  return row?.description || '';
}

// No 46 (employee) and No 47 (employer) are entered manually; No 48 is their sum.
function recalc48(asf){
  const f46 = asf.querySelector(':scope > .grid [data-no="46"]');
  const f47 = asf.querySelector(':scope > .grid [data-no="47"]');
  const f48 = asf.querySelector(':scope > .grid [data-no="48"]');
  if(!f46 || !f47 || !f48) return;
  f46.readOnly = false;
  f47.readOnly = false;
  f48.readOnly = true;
  if(importing) return;
  const num=f=>Number(String(f.value||0).replace(',', '.'))||0;
  f48.value = round2(num(f46) + num(f47)).toFixed(2);
}

function recalcEpidotisi(epi){
  const f60 = epi.querySelector(':scope > .grid [data-no="60"]');
  const f61 = epi.querySelector(':scope > .grid [data-no="61"]');
  const f62 = epi.querySelector(':scope > .grid [data-no="62"]');
  const f63 = epi.querySelector(':scope > .grid [data-no="63"]');
  const f64 = epi.querySelector(':scope > .grid [data-no="64"]');
  const f65 = epi.querySelector(':scope > .grid [data-no="65"]');
  if(!f60 || !f61 || !f62 || !f63 || !f64 || !f65) return;

  const parentAsf = epi.closest('.record[data-line="StoixeiaAsfalisis"]');
  const f45 = parentAsf?.querySelector(':scope > .grid [data-no="45"]');
  const no45 = Number(String(f45?.value || 0).replace(',', '.'));

  f61.readOnly = true; f62.readOnly = true; f63.readOnly = true; f64.readOnly = true; f65.readOnly = true;
  if(importing) return;

  const codeRaw = String(f60.value||'').trim();
  const codeCompact = Number.isNaN(Number(codeRaw)) ? codeRaw : String(parseInt(codeRaw,10));
  const row = epidotiseisValues.find(x => {
    const c = String(x.code||'').trim();
    if(c === codeRaw) return true;
    if(!Number.isNaN(Number(c)) && !Number.isNaN(Number(codeRaw)) && String(parseInt(c,10)) === codeCompact) return true;
    return false;
  });
  if(!row) return;

  const pemployee = Number(row.pemployee || 0);
  const pcompany = Number(row.pcompany || 0);
  f61.value = String(pemployee);
  f63.value = String(pcompany);

  const veRaw = String(row.vemployee || '').trim();
  const vcRaw = String(row.vcompany || '').trim();
  const no62 = veRaw === '*' ? round2(no45 * pemployee / 100) : round2(parsePercent(veRaw));
  const no64 = vcRaw === '*' ? round2(no45 * pcompany / 100) : round2(parsePercent(vcRaw));
  const no65 = round2(no62 + no64);

  f62.value = no62.toFixed(2);
  f64.value = no64.toFixed(2);
  f65.value = no65.toFixed(2);
}

function enforceBy36Optional(asf){
  const f36=asf.querySelector(':scope > .grid [data-no="36"]');
  const v36=normalizeNumericCode(f36?.value);
  const allow=v36==='14';
  ['32','33','34','56','57'].forEach(no=>{
    const f=asf.querySelector(`:scope > .grid [data-no="${no}"]`);
    if(!f) return;
    f.required = !allow;
    if(allow) f.setCustomValidity('');
  });
  return allow;
}

function enforce44(asf){
  const f44=asf.querySelector(':scope > .grid [data-no="44"]'), f56=asf.querySelector(':scope > .grid [data-no="56"]');
  if(!f44||!f56) return;
  const allowBy36 = enforceBy36Optional(asf);
  if(allowBy36 && String(f56.value||'').trim()===''){ f44.readOnly=false; return; }
  const v=String(parseInt(String(f56.value||'0').replace(/\D/g,'')||'0',10));
  if(v==='0'){if(!importing) f44.value='0'; f44.readOnly=true;} else f44.readOnly=false;
}
function validate44(asf){
  const f44=asf.querySelector(':scope > .grid [data-no="44"]'), f56=asf.querySelector(':scope > .grid [data-no="56"]');
  if(!f44||!f56) return true;
  const allowBy36 = enforceBy36Optional(asf);
  if(allowBy36 && String(f56.value||'').trim()===''){ f44.setCustomValidity(''); return true; }
  const v=String(parseInt(String(f56.value||'0').replace(/\D/g,'')||'0',10));
  const n=parseFloat(f44.value||0)||0;
  const ok=(v==='0')?Math.abs(n)<1e-9:(v==='1'?n>0:true);
  f44.setCustomValidity(ok?'':'No44/No56 rule');
  return ok;
}
function enforce40_41(asf){const f40=asf.querySelector(':scope > .grid [data-no="40"]'),f41=asf.querySelector(':scope > .grid [data-no="41"]'),f42=asf.querySelector(':scope > .grid [data-no="42"]'); if(!f40||!f41||!f42) return; const allow=['002','004','005','006'].includes(normalizeCode3(f42.value)); f40.required=!allow; f41.required=!allow; if(allow){f40.setCustomValidity('');f41.setCustomValidity('');}}
function validate40_41(asf){const f40=asf.querySelector(':scope > .grid [data-no="40"]'),f41=asf.querySelector(':scope > .grid [data-no="41"]'),f42=asf.querySelector(':scope > .grid [data-no="42"]'); if(!f40||!f41||!f42) return true; const allow=['002','004','005','006'].includes(normalizeCode3(f42.value)); if(allow){f40.setCustomValidity('');f41.setCustomValidity(''); return true;} const ok40=String(f40.value||'').trim()!==''; const ok41=String(f41.value||'').trim()!==''; f40.setCustomValidity(ok40?'':'No 40 required'); f41.setCustomValidity(ok41?'':'No 41 required'); return ok40&&ok41;}

function setAsfalismenouSums(person){
  if(!person || importing) return;
  const asf=[...person.querySelectorAll(':scope > .children-host > .record[data-line="StoixeiaAsfalisis"]')];

  // Each StoixeiaEpidotiseon affects only its own parent StoixeiaAsfalisis
  asf.forEach(a=>{
    const epi=[...a.querySelectorAll(':scope > .children-host > .record[data-line="StoixeiaEpidotiseon"]')];
    const s62=epi.reduce((x,r)=>x+(parseFloat(String(r.querySelector(':scope > .grid [data-no="62"]')?.value||0).replace(',', '.'))||0),0);
    const s64=epi.reduce((x,r)=>x+(parseFloat(String(r.querySelector(':scope > .grid [data-no="64"]')?.value||0).replace(',', '.'))||0),0);
    const s65=epi.reduce((x,r)=>x+(parseFloat(String(r.querySelector(':scope > .grid [data-no="65"]')?.value||0).replace(',', '.'))||0),0);

    const f49=a.querySelector(':scope > .grid [data-no="49"]');
    const f50=a.querySelector(':scope > .grid [data-no="50"]');
    const f52=a.querySelector(':scope > .grid [data-no="52"]');
    if(f49) f49.value=s62.toFixed(2);
    if(f50) f50.value=s64.toFixed(2);
    if(f52) f52.value=s65.toFixed(2);
    recalc51(a);
  });
}

function normalizeTextFields(){if(importing) return; recordsEl.querySelectorAll(':is(input,select)[data-key]').forEach(f=>{if(['Α','Χ'].includes(String(f.dataset.typos||'')) && !f.readOnly){const after=normalizeGreekUpperText(f.value); if(after!==f.value){f.value=after;}}});}

function collectRecords(){return [...recordsEl.querySelectorAll('.record')].map(card=>{const line_name=card.dataset.line; const values={}; card.querySelectorAll(':scope > .grid [data-key]').forEach(i=>{const no=String(i.dataset.no||''); const v=String(i.value||''); if(['54','55'].includes(no)&&v.trim()==='') values[i.dataset.key]='__'; else if(no==='58'&&v.trim()==='') values[i.dataset.key]='00'; else values[i.dataset.key]=v;}); return {line_name,values};});}
function applyHeaderTotals(records){
  const asf = records.filter(r=>r.line_name==='StoixeiaAsfalisis');
  const key43 = byNo('StoixeiaAsfalisis','43');
  const key45 = byNo('StoixeiaAsfalisis','45');
  const key51 = byNo('StoixeiaAsfalisis','51');

  const sum43 = asf.reduce((a,r)=>a+(parseFloat(r.values[key43]||0)||0),0);
  const sum45 = asf.reduce((a,r)=>a+(parseFloat(String(r.values[key45]||0).replace(',', '.'))||0),0);
  // Critical rule: No18 must sum ONLY StoixeiaAsfalisis No51 values
  const sum51 = asf.reduce((a,r)=>a+(parseFloat(String(r.values[key51]||0).replace(',', '.'))||0),0);

  const h=records.find(r=>r.line_name==='GenikaStoixeia');
  if(!h) return records;
  h.values['Σύνολο_Ημερών_Απασχόλησης']=String(Math.round(sum43));
  h.values['Σύνολο_Αποδοχών']=sum45.toFixed(2);
  h.values['Σύνολο_Καταβλητέων_Εισφορών']=sum51.toFixed(2);

  const card=recordsEl.querySelector('.record[data-line="GenikaStoixeia"]');
  if(card){
    [['Σύνολο_Ημερών_Απασχόλησης',h.values['Σύνολο_Ημερών_Απασχόλησης']],['Σύνολο_Αποδοχών',h.values['Σύνολο_Αποδοχών']],['Σύνολο_Καταβλητέων_Εισφορών',h.values['Σύνολο_Καταβλητέων_Εισφορών']]].forEach(([k,v])=>{const e=card.querySelector(`:scope > .grid [data-key="${k}"]`); if(e)e.value=v;});
  }
  return records;
}

function toDdMmYyyyDigits(v){
  const raw=String(v||'').trim();
  if(!raw) return '';
  const digits=raw.replace(/\D/g,'');
  if(digits.length!==8) return null;

  // yyyy-mm-dd or yyyy/mm/dd
  if(/^\d{4}[-/]\d{2}[-/]\d{2}$/.test(raw)) return `${digits.slice(6,8)}${digits.slice(4,6)}${digits.slice(0,4)}`;
  // dd-mm-yyyy or dd/mm/yyyy
  if(/^\d{2}[-/]\d{2}[-/]\d{4}$/.test(raw)) return digits;

  // bare 8 digits: infer if starts with plausible year
  const y=Number(digits.slice(0,4));
  if(y>=1900 && y<=2099) return `${digits.slice(6,8)}${digits.slice(4,6)}${digits.slice(0,4)}`;
  return digits; // assume ddmmyyyy
}

function formatValue(col,val){const v=val??''; const t=col.typos, len=Number(col.length);
  if(t==='9'||t==='V'){if(v===''||v==null) return '0'.repeat(len); const s=String(v); if(!/^\d+$/.test(s) && String(col.vals).split(',').includes(s)){ if(s==='__') return ' '.repeat(len); return s.padEnd(len,' ').slice(0,len);} const d=s.replace(/\D/g,''); if(d.length>len) throw new Error(`Value too long for ${col.name}`); return d.padStart(len,'0');}
  if(t==='F'||t==='F3'){
    const dp=t==='F3'?3:2;
    const norm = String(v||'0').replace(',', '.');
    const n=Number(norm||0);
    if(!Number.isFinite(n)) throw new Error(`Invalid numeric value for ${col.name}`);
    const s=n.toFixed(dp).replace('.','');
    if(s.replace('-','').length>len) throw new Error(`Value too long for ${col.name}`);
    return s.padStart(len,'0');
  }
  if(t==='D'){const d=toDdMmYyyyDigits(v); if(d==='') return '0'.repeat(8); if(!d) throw new Error(`Invalid date for ${col.name}`); return d;}
  const s=String(v||''); if(s.length>len) throw new Error(`Value too long for ${col.name}`); return s.padEnd(len,' ');
}

function validateAllowed(col,val){const vals=String(col.vals||''); if(vals==='*'||vals==='#') return true; const list=vals.split(','); let s=String(val??''); if(col.typos==='9') s=s.padStart(Number(col.length),'0'); return list.includes(s);}

function buildLine(lineName,values){
  const d=meta[lineName];
  let out='';

  const noToName = Object.fromEntries(d.columns.map(c=>[String(c.no), c.name]));
  const isAsf = lineName==='StoixeiaAsfalisis';
  const no36Name = noToName['36'];
  const v36 = isAsf ? normalizeNumericCode(values[no36Name]) : '';

  d.columns.forEach(col=>{
    let v;
    if(col.vals!=='*'&&col.vals!=='#'&&!String(col.vals).includes(',')) v=col.vals;
    else v=values[col.name];

    const no = String(col.no);
    const blankBy36 = isAsf && v36==='14' && ['32','33','34','56','57'].includes(no) && String(v??'').trim()==='';
    if(blankBy36){
      out += ' '.repeat(Number(col.length));
      return;
    }

    if(!validateAllowed(col,v)) throw new Error(`Invalid value '${v}' for column ${col.name}. Valid values: ${col.vals}`);
    out += formatValue(col,v);
  });

  if(out.length!==Number(d.line_size)) throw new Error(`${lineName} wrong length`);
  return out;
}

function buildAPD(records){if(!records.length) throw new Error('APD has no lines'); const lines=records.map(r=> r.line_name==='TelosArxeiou' ? 'EOF' : buildLine(r.line_name,r.values)); if(!lines[0].startsWith('1')) throw new Error('APD must start with type 1'); if(!lines.some(l=>l.startsWith('2'))) throw new Error('APD needs at least one type 2'); if(lines[lines.length-1]!=='EOF') throw new Error('APD must end with EOF');
  // Κάθε εγγραφή τελειώνει σε CR LF (0D 0A) — και η τελευταία (EOF).
  return lines.join('\r\n')+'\r\n';}

function decodeDate(ddmmyyyy){const d=String(ddmmyyyy||''); if(!/^[0-9]{8}$/.test(d) || d==='00000000') return ''; return `${d.slice(0,2)}/${d.slice(2,4)}/${d.slice(4,8)}`;}
function decodeFixed(part, col){
  const raw = String(part||'');
  const trimmed = raw.trim();
  if(col.typos==='D') return decodeDate(trimmed);
  if(col.typos==='F' || col.typos==='F3'){
    const dp = col.typos==='F3' ? 3 : 2;
    const digits = raw.replace(/\D/g,'');
    if(!digits) return dp===3 ? '0.000' : '0.00';
    const n = Number(digits) / (10 ** dp);
    return n.toFixed(dp);
  }
  if(col.typos==='9' || col.typos==='V'){
    if(raw.includes('_')) return '__';

    const no = String(col.no||'');
    // No 37 is a free code field: keep the code exactly as stored in the file.
    if(no==='37') return trimmed;

    const opts = (tableOptions[no] || []).map(x => String(x.code));

    if(trimmed===''){
      // Keep blanks as-is on import; defaults are applied only on recalculation.
      if(!importing && no==='36'){
        if(opts.includes('0')) return '0';
        if(opts[0]) return opts[0];
      }
      return '';
    }

    if(opts.length){
      if(opts.includes(trimmed)) return trimmed;
      const compact = String(parseInt(trimmed,10));
      if(!Number.isNaN(Number(trimmed)) && opts.includes(compact)) return compact;
      if(!Number.isNaN(Number(trimmed))){
        const byDigits = opts.find(v => {
          const d = String(v||'').replace(/\D/g,'');
          return d && String(parseInt(d,10)) === compact;
        });
        if(byDigits) return byDigits;
      }
    }

    // business preference: No 43 should be shown as plain number (no leading zeros)
    if(no === '43' && !Number.isNaN(Number(trimmed))) return String(parseInt(trimmed,10));

    return trimmed;
  }
  return trimmed;
}
function parseAPDText(text){const lines=text.split(/\r?\n/).filter(Boolean); const records=[]; for(const ln of lines){if(ln==='EOF'){records.push({line_name:'TelosArxeiou',values:{'Τέλος_Αρχείου':'EOF'}}); continue;} const t=ln[0]; let lineName=t==='1'?'GenikaStoixeia':t==='2'?'StoixeiaAsfalismenou':t==='3'?'StoixeiaAsfalisis':t==='4'?'StoixeiaEpidotiseon':null; if(!lineName) continue; const def=meta[lineName]; if(ln.length!==Number(def.line_size)) continue; const values={}; let pos=0; def.columns.forEach(c=>{const part=ln.slice(pos,pos+Number(c.length)); pos+=Number(c.length); values[c.name]=decodeFixed(part,c);}); records.push({line_name:lineName,values}); }
  return {records}; }

function encodeText(str,enc){ if(enc==='utf-8') return new TextEncoder().encode(str);
  const map={
    'Α':0xC1,'Β':0xC2,'Γ':0xC3,'Δ':0xC4,'Ε':0xC5,'Ζ':0xC6,'Η':0xC7,'Θ':0xC8,'Ι':0xC9,'Κ':0xCA,'Λ':0xCB,'Μ':0xCC,'Ν':0xCD,'Ξ':0xCE,'Ο':0xCF,'Π':0xD0,'Ρ':0xD1,'Σ':0xD3,'Τ':0xD4,'Υ':0xD5,'Φ':0xD6,'Χ':0xD7,'Ψ':0xD8,'Ω':0xD9
  }; const arr=[]; for(const ch of str){const code=ch.charCodeAt(0); if(code<128) arr.push(code); else if(map[ch]!=null) arr.push(map[ch]); else arr.push(0x20);} return new Uint8Array(arr);
}

function recalculateTotals(){normalizeTextFields(); recordsEl.querySelectorAll('.record[data-line="StoixeiaAsfalisis"]').forEach(b=>{enforceBy36Optional(b);enforce40_41(b);validate40_41(b);enforce44(b);validate44(b);recalc48(b); b.querySelectorAll(':scope > .children-host > .record[data-line="StoixeiaEpidotiseon"]').forEach(recalcEpidotisi); recalc51(b);b.__refreshStatus?.();}); recordsEl.querySelectorAll(':scope > .record[data-line="StoixeiaAsfalismenou"]').forEach(setAsfalismenouSums); const records=applyHeaderTotals(collectRecords()); recordsEl.querySelectorAll('.record').forEach(b=>b.__refreshStatus?.());
  const btn=document.getElementById('btnRecalc'); if(btn){btn.classList.remove('recalc-flash'); void btn.offsetWidth; btn.classList.add('recalc-flash');}
  const issues=[]; [...recordsEl.querySelectorAll('.record')].forEach((box,i)=>{const line=box.dataset.line; const fields=[...box.querySelectorAll(':scope > .grid [data-key]')].filter(f=>!f.readOnly); const det=[]; fields.forEach(f=>{const empty=String(f.value||'').trim()===''; const invalid=!f.checkValidity(); const over=isOverLen(f); if((allowsBlank(f,box)&&empty&&!invalid&&!over)||(!empty&&!invalid&&!over)) return; const label=(f.closest('label')?.childNodes?.[0]?.textContent||f.dataset.key||'Field').trim(); let reason=over?`text length exceeds max ${f.dataset.maxlen} chars`:(empty?'required value is missing':(f.validationMessage||'invalid value')); det.push(` - ${label}: ${reason}`);}); if(det.length){issues.push(`${i+1}. ${line}`,...det);}});
  if(issues.length){
    setRecalcIndicator(false);
    alert(`Recalculation finished with issues:\n\n${issues.join('\n')}`);
  } else {
    setRecalcIndicator(true);
  }
}

async function generate(){try{recalculateTotals(); let records=applyHeaderTotals(collectRecords()); const txt=buildAPD(records); document.getElementById('outText').value=txt; const enc=document.getElementById('genEncoding').value; const bytes=encodeText(txt,enc); const blob=new Blob([bytes],{type:'text/plain'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=`apd_output_${enc}.txt`; a.click(); URL.revokeObjectURL(a.href);}catch(e){alert(`Generate APD failed: ${e.message||e}`)}}

async function parseFile(){
  const f=document.getElementById('fileIn').files[0];
  if(!f) return alert('Select file');
  const enc=document.getElementById('parseEncoding').value;
  const buf=await f.arrayBuffer();
  let txt='';
  try{txt=new TextDecoder(enc).decode(buf);}catch{txt=new TextDecoder('utf-8').decode(buf);}
  importing=true;
  try{
    const parsed=parseAPDText(txt);
    document.getElementById('parsedJson').value=JSON.stringify(parsed,null,2);
    fillFormFromParsed(parsed.records||[]);
  } finally {
    importing=false;
  }
}
function fillFormFromParsed(records){
  recordsEl.innerHTML='';
  records.forEach(r=>addRecord(r.line_name,r.values));

  // post-parse normalization for select codes
  recordsEl.querySelectorAll('.record[data-line="StoixeiaAsfalisis"]').forEach(box => {
    const sel36 = box.querySelector(':scope > .grid select[data-no="36"]');
    if(!importing && sel36 && String(sel36.value||'').trim()===''){
      const vals = [...sel36.options].map(o=>o.value).filter(Boolean);
      if(vals.includes('0')) sel36.value='0';
      else if(vals[0]) sel36.value=vals[0];
    }
  });

  setSingletonButtons();
  syncGenikaNo4FromNo3();
  // No recalculation on import: the file is shown exactly as it is.
  // The user triggers recalculation explicitly via the Recalculate button.
  recordsEl.querySelectorAll('.record').forEach(b=>b.__refreshStatus?.());
  markNeedsRecalc();
}

// wiring
document.getElementById('btnAddGenika').onclick=()=>addRecord('GenikaStoixeia');
document.getElementById('btnAddAsf').onclick=()=>addRecord('StoixeiaAsfalismenou');
document.getElementById('btnAddEof').onclick=()=>addRecord('TelosArxeiou');
document.getElementById('btnRecalc').onclick=recalculateTotals;
document.getElementById('btnGenerate').onclick=generate;
document.getElementById('btnParse').onclick=parseFile;
document.getElementById('btnClear').onclick=()=>{
  if(!confirm('Are you sure you want to clear all sections?')) return;
  recordsEl.innerHTML='';
  document.getElementById('parsedJson').value='';
  document.getElementById('outText').value='';
  setSingletonButtons();
  markNeedsRecalc();
};
document.getElementById('closeLookup').onclick=closeLookup;
document.getElementById('lookupModal').onclick=(e)=>{if(e.target.id==='lookupModal') closeLookup();};
document.getElementById('lookupSearch').oninput=(e)=>searchLookup(e.target.value);

recordsEl.addEventListener('input', (e)=>{ if(e.target?.matches('[data-key]')) markNeedsRecalc(); });
recordsEl.addEventListener('change', (e)=>{ if(e.target?.matches('[data-key]')) markNeedsRecalc(); });

const helpModal = document.getElementById('helpModal');
document.getElementById('btnHelp').onclick=()=>{helpModal.style.display='flex';};
document.getElementById('closeHelp').onclick=()=>{helpModal.style.display='none';};
helpModal.onclick=(e)=>{if(e.target===helpModal) helpModal.style.display='none';};

setSingletonButtons();
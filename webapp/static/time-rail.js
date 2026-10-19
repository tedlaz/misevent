/* ΡΑΓΑ ΩΡΑΡΙΟΥ — οπτικός επεξεργαστής των πεδίων ωραρίου ημέρας
   -------------------------------------------------------------------------
   Progressive enhancement, όπως το `date-gr.js`: κάθε πεδίο με
   `data-time-rail` παίρνει μια ράγα 00:00 → 09:00 της επομένης, όπου οι
   βάρδιες σύρονται, αλλάζουν άκρες (βήμα 15΄) και γίνονται τηλεεργασία.

   ⚠️ ΤΟ ΠΕΔΙΟ ΚΕΙΜΕΝΟΥ ΜΕΝΕΙ Η ΠΗΓΗ ΑΛΗΘΕΙΑΣ: κρατά name/value στη ΓΝΩΣΤΗ
   μορφή («09:00-13:00+Τ16:00-20:00»), άρα υποβάλλεται ό,τι και πριν και
   ΚΑΝΕΝΑ view/έλεγχος δεν αλλάζει. Οι κανόνες είναι αντίγραφο του
   `constraints.parse_day_schedule` / `check_daily_rest` ΜΟΝΟ για άμεση
   ένδειξη — ο αυθεντικός έλεγχος μένει στον server.

   Δύο μορφές:
   • `data-time-rail-week` σε container με 7 πεδία `data-time-rail="Δευτέρα"`:
     ΞΑΝΑΧΤΙΖΕΙ το container σε πλέγμα με ΚΟΙΝΟ άξονα (οι βάρδιες
     συγκρίνονται κάθετα), ανάπαυση 11ω μεταξύ διαδοχικών ημερών (κυκλικά,
     όπως το check_schedule), σύνολο εβδομάδας vs σύμβαση. Σύμβαση από
     `data-contract-hours` ή από το πεδίο `data-contract-field` της φόρμας·
     `data-childcare-hours` = μειωμένο ωράριο φροντίδας τέκνου (ώρ./ημέρα).
   • σκέτο `data-time-rail` (ένα πεδίο): η ράγα μπαίνει ΜΕΤΑ το <label> του
     (ποτέ μέσα: κλικ στη ράγα θα εστίαζε το πεδίο). */
(() => {
  const DOM = 1980, SNAP = 15, MIN_GAP = 180, MAX_NEXT = 540, REST = 660;
  const PRESETS = ["09:00-17:00", "08:00-16:00", "07:00-15:00", "15:00-23:00",
    "22:00-06:00", "09:00-13:00+17:00-21:00"];
  const WEEKDAYS = 5;

  const pad = n => String(n).padStart(2, "0");
  const hm = m => `${pad(Math.floor(m / 60) % 24)}:${pad(m % 60)}`;
  const hrs = m => (m / 60).toLocaleString("el", {maximumFractionDigits: 2}) + "ω";
  const pct = m => (m / DOM * 100) + "%";
  const snap = m => Math.round(m / SNAP) * SNAP;
  const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");

  /** Αντίγραφο του constraints.parse_day_schedule -> {segs} ή {err}. */
  function parse(str) {
    const parts = (str || "").replace(/,/g, "+").split("+").map(p => p.trim()).filter(Boolean);
    if (!parts.length) return {segs: []};
    if (parts.length > 2) return {err: `Έως 2 τμήματα ανά ημέρα — δόθηκαν ${parts.length}`};
    const segs = [];
    for (const p of parts) {
      const t = /^[ΤτTt]/.test(p), core = t ? p.slice(1).trim() : p;
      const m = /^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$/.exec(core);
      if (!m) return {err: `Μη έγκυρο τμήμα «${p}» — μορφή ΩΩ:ΛΛ-ΩΩ:ΛΛ`};
      const [a, b, c, d] = m.slice(1).map(Number);
      if (a > 23 || c > 23 || b > 59 || d > 59) return {err: `Μη έγκυρη ώρα στο «${p}»`};
      let s = a * 60 + b, e = c * 60 + d;
      if (e === s) return {err: `Η λήξη είναι ίδια με την έναρξη (${p})`};
      if (e < s) {
        if (e > MAX_NEXT) return {err: `Η λήξη της επόμενης ημέρας πρέπει να είναι έως 09:00 (${p})`};
        e += 1440;
      }
      segs.push({s, e, t});
    }
    segs.sort((x, y) => x.s - y.s);
    return {segs};
  }
  const format = segs => segs.map(g => `${g.t ? "Τ" : ""}${hm(g.s)}-${hm(g.e)}`).join("+");

  /** Συντομογραφίες: 5-12 · 0500-1200 · 1730 130 · τ9-17 -> κανονική μορφή. */
  function normTime(x) {
    x = x.trim();
    let h, m;
    const r = /^(\d{1,2})(?:[:.](\d{1,2}))?$/.exec(x);
    if (r) { h = +r[1]; m = +(r[2] || 0); }
    else if (/^\d{3,4}$/.test(x)) { h = +x.slice(0, -2); m = +x.slice(-2); }
    else return null;
    return h > 23 || m > 59 ? null : `${pad(h)}:${pad(m)}`;
  }
  function normalize(str) {
    const parts = (str || "").replace(/,/g, "+").split("+").map(p => p.trim()).filter(Boolean);
    const out = [];
    for (const p of parts) {
      const t = /^[ΤτTt]/.test(p), core = t ? p.slice(1).trim() : p;
      const xy = core.split(/\s*[-–—]\s*|\s+/).filter(Boolean);
      if (xy.length !== 2) return str;
      const a = normTime(xy[0]), b = normTime(xy[1]);
      if (!a || !b) return str;
      out.push(`${t ? "Τ" : ""}${a}-${b}`);
    }
    return out.join("+");
  }

  function ruler() {
    const r = document.createElement("div");
    r.className = "trl-ruler";
    r.setAttribute("aria-hidden", "true");
    let html = "";
    for (let h = 0; h <= 33; h += 3) {
      html += `<span class="${h === 24 ? "mid" : ""}" style="left:${h / 33 * 100}%">${pad(h % 24)}</span>`;
    }
    r.innerHTML = html + `<em style="left:${24 / 33 * 100}%">επόμενη ημέρα</em>`;
    return r;
  }

  /* ------------------------------------------------------------ ομάδα */
  function build(host, inputs, week) {
    const G = {rows: [], active: 0, sel: 0, week};
    const grid = document.createElement("div");
    grid.className = "trl-grid" + (week ? "" : " one");
    const rr = document.createElement("div");
    rr.className = "trl-rcell";
    rr.append(ruler());
    grid.append(rr);

    inputs.forEach((input, idx) => {
      const name = input.dataset.timeRail || "";
      const row = {input, name, idx};
      if (week) {
        const day = document.createElement("div");
        day.className = "trl-day";
        day.innerHTML = `<span>${esc(name)}</span><small></small>`;
        const tc = document.createElement("div");
        tc.className = "trl-textcell";
        tc.append(input);  // ΜΕΤΑΚΙΝΕΙΤΑΙ — το name/value μένουν ως έχουν
        row.sub = day.querySelector("small");
        row.sum = document.createElement("div");
        row.sum.className = "trl-sum";
        row.cells = [day, tc];
        day.addEventListener("click", () => input.focus());
      }
      const kc = document.createElement("div");
      kc.className = "trl-trackcell";
      row.track = document.createElement("div");
      row.track.className = "trl-track";
      row.track.setAttribute("role", "group");
      row.track.setAttribute("aria-label", `Ράγα ωραρίου ${name}`.trim());
      kc.append(row.track);
      row.msg = document.createElement("div");
      row.msg.className = "trl-msg";
      row.cells = [...(row.cells || []), kc, ...(week ? [row.sum] : []), row.msg];
      const r = document.createElement("div");
      r.className = "trl-row";
      r.append(...row.cells);
      row.el = r;
      grid.append(r);

      input.classList.add("trl-text");
      input.autocomplete = "off";
      input.spellcheck = false;
      const p = parse(input.value);
      row.segs = p.segs || [];
      row.err = p.err || null;
      G.rows.push(row);
      bindInput(G, row);
      bindTrack(G, row);
    });

    G.tools = document.createElement("div");
    G.tools.className = "trl-tools";
    G.footer = document.createElement("div");
    G.footer.className = "trl-total";
    host.append(grid, G.tools);
    if (week) host.append(G.footer);

    // Σύμβαση: σταθερή (data-contract-hours) ή ζωντανά από πεδίο της φόρμας
    const form = host.closest("form");
    const field = host.dataset.contractField && form ? form.elements[host.dataset.contractField] : null;
    G.contract = () => +(field ? field.value : host.dataset.contractHours) || 0;
    G.childcare = +host.dataset.childcareHours || 0;
    if (field) field.addEventListener("input", () => render(G, false));

    if (window.ResizeObserver) new ResizeObserver(() => render(G, false)).observe(grid);
    render(G);
  }

  function setSegs(G, row, segs) {
    segs.sort((x, y) => x.s - y.s);
    row.segs = segs;
    row.err = null;
    row.input.value = format(segs);
    // Τα ΙΔΙΑ events με την πληκτρολόγηση — ώστε να τρέχουν htmx/άλλα scripts
    row.input.dispatchEvent(new Event("input", {bubbles: true}));
    row.input.dispatchEvent(new Event("change", {bubbles: true}));
  }

  function activate(G, row, sel = 0) {
    if (G.active === row.idx && G.sel === sel) return;
    G.active = row.idx;
    G.sel = sel;
    render(G);
  }

  function bindInput(G, row) {
    const {input} = row;
    input.addEventListener("focus", () => activate(G, row, G.active === row.idx ? G.sel : 0));
    input.addEventListener("input", () => {
      const p = parse(input.value);
      if (p.err) row.err = p.err;
      else { row.segs = p.segs; row.err = null; }
      render(G, false);
    });
    const commit = () => {
      const n = normalize(input.value), p = parse(n);
      if (!p.err && n !== input.value) {
        input.value = format(p.segs);
        row.segs = p.segs;
        row.err = null;
        input.dispatchEvent(new Event("change", {bubbles: true}));
      }
      render(G, false);
    };
    input.addEventListener("blur", commit);
    // Enter = κανονικοποίηση, ΟΧΙ υποβολή της φόρμας
    input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); commit(); } });
  }

  /* ------------------------------------------------------------ ράγα */
  function bindTrack(G, row) {
    const {track} = row;
    let drag = null;
    const minuteAt = x => {
      const r = track.getBoundingClientRect();
      return Math.max(0, Math.min(DOM, (x - r.left) / r.width * DOM));
    };
    const bounds = (segs, i) => ({lo: i > 0 ? segs[i - 1].e : 0, hi: i < segs.length - 1 ? segs[i + 1].s : DOM});

    track.addEventListener("pointerdown", e => {
      if (e.button !== 0 || row.input.disabled || row.input.readOnly) return;
      const segs = row.segs.map(g => ({...g})), bar = e.target.closest(".trl-bar");
      if (bar) {
        const i = +bar.dataset.i;
        const mode = e.target.classList.contains("l") ? "l" : e.target.classList.contains("r") ? "r" : "move";
        drag = {mode, i, segs, x0: e.clientX, m0: minuteAt(e.clientX), orig: {...segs[i]}, moved: false};
        G.active = row.idx;
        G.sel = i;
      } else {
        G.active = row.idx;
        if (segs.length >= 2 || row.err) { render(G); return; }
        const m = Math.min(1440 - SNAP, snap(minuteAt(e.clientX)));
        if (segs.some(g => m >= g.s && m < g.e)) return;
        segs.push({s: m, e: m + SNAP, t: false});
        segs.sort((x, y) => x.s - y.s);
        const i = segs.findIndex(g => g.s === m);
        G.sel = i;
        drag = {mode: "new", i, segs, x0: e.clientX, m0: m, orig: {...segs[i]}, moved: false};
      }
      track.setPointerCapture(e.pointerId);
      e.preventDefault();
      render(G);
    });
    track.addEventListener("pointermove", e => {
      if (!drag) return;
      if (Math.abs(e.clientX - drag.x0) > 3) drag.moved = true;
      if (!drag.moved) return;
      const {segs, i, orig} = drag, g = segs[i], {lo, hi} = bounds(segs, i);
      const cur = snap(minuteAt(e.clientX));
      if (drag.mode === "move") {
        const L = orig.e - orig.s, d = snap(minuteAt(e.clientX) - drag.m0);
        g.s = Math.max(lo, Math.min(orig.s + d, Math.min(1440 - SNAP, hi - L)));
        g.e = g.s + L;
      } else if (drag.mode === "l") {
        g.s = Math.max(lo, Math.min(cur, g.e - SNAP, 1440 - SNAP));
      } else {  // "r" ή "new"
        g.e = Math.min(hi, g.s + 1440 - SNAP, Math.max(cur, g.s + SNAP));
      }
      setSegs(G, row, segs.map(x => ({...x})));
      render(G, false, {row, i: row.segs.findIndex(x => x.s === g.s)});
    });
    const end = () => {
      if (!drag) return;
      if (drag.mode === "new" && !drag.moved) {
        // Κλικ χωρίς σύρσιμο: 8ωρη βάρδια (4ωρη αν υπάρχει ήδη μία)
        const {segs, i} = drag, g = segs[i], {hi} = bounds(segs, i);
        g.e = Math.min(hi, g.s + (segs.length > 1 ? 240 : 480));
        if (g.e - g.s < SNAP) segs.splice(i, 1);
        setSegs(G, row, segs);
      }
      drag = null;
      render(G);
    };
    track.addEventListener("pointerup", end);
    track.addEventListener("pointercancel", end);

    track.addEventListener("keydown", e => {
      const bar = e.target.closest(".trl-bar");
      if (!bar) return;
      const i = +bar.dataset.i, segs = row.segs.map(g => ({...g})), g = segs[i], {lo, hi} = bounds(segs, i);
      const step = e.shiftKey ? 60 : SNAP;
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        const d = e.key === "ArrowLeft" ? -step : step;
        if (e.altKey) g.e = Math.max(g.s + SNAP, Math.min(hi, g.s + 1440 - SNAP, g.e + d));
        else { const L = g.e - g.s; g.s = Math.max(lo, Math.min(g.s + d, 1440 - SNAP, hi - L)); g.e = g.s + L; }
      } else if (e.key === "Delete" || e.key === "Backspace") {
        segs.splice(i, 1);
      } else if (e.key === "t" || e.key === "T" || e.key === "τ" || e.key === "Τ") {
        g.t = !g.t;
      } else return;
      e.preventDefault();
      setSegs(G, row, segs);
      G.active = row.idx;
      G.sel = Math.max(0, Math.min(i, segs.length - 1));
      render(G);
      const b = track.querySelector(`.trl-bar[data-i="${G.sel}"]`);
      if (b) b.focus({preventScroll: true});
    });
  }

  /* ------------------------------------------------------------ σχεδίαση */
  function render(G, withTools = true, tip = null) {
    const n = G.rows.length, cyclic = G.week && n === 7;
    let total = 0, workDays = 0;
    G.rows.forEach((row, idx) => {
      const {segs, err, track} = row;
      const prevRow = cyclic ? G.rows[(idx + n - 1) % n] : null;
      row.el.classList.toggle("active", G.week && idx === G.active);
      row.input.classList.toggle("trl-bad", !!err);
      const mins = err ? 0 : segs.reduce((a, g) => a + g.e - g.s, 0);
      total += mins;
      if (mins) workDays += 1;
      if (row.sum) {
        row.sum.textContent = mins ? hrs(mins) : "ρεπό";
        row.sum.classList.toggle("zero", !mins);
      }
      if (row.sub) {
        row.sub.textContent = err ? "" : segs.length === 2 ? "σπαστό"
          : segs.some(g => g.e > 1440) ? "περνά τα μεσάνυχτα" : "";
      }
      track.classList.toggle("full", segs.length >= 2 || !!err);

      let html = "";
      const msgs = [];
      // Η βάρδια της ΠΡΟΗΓΟΥΜΕΝΗΣ ημέρας που περνά τα μεσάνυχτα, ως «φάντασμα»
      const pl = prevRow && prevRow.segs.length && !prevRow.err ? prevRow.segs[prevRow.segs.length - 1] : null;
      if (pl && pl.e > 1440) {
        html += `<div class="trl-ghost${pl.t ? " tele" : ""}" style="left:0;width:${pct(pl.e - 1440)}" title="Συνέχεια από ${esc(prevRow.name)}"></div>`;
      }
      if (err) msgs.push(`<span class="trl-chip prob">${esc(err)}</span>`);
      if (!err && segs.length === 2) {
        const gap = segs[1].s - segs[0].e;
        if (gap < MIN_GAP) {
          html += `<div class="trl-bad-zone" style="left:${pct(segs[0].e)};width:${pct(Math.max(gap, 6))}"></div>`;
          msgs.push(`<span class="trl-chip prob">Κενό σπαστού ${hrs(gap)} — χρειάζεται τουλάχιστον 3ω</span>`);
        }
      }
      if (!err && segs.length && pl) {
        const rest = segs[0].s + 1440 - pl.e;
        if (rest < REST) {
          const a = Math.max(0, pl.e - 1440);
          html += `<div class="trl-bad-zone" style="left:${pct(a)};width:${pct(Math.max(segs[0].s - a, 6))}"></div>`;
          msgs.push(`<span class="trl-chip warn">Ανάπαυση από ${esc(prevRow.name)}: ${hrs(rest)} — ελάχιστο 11ω</span>`);
        }
      }
      if (!err) segs.forEach((g, i) => {
        const w = track.clientWidth * (g.e - g.s) / DOM;
        const label = w > 150 ? `${hm(g.s)}–${hm(g.e)} · ${hrs(g.e - g.s)}` : w > 50 ? hrs(g.e - g.s) : "";
        const sel = idx === G.active && i === G.sel;
        html += `<div class="trl-bar${g.t ? " tele" : ""}${sel ? " sel" : ""}" data-i="${i}" tabindex="0"
          role="slider" aria-label="${esc(row.name)} βάρδια ${i + 1}${g.t ? ", τηλεεργασία" : ""}"
          aria-valuetext="${hm(g.s)} έως ${hm(g.e)}, ${hrs(g.e - g.s)}"
          title="${g.t ? "Τηλεεργασία " : ""}${hm(g.s)}–${hm(g.e)} (${hrs(g.e - g.s)})"
          style="left:${pct(g.s)};width:${pct(g.e - g.s)}"><i class="h l"></i><span>${label}</span><i class="h r"></i></div>`;
      });
      if (tip && tip.row === row && segs[tip.i]) {
        const g = segs[tip.i];
        html += `<div class="trl-tip" style="left:${pct((g.s + g.e) / 2)}">${hm(g.s)}–${hm(g.e)} · ${hrs(g.e - g.s)}</div>`;
      }
      track.innerHTML = html;
      row.msg.innerHTML = msgs.join("");
    });

    if (G.week) {
      // Σύνολο vs σύμβαση· με μειωμένο ωράριο φροντίδας τέκνου δεκτό το εύρος
      const c = G.contract() * 60, low = c - G.childcare * 60 * workDays, d = total - c;
      let chip = "";
      if (c) {
        chip = Math.abs(d) < 1 ? `<span class="trl-chip ok">✓ ίσο με τη σύμβαση</span>`
          : G.childcare && total >= low - 1 && d < 0 ? `<span class="trl-chip ok">✓ μειωμένο ωράριο φροντίδας τέκνου</span>`
          : d < 0 ? `<span class="trl-chip warn">λείπουν ${hrs(-d)}</span>`
          : `<span class="trl-chip info">+${hrs(d)} πάνω από τη σύμβαση</span>`;
      }
      const meter = c ? Math.min(100, total / c * 100) : 0;
      G.footer.innerHTML = `<span>Σύνολο <b>${hrs(total)}</b>${c ? ` / σύμβαση ${hrs(c)}` : ""}</span>
        <span class="trl-meter" aria-hidden="true"><i style="width:${meter}%"></i></span>${chip}`;
    }
    if (withTools) renderTools(G);
  }

  function renderTools(G) {
    const row = G.rows[G.active], segs = row.segs, i = Math.min(G.sel, segs.length - 1), g = segs[i];
    const locked = row.input.disabled || row.input.readOnly;
    G.tools.innerHTML = `
      ${G.week ? `<b>${esc(row.name)}</b>` : ""}
      ${PRESETS.map(p => `<button type="button" class="trl-preset" data-p="${p}">${p.replace("+", " + ")}</button>`).join("")}
      <button type="button" data-p="">Ρεπό</button>
      <span class="trl-sep" aria-hidden="true"></span>
      <button type="button" data-a="tele" aria-pressed="${g && g.t ? "true" : "false"}" ${g ? "" : "disabled"}>Τηλεεργασία${segs.length === 2 ? ` (${i + 1}η βάρδια)` : ""}</button>
      <button type="button" data-a="del" ${g ? "" : "disabled"}>Διαγραφή βάρδιας</button>
      ${G.week ? `<button type="button" data-a="copy" ${row.err ? "disabled" : ""}>Αντιγραφή σε Δευτέρα–Παρασκευή</button>` : ""}`;
    G.tools.querySelectorAll("button").forEach(b => {
      if (locked) b.disabled = true;
      b.addEventListener("click", () => {
        if (b.dataset.p !== undefined) { setSegs(G, row, parse(b.dataset.p).segs); G.sel = 0; }
        else if (b.dataset.a === "tele") { const s = segs.map(x => ({...x})); s[i].t = !s[i].t; setSegs(G, row, s); }
        else if (b.dataset.a === "del") { const s = segs.map(x => ({...x})); s.splice(i, 1); G.sel = 0; setSegs(G, row, s); }
        else if (b.dataset.a === "copy") {
          G.rows.slice(0, WEEKDAYS).forEach(r => { if (r !== row) setSegs(G, r, row.segs.map(x => ({...x}))); });
        }
        render(G);
      });
    });
  }

  /* ------------------------------------------------------------ σάρωση */
  function scan() {
    document.querySelectorAll("[data-time-rail-week]:not([data-trl-done])").forEach(host => {
      host.dataset.trlDone = "1";
      const inputs = [...host.querySelectorAll("input[data-time-rail]")];
      inputs.forEach(i => { i.dataset.trlDone = "1"; });
      const shell = [...host.children];  // οι παλιές γραμμές/κάρτες ημερών
      build(host, inputs, true);
      shell.forEach(el => el.remove());
    });
    document.querySelectorAll("input[data-time-rail]:not([data-trl-done])").forEach(input => {
      input.dataset.trlDone = "1";
      const anchor = input.closest("label") || input;
      const host = document.createElement("div");
      host.className = "trl" + (anchor.classList.contains("wide") ? " wide" : "");
      anchor.after(host);
      build(host, [input], false);
      // Το πεδίο μένει μέσα στο <label> του — η ράγα ακολουθεί από κάτω
    });
  }

  document.addEventListener("DOMContentLoaded", scan);
  // Τα modals έρχονται με htmx — όπως στο date-gr.js, σάρωση ΟΛΟΥ του document
  document.addEventListener("htmx:load", scan);
  if (document.readyState !== "loading") scan();
})();

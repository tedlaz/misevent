/* ΑΝΑΖΗΤΗΣΗ ΜΕ ΠΛΗΚΤΡΟΛΟΓΗΣΗ ΣΤΑ ΜΕΓΑΛΑ <select> (ΣΕΠΕ, ΟΑΕΔ, ΚΑΔ, Καλλικράτης…)
   ΚΑΙ ΣΤΑ ΠΕΔΙΑ <input list> (ΚΑΔ/ειδικότητες Ο.Σ.Υ.Κ. της ειδικότητας)
   -------------------------------------------------------------------------
   Progressive enhancement: κάθε select με > MIN επιλογές αντικαθίσταται
   οπτικά από ΕΝΑ πεδίο κειμένου με δική μας λίστα προτάσεων. Κάθε λέξη πρέπει
   να περιέχεται στη γραμμή (κωδικός + περιγραφή), ΧΩΡΙΣ τόνους/πεζά-κεφαλαία
   — γι' αυτό όχι native <datalist>, που κάνει διάκριση τόνων και δεν
   στυλάρεται (στενό, λίγες γραμμές).
   ⚠️ ΤΟ SELECT ΠΑΡΑΜΕΝΕΙ Η ΠΗΓΗ ΑΛΗΘΕΙΑΣ (κρυμμένο, όπως στο date-gr.js):
   κρατά name/value, άρα υποβάλλεται ό,τι και πριν. Το πεδίο δεν έχει name.
   Η επιλογή αλλάζει το select (+ `change`, ώστε όσοι το ακούν να
   αντιδρούν)· άκυρο κείμενο επανέρχεται στην επιλογή μόλις φύγει η εστίαση.
   ⚠️ Το `required` ΜΕΤΑΦΕΡΕΤΑΙ στο ορατό πεδίο: ένα κρυμμένο invalid control
   μπλοκάρει σιωπηλά την υποβολή.
   ➜ <input list>: το ΙΔΙΟ το input μένει (με το name του, ελεύθερη
   πληκτρολόγηση)· βγαίνει μόνο το `list` ώστε να μη βγαίνει και η native
   λίστα. Το datalist διαβάζεται σε κάθε άνοιγμα (το htmx μπορεί να το αλλάξει). */
(() => {
  const MIN = 20;
  const MAX_SHOWN = 100;  // ο Καλλικράτης έχει χιλιάδες γραμμές
  const norm = s => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase();
  const optsOf = options => [...options].filter(o => o.value)
    .map(o => ({ value: o.value, text: o.text || o.value, key: norm(o.text + " " + o.value) }));

  /* ΠΑΝΤΑ ΑΠΟ ΠΑΝΩ: η ανοιχτή λίστα μπαίνει στο TOP LAYER του browser (native
     Popover API), οπότε ΚΑΝΕΝΑ overflow/z-index προγόνου (modal, .mbody,
     .fsec) δεν την κόβει. Τοποθετείται fixed κάτω από το πεδίο — ή από πάνω,
     όταν από κάτω χωρά λιγότερο — και ακολουθεί κάθε scroll/resize.
     Browser χωρίς popover: μένει η παλιά absolute λίστα. */
  const canPop = typeof HTMLElement.prototype.showPopover === "function";
  const floating = new Map();  // λίστα -> πεδίο αγκύρωσης
  const place = (el, anchor) => {
    if (!anchor.isConnected || !el.isConnected) return unfloat(el);
    const r = anchor.getBoundingClientRect();
    const vw = innerWidth, vh = innerHeight, gap = 2, edge = 8;
    const width = Math.min(Math.max(r.width, 560), vw - 2 * edge);
    const below = vh - r.bottom - gap - edge, above = r.top - gap - edge;
    const up = below < 240 && above > below;
    el.style.width = width + "px";
    el.style.left = Math.max(edge, Math.min(r.left, vw - width - edge)) + "px";
    el.style.maxHeight = Math.min(420, up ? above : below) + "px";
    el.style.top = (up ? r.top - gap - el.offsetHeight : r.bottom + gap) + "px";
  };
  const float = (el, anchor) => {
    if (!canPop) return;
    el.popover = "manual";
    if (!el.matches(":popover-open")) el.showPopover();
    floating.set(el, anchor);
    place(el, anchor);
  };
  function unfloat(el) {
    floating.delete(el);
    if (canPop && el.matches(":popover-open")) el.hidePopover();
  }
  const replace = () => floating.forEach((anchor, el) => place(el, anchor));
  addEventListener("scroll", replace, true);  // capture: και το scroll του .mbody
  addEventListener("resize", replace);

  // Η κοινή λίστα προτάσεων κάτω από το `box`. `pick(value)` επιλέγει,
  // `revert()` καλείται σε blur/Escape.
  function attach(box, getOpts, pick, revert) {
    const wrap = document.createElement("span");
    wrap.className = "ss";
    const menu = document.createElement("ul");
    menu.className = "ss-menu";
    menu.hidden = true;
    box.before(wrap);
    wrap.append(box, menu);

    let shown = [], active = -1;
    const close = () => { menu.hidden = true; active = -1; unfloat(menu); };
    const choose = value => { pick(value); close(); };
    const mark = i => {
      menu.children[active]?.classList.remove("on");
      active = i;
      const li = menu.children[i];
      if (li) { li.classList.add("on"); li.scrollIntoView({ block: "nearest" }); }
    };
    const render = q => {
      const words = norm(q).split(/\s+/).filter(Boolean);
      const hits = getOpts().filter(o => words.every(w => o.key.includes(w)));
      shown = hits.slice(0, MAX_SHOWN);
      menu.replaceChildren(...shown.map(o => {
        const li = document.createElement("li");
        li.textContent = o.text;
        return li;
      }));
      if (hits.length > MAX_SHOWN) {
        const li = document.createElement("li");
        li.className = "ss-more";
        li.textContent = `… και ${hits.length - MAX_SHOWN} ακόμη — συνεχίστε να πληκτρολογείτε`;
        menu.append(li);
      }
      menu.hidden = !shown.length;
      if (menu.hidden) unfloat(menu); else float(menu, box);
      active = -1;
      if (words.length) mark(0);
    };

    box.addEventListener("input", () => render(box.value));
    box.addEventListener("focus", () => { box.select(); render(""); });
    box.addEventListener("blur", () => { revert(); close(); });
    box.addEventListener("keydown", e => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (menu.hidden) render("");
        const d = e.key === "ArrowDown" ? 1 : -1;
        mark(Math.max(0, Math.min(shown.length - 1, active + d)));
      } else if (e.key === "Enter" && !menu.hidden && shown[active]) {
        e.preventDefault();
        choose(shown[active].value);
      } else if (e.key === "Escape" && !menu.hidden) {
        e.preventDefault();
        e.stopPropagation();  // να μην κλείσει και το modal
        revert();
        close();
      }
    });
    // mousedown (όχι click): προλαβαίνει το blur που θα έκλεινε τη λίστα.
    menu.addEventListener("mousedown", e => {
      e.preventDefault();
      const i = [...menu.children].indexOf(e.target.closest("li"));
      if (shown[i]) choose(shown[i].value);
    });
  }

  function enhance(sel) {
    if (sel.dataset.ssDone || sel.multiple || sel.options.length <= MIN) return;
    sel.dataset.ssDone = "1";
    const opts = optsOf(sel.options);
    const box = document.createElement("input");
    box.type = "text";
    box.autocomplete = "off";
    box.placeholder = [...sel.options].find(o => !o.value)?.text
      || "Πληκτρολογήστε κωδικό ή λέξη…";
    if (sel.required) { box.required = true; sel.required = false; }
    box.disabled = sel.disabled;
    sel.hidden = true;
    sel.before(box);
    const show = () => { box.value = sel.value ? sel.selectedOptions[0].text : ""; };
    attach(box, () => opts, value => {
      if (sel.value !== value) {
        sel.value = value;
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      }
      show();
    }, show);
    box.addEventListener("input", () => {
      if (!box.value && sel.value) {
        sel.value = "";
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
    show();
    sel.addEventListener("change", show);
    sel.form?.addEventListener("reset", () => setTimeout(show));
  }

  function enhanceList(inp) {
    if (inp.dataset.ssDone) return;
    inp.dataset.ssDone = "1";
    const listId = inp.getAttribute("list");
    inp.removeAttribute("list");
    const getOpts = () => optsOf(document.getElementById(listId)?.options || []);
    attach(inp, getOpts, value => {
      if (inp.value !== value) {
        inp.value = value;
        inp.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }, () => {});
  }

  const scan = root => {
    root.querySelectorAll("select").forEach(enhance);
    root.querySelectorAll("input[list]").forEach(enhanceList);
  };
  document.addEventListener("DOMContentLoaded", () => scan(document));
  document.addEventListener("htmx:load", () => scan(document));

  /* Τα αποτελέσματα ΕΡΓΑΝΗ (htmx, `_ergani_specialty_results.html`) — ίδια
     συμπεριφορά: ανοιχτά όσο έχουν περιεχόμενο ΚΑΙ η εστίαση είναι στα πεδία
     τους ή μέσα τους. Η καθυστέρηση στο focusout αφήνει το κλικ σε αποτέλεσμα
     να φτάσει (σε Safari τα κουμπιά δεν παίρνουν εστίαση). */
  const ERG = ".ergani-specialty-results";
  const ownsFocus = el => {
    const a = document.activeElement;
    return el.contains(a) || a?.getAttribute("hx-target") === "#" + el.id;
  };
  const syncErgani = () => document.querySelectorAll(ERG).forEach(el => {
    if (!canPop) return;
    el.popover = "manual";
    const anchor = el.closest("label")?.querySelector("input");
    if (el.children.length && anchor && ownsFocus(el)) float(el, anchor);
    else unfloat(el);
  });
  document.addEventListener("htmx:afterSettle", syncErgani);
  document.addEventListener("htmx:oobAfterSwap", syncErgani);
  document.addEventListener("focusout", () => setTimeout(syncErgani, 150));
})();

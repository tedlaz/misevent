/* ΑΝΑΖΗΤΗΣΗ ΜΕ ΠΛΗΚΤΡΟΛΟΓΗΣΗ ΣΤΑ ΜΕΓΑΛΑ <select> (ΣΕΠΕ, ΟΑΕΔ, ΚΑΔ, Καλλικράτης…)
   -------------------------------------------------------------------------
   Progressive enhancement: κάθε select με > MIN επιλογές αντικαθίσταται
   οπτικά από ΕΝΑ πεδίο κειμένου με δική μας λίστα προτάσεων. Κάθε λέξη πρέπει
   να περιέχεται στη γραμμή (κωδικός + περιγραφή), ΧΩΡΙΣ τόνους/πεζά-κεφαλαία
   — γι' αυτό όχι native <datalist>, που κάνει διάκριση τόνων.
   ⚠️ ΤΟ SELECT ΠΑΡΑΜΕΝΕΙ Η ΠΗΓΗ ΑΛΗΘΕΙΑΣ (κρυμμένο, όπως στο date-gr.js):
   κρατά name/value, άρα υποβάλλεται ό,τι και πριν. Το πεδίο δεν έχει name.
   Η επιλογή αλλάζει το select (+ `change`, ώστε όσοι το ακούν να
   αντιδρούν)· άκυρο κείμενο επανέρχεται στην επιλογή μόλις φύγει η εστίαση.
   ⚠️ Το `required` ΜΕΤΑΦΕΡΕΤΑΙ στο ορατό πεδίο: ένα κρυμμένο invalid control
   μπλοκάρει σιωπηλά την υποβολή. */
(() => {
  const MIN = 20;
  const MAX_SHOWN = 100;  // ο Καλλικράτης έχει χιλιάδες γραμμές
  const norm = s => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase();

  function enhance(sel) {
    if (sel.dataset.ssDone || sel.multiple || sel.options.length <= MIN) return;
    sel.dataset.ssDone = "1";
    const opts = [...sel.options].filter(o => o.value)
      .map(o => ({ value: o.value, text: o.text, key: norm(o.text) }));

    const wrap = document.createElement("span");
    wrap.className = "ss";
    const box = document.createElement("input");
    box.type = "text";
    box.autocomplete = "off";
    box.placeholder = [...sel.options].find(o => !o.value)?.text
      || "Πληκτρολογήστε κωδικό ή λέξη…";
    if (sel.required) { box.required = true; sel.required = false; }
    box.disabled = sel.disabled;
    const menu = document.createElement("ul");
    menu.className = "ss-menu";
    menu.hidden = true;
    wrap.append(box, menu);
    sel.hidden = true;
    sel.before(wrap);

    let shown = [], active = -1;
    const show = () => { box.value = sel.value ? sel.selectedOptions[0].text : ""; };
    const close = () => { menu.hidden = true; active = -1; };
    const pick = value => {
      if (sel.value !== value) {
        sel.value = value;
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      }
      show();
      close();
    };
    const mark = i => {
      menu.children[active]?.classList.remove("on");
      active = i;
      const li = menu.children[i];
      if (li) { li.classList.add("on"); li.scrollIntoView({ block: "nearest" }); }
    };
    const render = q => {
      const words = norm(q).split(/\s+/).filter(Boolean);
      const hits = opts.filter(o => words.every(w => o.key.includes(w)));
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
      active = -1;
      if (words.length) mark(0);
    };
    show();

    box.addEventListener("input", () => {
      if (!box.value) pick("");
      render(box.value);
    });
    box.addEventListener("focus", () => { box.select(); render(""); });
    box.addEventListener("blur", () => { show(); close(); });
    box.addEventListener("keydown", e => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (menu.hidden) render("");
        const d = e.key === "ArrowDown" ? 1 : -1;
        mark(Math.max(0, Math.min(shown.length - 1, active + d)));
      } else if (e.key === "Enter" && !menu.hidden && shown[active]) {
        e.preventDefault();
        pick(shown[active].value);
      } else if (e.key === "Escape" && !menu.hidden) {
        e.preventDefault();
        e.stopPropagation();  // να μην κλείσει και το modal
        show();
        close();
      }
    });
    // mousedown (όχι click): προλαβαίνει το blur που θα έκλεινε τη λίστα.
    menu.addEventListener("mousedown", e => {
      e.preventDefault();
      const i = [...menu.children].indexOf(e.target.closest("li"));
      if (shown[i]) pick(shown[i].value);
    });
    sel.addEventListener("change", show);
    sel.form?.addEventListener("reset", () => setTimeout(show));
  }

  const scan = root => root.querySelectorAll("select").forEach(enhance);
  document.addEventListener("DOMContentLoaded", () => scan(document));
  document.addEventListener("htmx:load", () => scan(document));
})();

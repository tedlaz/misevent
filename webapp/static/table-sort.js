/* Ταξινόμηση & φίλτρα πίνακα.
 *
 * ΤΑΞΙΝΟΜΗΣΗ — `<table data-sortable>`: κλικ στην κεφαλίδα. Κλειδί κελιού:
 * `data-sort` αν υπάρχει, αλλιώς το κείμενο. Αριθμοί συγκρίνονται ως
 * αριθμοί, τα υπόλοιπα με ελληνική σειρά (localeCompare). Δεύτερο κλικ στην
 * ίδια στήλη = αντίστροφη σειρά (`aria-sort` → βέλος CSS).
 * `<th data-nosort>` = στήλη χωρίς ταξινόμηση (π.χ. ενέργειες).
 *
 * ΦΙΛΤΡΑ — `<table data-filterable>`: γραμμή με ένα πεδίο ανά στήλη κάτω
 * από τις κεφαλίδες· γραμμή φαίνεται αν ΚΑΘΕ συμπληρωμένο πεδίο περιέχεται
 * στο ΚΕΙΜΕΝΟ του κελιού (χωρίς τόνους, πεζά/κεφαλαία αδιάφορα).
 * `<th data-nofilter>` = στήλη χωρίς φίλτρο.
 *
 * Πίνακας που ξαναφορτώνεται με htmx (π.χ. μετά από υποβολή) ξαναπαίρνει
 * την τελευταία ταξινόμηση ΚΑΙ τα φίλτρα του — κλειδί: το id του
 * πλησιέστερου γονέα με id (ζει μόνο όσο η σελίδα είναι ανοιχτή).
 */
(() => {
  const last = {}, filters = {};
  const key = (row, i) => {
    const td = row.cells[i];
    return td ? (td.dataset.sort ?? td.textContent.trim()) : "";
  };
  const cmp = (a, b) => {
    const x = Number(a), y = Number(b);
    if (a !== "" && b !== "" && !isNaN(x) && !isNaN(y)) return x - y;
    return a.localeCompare(b, "el", { sensitivity: "base" });
  };
  const norm = (s) =>
    s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
  const slot = (table) => table.parentElement?.closest("[id]")?.id || "";
  const heads = (table) => [...table.tHead.rows[0].cells];

  function sort(table, i, asc) {
    const ths = heads(table);
    ths.forEach((h) => h.removeAttribute("aria-sort"));
    ths[i].setAttribute("aria-sort", asc ? "ascending" : "descending");
    // Πολλά <tbody> (δέντρο: ρίζα + παιδιά) -> κινούνται ΟΛΟΚΛΗΡΑ, με κλειδί
    // την πρώτη τους γραμμή· αλλιώς ταξινομούνται οι γραμμές του ενός.
    const many = table.tBodies.length > 1;
    const parent = many ? table : table.tBodies[0];
    const items = many ? [...table.tBodies] : [...parent.rows];
    const head = (x) => (many ? x.rows[0] : x);
    items
      .sort((a, b) => (asc ? 1 : -1) * cmp(key(head(a), i), key(head(b), i)))
      .forEach((x) => parent.appendChild(x));
    last[slot(table)] = { i, asc };
  }

  function filter(table) {
    const inputs = [...table.tHead.querySelectorAll("input[data-col]")];
    const want = inputs.map((inp) => [+inp.dataset.col, norm(inp.value)])
      .filter(([, v]) => v);
    filters[slot(table)] = Object.fromEntries(
      inputs.map((inp) => [inp.dataset.col, inp.value]));
    [...table.tBodies[0].rows].forEach((r) => {
      r.hidden = !want.every(([i, v]) => norm(r.cells[i]?.textContent || "").includes(v));
    });
  }

  function filterRow(table) {
    const tr = document.createElement("tr");
    tr.className = "tbl-filter";
    const saved = filters[slot(table)] || {};
    heads(table).forEach((th, i) => {
      const cell = document.createElement("th");
      if (!th.hasAttribute("data-nofilter")) {
        const inp = document.createElement("input");
        inp.type = "search";
        inp.dataset.col = i;
        inp.placeholder = "φίλτρο…";
        inp.setAttribute("aria-label", "Φίλτρο: " + th.textContent.trim());
        inp.value = saved[i] || "";
        inp.addEventListener("input", () => filter(table));
        cell.appendChild(inp);
      }
      tr.appendChild(cell);
    });
    table.tHead.appendChild(tr);
    filter(table);
  }

  function bind(table) {
    if (table.dataset.sortBound || !table.tHead) return;
    table.dataset.sortBound = "1";
    if (table.hasAttribute("data-sortable")) {
      heads(table).forEach((th, i) => {
        if (th.hasAttribute("data-nosort")) return;
        th.addEventListener("click", () =>
          sort(table, i, th.getAttribute("aria-sort") !== "ascending"));
      });
      const prev = last[slot(table)];
      if (prev) sort(table, prev.i, prev.asc);
    }
    if (table.hasAttribute("data-filterable")) filterRow(table);
  }

  const init = () => document
    .querySelectorAll("table[data-sortable], table[data-filterable]").forEach(bind);
  document.addEventListener("htmx:afterSettle", init);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

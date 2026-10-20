/* Φίλτρα της οθόνης Εργαζόμενοι — κρύβει γραμμές του #persons-table.
 *
 * Ο server βάζει σε κάθε <tr> data-status / data-specialty / data-search
 * (ήδη κανονικοποιημένο: κεφαλαία χωρίς τόνους + ΑΦΜ/ΑΜΚΑ/ΑΜΑ). Ξαναεφαρμόζεται
 * μετά από κάθε swap του htmx (καταχώρηση, εισαγωγή κ.λπ.).
 */
(() => {
  const norm = (s) =>
    s.normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase().trim();

  function apply() {
    const form = document.getElementById("persons-filter");
    const table = document.getElementById("persons-table");
    if (!form || !table) return;
    const q = norm(form.querySelector("[name=q]").value);
    const status = form.querySelector("[name=status]").value;
    const spec = form.querySelector("[name=specialty]").value;
    const rows = table.querySelectorAll("tbody tr");
    let shown = 0;
    for (const tr of rows) {
      const ok =
        (!status || tr.dataset.status === status) &&
        (!spec || tr.dataset.specialty === spec) &&
        q.split(/\s+/).every((w) => tr.dataset.search.includes(w));
      tr.hidden = !ok;
      if (ok) shown++;
    }
    const none = document.getElementById("persons-nomatch");
    if (none) none.hidden = shown > 0 || !rows.length;
    document.getElementById("persons-count").textContent =
      rows.length ? `${shown} / ${rows.length}` : "";
  }

  document.addEventListener("input", (e) => {
    if (e.target.closest("#persons-filter")) apply();
  });
  document.addEventListener("reset", (e) => {
    if (e.target.id === "persons-filter") setTimeout(apply);
  });
  document.addEventListener("htmx:afterSettle", apply);
  document.addEventListener("DOMContentLoaded", apply);
})();

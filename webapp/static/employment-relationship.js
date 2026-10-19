(() => {
  function syncSection(section) {
    if (!section) return;
    const select = section.querySelector("[data-employment-relationship]");
    const fields = section.querySelector("[data-fixed-term-fields]");
    const hireDate = section.querySelector("[data-hire-date]");
    const fixedFrom = section.querySelector("[data-fixed-term-from]");
    if (!select || !fields) return;

    const fixedTerm = select.value === "1";
    if (fixedTerm && hireDate && fixedFrom) fixedFrom.value = hireDate.value;
    fields.hidden = !fixedTerm;
    fields.querySelectorAll("input").forEach((input) => {
      input.disabled = !fixedTerm;
      input.required = fixedTerm;
    });
    if (fixedFrom) fixedFrom.readOnly = true;
  }

  function syncAll(root) {
    if (root instanceof Element && root.matches(
      "[data-employment-relationship-section]"
    )) syncSection(root);
    root.querySelectorAll?.("[data-employment-relationship-section]")
      .forEach(syncSection);
  }

  document.addEventListener("DOMContentLoaded", () => syncAll(document));
  document.addEventListener("change", (event) => {
    if (event.target.matches("[data-employment-relationship]")) {
      syncSection(event.target.closest("[data-employment-relationship-section]"));
    }
  });
  document.addEventListener("input", (event) => {
    if (event.target.matches("[data-hire-date]")) {
      syncSection(event.target.closest("[data-employment-relationship-section]"));
    }
  });
  document.addEventListener("htmx:afterSwap", (event) => syncAll(event.target));
})();

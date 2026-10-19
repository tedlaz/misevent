(() => {
  function syncForm(form) {
    if (!form) return;
    const nationality = form.querySelector("[data-nationality-select]");
    const radio = form.querySelector("[data-third-country-radio]");
    const label = form.querySelector("[data-third-country-label]");
    if (!nationality || !radio || !label) return;

    const euCodes = new Set(
      (form.dataset.euNationalities || "").split(",").filter(Boolean)
    );
    const enabled = Boolean(nationality.value) && !euCodes.has(nationality.value);
    radio.disabled = !enabled;
    label.classList.toggle("is-disabled", !enabled);
    label.setAttribute("aria-disabled", String(!enabled));
    form.querySelectorAll(".tp-foreign input, .tp-foreign select, .tp-foreign textarea")
      .forEach((control) => { control.disabled = !enabled; });

    if (!enabled && radio.checked) {
      const general = form.querySelector("#tab-general");
      if (general) general.checked = true;
    }
  }

  function syncAll(root) {
    if (root instanceof Element && root.matches("form[data-eu-nationalities]")) {
      syncForm(root);
    }
    root.querySelectorAll?.("form[data-eu-nationalities]").forEach(syncForm);
  }

  document.addEventListener("DOMContentLoaded", () => syncAll(document));
  document.addEventListener("change", (event) => {
    if (event.target.matches("[data-nationality-select]")) {
      syncForm(event.target.closest("form"));
    }
  });
  document.addEventListener("htmx:afterSwap", (event) => syncAll(event.target));
})();

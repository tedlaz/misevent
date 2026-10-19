(() => {
  function sync(tab) {
    if (!tab) return;
    const placement = tab.querySelector("[data-oaed-placement]");
    const programFields = tab.querySelector("[data-oaed-program-fields]");
    const replacement = tab.querySelector("[data-oaed-replacement]");
    const replacementFields = tab.querySelector("[data-oaed-replacement-fields]");
    const trial = tab.querySelector("[data-trial-period]");
    const trialDateField = tab.querySelector("[data-trial-date-field]");

    const hasProgram = placement?.value === "1";
    programFields.hidden = !hasProgram;
    programFields.querySelectorAll("input,select").forEach(control => {
      control.disabled = !hasProgram;
    });
    const program = programFields.querySelector("[name=f_programaoaed]");
    if (program) program.required = hasProgram;
    if (replacement) replacement.required = hasProgram;

    const replaces = hasProgram && replacement?.value === "1";
    replacementFields.hidden = !replaces;
    replacementFields.querySelectorAll("input").forEach(input => {
      input.disabled = !replaces;
      input.required = false;
    });

    const hasTrial = trial?.value === "1";
    trialDateField.hidden = !hasTrial;
    const trialDate = trialDateField.querySelector("input");
    trialDate.disabled = !hasTrial;
    trialDate.required = hasTrial;
  }

  function scan(root) {
    if (root instanceof Element && root.matches("[data-oaed-tab]")) sync(root);
    root.querySelectorAll?.("[data-oaed-tab]").forEach(sync);
  }

  document.addEventListener("DOMContentLoaded", () => scan(document));
  document.addEventListener("htmx:afterSwap", event => scan(event.target));
  document.addEventListener("change", event => {
    if (event.target.matches("[data-oaed-placement],[data-oaed-replacement],[data-trial-period]")) {
      sync(event.target.closest("[data-oaed-tab]"));
    }
  });
})();

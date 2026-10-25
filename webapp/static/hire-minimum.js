(() => {
  const timers = new WeakMap();
  const requests = new WeakMap();
  // Κάθε πεδίο που ΑΛΛΑΖΕΙ το κατώτατο όριο. Λείπουν πεδία -> η ένδειξη
  // μένει στάσιμη με ΠΑΛΙΟ αποτέλεσμα (ή παλιό σφάλμα) και δεν καθαρίζει.
  // «migrated»/«payroll_from»: στη μεταφορά το όριο κρίνεται στην ΑΝΑΛΗΨΗ,
  // με τις τριετίες ωριμασμένες (svc.wage_check_basis).
  const relevant = new Set([
    "hire_date", "pay_type", "amount", "weekly_hours", "service_years",
    "service_months", "frozen_service_years", "frozen_service_months",
    "married", "extra_allowances_pct",
    "migrated", "payroll_from"
  ]);
  const freezeStart = "2012-02-14";

  function syncFrozenFields(form) {
    if (!form) return;
    const hireDate = form.querySelector('[name="hire_date"]')?.value || "";
    const visible = !!hireDate && hireDate >= freezeStart;
    form.querySelectorAll("[data-frozen-service-field]").forEach(field => {
      field.hidden = !visible;
    });
  }

  // Βιβλιάριο ανηλίκου: ΜΟΝΟ για ανήλικο την ημ. πρόσληψης. Ημ. γέννησης
  // από το πεδίο της φόρμας (νέο πρόσωπο) ή από το data-birth-date (υπάρχον).
  function syncMinorFields(form) {
    const box = form?.querySelector("[data-hire-status]");
    if (!box) return;
    const born = form.querySelector('[name="birth_date"]')?.value || box.dataset.birthDate;
    const hired = form.querySelector('[name="hire_date"]')?.value;
    let minor = false;
    if (born && hired) {
      const [by, bm, bd] = born.split("-").map(Number);
      const [hy, hm, hd] = hired.split("-").map(Number);
      minor = hy - by - ((hm < bm || (hm === bm && hd < bd)) ? 1 : 0) < 18;
    }
    box.querySelectorAll("[data-minor-field]").forEach(f => { f.hidden = !minor; });
  }

  const money = value => Number(value).toLocaleString("el-GR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });

  async function refresh(section) {
    const form = section.closest("form");
    const amount = section.querySelector("[data-compensation-amount]");
    const note = section.querySelector("[data-minimum-compensation-note]");
    if (!form || !amount || !note || !section.dataset.minimumUrl) return;

    try {
      requests.get(section)?.abort();
      const controller = new AbortController();
      requests.set(section, controller);
      const response = await fetch(section.dataset.minimumUrl, {
        method: "POST",
        body: new FormData(form),
        headers: {"X-Requested-With": "XMLHttpRequest"},
        signal: controller.signal
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Αποτυχία υπολογισμού");

      const previousText = amount.value.trim();
      const previous = Number.parseFloat(previousText);
      const adjusted = !Number.isFinite(previous) || previous + 0.005 < result.floor;
      if (adjusted) {
        amount.value = Number(result.floor).toFixed(2);
        // Ενημερώνει και το live WebE3N JSON με το αυτόματα διορθωμένο ποσό.
        section.dataset.minimumSyncing = "1";
        amount.dispatchEvent(new Event("input", {bubbles: true}));
        delete section.dataset.minimumSyncing;
      }

      note.classList.remove("error");
      const details = result.description ? ` (${result.description})` : "";
      if (!previousText) {
        note.textContent = `Προτεινόμενες ελάχιστες αποδοχές: ` +
          `${money(result.floor)} ${result.unit}${details}.`;
        note.classList.add("adjusted");
      } else if (adjusted) {
        note.textContent = `Οι αποδοχές ήταν χαμηλότερες από το νόμιμο όριο ` +
          `και διορθώθηκαν αυτόματα σε ${money(result.floor)} ` +
          `${result.unit}${details}.`;
        note.classList.add("adjusted");
      } else {
        note.textContent = `Ελάχιστες επιτρεπόμενες αποδοχές: ` +
          `${money(result.floor)} ${result.unit}${details}.`;
        note.classList.remove("adjusted");
      }
    } catch (error) {
      if (error.name === "AbortError") return;
      note.textContent = error.message;
      note.classList.add("error");
    }
  }

  function schedule(section, delay = 350) {
    clearTimeout(timers.get(section));
    timers.set(section, setTimeout(() => refresh(section), delay));
  }

  function scan(root) {
    if (root instanceof Element && root.matches("[data-minimum-compensation]")) {
      schedule(root, 0);
    }
    root.querySelectorAll?.("[data-minimum-compensation]")
      .forEach(section => schedule(section, 0));
    const form = root instanceof HTMLFormElement ? root : root.querySelector?.("form");
    if (form) { syncFrozenFields(form); syncMinorFields(form); }
  }

  function sectionFor(control) {
    return control.closest("[data-minimum-compensation]") ||
      control.closest("form")?.querySelector("[data-minimum-compensation]");
  }

  document.addEventListener("DOMContentLoaded", () => scan(document));
  document.addEventListener("htmx:afterSwap", event => scan(event.target));
  document.addEventListener("input", event => {
    if (event.target.name === "hire_date") {
      syncFrozenFields(event.target.form);
    }
    if (event.target.name === "hire_date" || event.target.name === "birth_date") {
      syncMinorFields(event.target.form);
    }
    if (!relevant.has(event.target.name)) return;
    const section = sectionFor(event.target);
    if (section && !section.dataset.minimumSyncing) schedule(section);
  });
  document.addEventListener("change", event => {
    if (event.target.name === "hire_date") {
      syncFrozenFields(event.target.form);
    }
    if (event.target.name === "hire_date" || event.target.name === "birth_date") {
      syncMinorFields(event.target.form);
    }
    if (!relevant.has(event.target.name)) return;
    const section = sectionFor(event.target);
    if (section) schedule(section, 80);
  });
})();

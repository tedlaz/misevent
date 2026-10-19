/* ΕΛΛΗΝΙΚΗ ΜΟΡΦΗ ΣΤΑ ΠΕΔΙΑ ΗΜΕΡΟΜΗΝΙΑΣ (ΗΗ/ΜΜ/ΕΕΕΕ)
   -------------------------------------------------------------------------
   Το native <input type="date"> μορφοποιείται από τη ΓΛΩΣΣΑ ΤΟΥ BROWSER, όχι
   από το lang="el" της σελίδας: σε αγγλικό Chrome εμφανίζεται mm/dd/yyyy. Το
   αρχείο αυτό είναι progressive enhancement: μπροστά από κάθε πεδίο μπαίνει
   ΔΙΚΟ ΜΑΣ πεδίο κειμένου με μάσκα ΗΗ/ΜΜ/ΕΕΕΕ + κουμπί ημερολογίου.

   ⚠️ ΤΟ NATIVE INPUT ΠΑΡΑΜΕΝΕΙ Η ΠΗΓΗ ΑΛΗΘΕΙΑΣ: κρατά name/value σε ISO, άρα
   υποβάλλεται ό,τι και πριν και ΚΑΜΙΑ φόρμα/έλεγχος δεν αλλάζει. Απλώς
   κρύβεται οπτικά (μένει rendered, ώστε να δουλεύει το showPicker()).
   ⚠️ Το `required` ΜΕΤΑΦΕΡΕΤΑΙ στο ορατό πεδίο: ένα κρυμμένο invalid control
   μπλοκάρει σιωπηλά την υποβολή (ο browser δεν μπορεί να το εστιάσει).
   Τα min/max μένουν στο native (τα τιμά ο picker) και ελέγχονται ΚΑΙ εδώ:
   εκτός ορίων -> το native αδειάζει και το μήνυμα βγαίνει στο ορατό πεδίο. */
(() => {
  const PAT = /^(\d{2})\/(\d{2})\/(\d{4})$/;
  const pad = n => String(n).padStart(2, "0");

  const toGr = value => {
    const [y, m, d] = (value || "").split("-");
    return y ? `${d}/${m}/${y}` : "";
  };

  /** "14/07/2026" -> "2026-07-14" (κενό αν δεν είναι ΥΠΑΡΚΤΗ ημερομηνία). */
  const toIso = text => {
    const m = PAT.exec((text || "").trim());
    if (!m) return "";
    const [, dd, mm, yyyy] = m.map(Number);
    const d = new Date(Date.UTC(yyyy, mm - 1, dd));
    if (d.getUTCFullYear() !== yyyy || d.getUTCMonth() !== mm - 1
        || d.getUTCDate() !== dd) return "";
    return `${yyyy}-${pad(mm)}-${pad(dd)}`;
  };

  /** Μάσκα καθώς πληκτρολογεί ο χρήστης: 14072026 -> 14/07/2026. */
  const mask = raw => {
    const digits = (raw || "").replace(/\D/g, "").slice(0, 8);
    const parts = [digits.slice(0, 2), digits.slice(2, 4), digits.slice(4, 8)];
    return parts.filter(p => p !== "").join("/");
  };

  function enhance(native) {
    if (native.dataset.grDone) return;
    native.dataset.grDone = "1";

    const wrap = document.createElement("span");
    wrap.className = "dgr";
    const text = document.createElement("input");
    text.type = "text";
    text.className = "dgr-text";
    text.inputMode = "numeric";
    text.maxLength = 10;
    text.size = 10;  // εγγενές πλάτος όπου δεν υπάρχει κανόνας width (header)
    text.placeholder = "ΗΗ/ΜΜ/ΕΕΕΕ";
    text.autocomplete = "off";
    text.value = toGr(native.value);
    if (native.required) { text.required = true; native.required = false; }
    if (native.disabled) text.disabled = true;
    if (native.readOnly) text.readOnly = true;
    if (native.title) text.title = native.title;
    if (native.id) text.setAttribute("aria-labelledby", native.id);

    const cal = document.createElement("button");
    cal.type = "button";  // ΠΟΤΕ submit μέσα σε φόρμα
    cal.className = "dgr-cal";
    cal.tabIndex = -1;
    cal.title = "Άνοιγμα ημερολογίου";
    cal.textContent = "📅";

    native.parentNode.insertBefore(wrap, native);
    wrap.append(text, cal, native);
    native.classList.add("dgr-native");
    native.tabIndex = -1;  // εκτός σειράς πλοήγησης: το ορατό πεδίο οδηγεί

    let syncing = false;

    const validate = () => {
      const raw = text.value.trim();
      if (!raw) {
        text.setCustomValidity(
          text.required ? "Συμπλήρωσε ημερομηνία (ΗΗ/ΜΜ/ΕΕΕΕ)" : ""
        );
        return "";
      }
      const iso = toIso(raw);
      if (!iso) {
        text.setCustomValidity("Δώσε ημερομηνία στη μορφή ΗΗ/ΜΜ/ΕΕΕΕ");
        return "";
      }
      const min = native.min, max = native.max;
      if ((min && iso < min) || (max && iso > max)) {
        const from = min ? toGr(min) : "—", to = max ? toGr(max) : "—";
        text.setCustomValidity(`Επιτρεπτό διάστημα: ${from} έως ${to}`);
        return "";
      }
      text.setCustomValidity("");
      return iso;
    };

    // ορατό πεδίο -> native (+ τα ΙΔΙΑ events, ώστε να τρέχουν htmx/hire-minimum)
    const push = () => {
      const iso = validate();
      if (native.value === iso) return;
      syncing = true;
      native.value = iso;
      native.dispatchEvent(new Event("input", {bubbles: true}));
      native.dispatchEvent(new Event("change", {bubbles: true}));
      syncing = false;
    };

    text.addEventListener("input", () => {
      const atEnd = text.selectionStart === text.value.length;
      const next = mask(text.value);
      if (next !== text.value) {
        text.value = next;
        if (atEnd) text.setSelectionRange(next.length, next.length);
      }
      push();
    });
    text.addEventListener("change", push);
    text.addEventListener("blur", push);

    // native -> ορατό πεδίο (επιλογή από το ημερολόγιο ή προγραμματιστική τιμή)
    native.addEventListener("change", () => {
      if (syncing) return;
      text.value = toGr(native.value);
      validate();
    });

    cal.addEventListener("click", () => {
      try {
        native.showPicker();
      } catch (err) {
        native.focus();
      }
    });

    validate();
  }

  const scan = root => {
    const target = root && root.querySelectorAll ? root : document;
    target.querySelectorAll('input[type="date"]:not([data-gr-done])')
      .forEach(enhance);
  };

  document.addEventListener("DOMContentLoaded", () => scan(document));
  // Κάθε htmx swap (και OOB) φέρνει νέα πεδία — σαρώνουμε ΟΛΟ το document,
  // γιατί τα OOB fragments προσαρτώνται αλλού από το target του event.
  document.addEventListener("htmx:load", () => scan(document));
  if (document.readyState !== "loading") scan(document);
})();

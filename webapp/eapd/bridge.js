/*
 * bridge.js — γέφυρα με την εφαρμογή μισθοδοσίας.
 *
 * Ο viewer παραμένει browser-only και ΑΥΤΟΝΟΜΟΣ: τίποτα εδώ δεν ενεργοποιείται
 * χωρίς query params, οπότε η σκέτη `index.html` δουλεύει όπως πάντα.
 *
 * ?src=<url>    κατεβάζει το αρχείο ΑΠΔ από τον server (route `/eapd/raw`) και
 *               το φορτώνει στη φόρμα, ακριβώς όπως το κουμπί «Διάβασε».
 * ?readonly=1   ΠΡΟΒΟΛΗ ΜΟΝΟ: το αρχείο έχει ήδη παραχθεί από τη μισθοδοσία
 *               και είναι παράγωγο των events — δεν συντάσσεται εδώ. Κρύβονται
 *               το toolbar, η ανάγνωση αρχείου και ΚΑΘΕ δυνατότητα μεταβολής
 *               (προσθήκη/αφαίρεση τμημάτων, lookup, πληκτρολόγηση).
 *
 * Φορτώνεται ΤΕΛΕΥΤΑΙΟ από το loader.js, ώστε οι global συναρτήσεις του
 * app.js (parseAPDText / fillFormFromParsed) να υπάρχουν ήδη.
 */
(async () => {
  // Η ένδειξη φόρτωσης του index.html φεύγει ΠΑΝΤΑ (και σε σφάλμα): το
  // bridge.js τρέχει τελευταίο, οπότε εδώ η εφαρμογή είναι έτοιμη.
  const bootDone = () => document.getElementById('apdBoot')?.remove();

  const params = new URLSearchParams(location.search);
  const src = params.get('src');
  const readonly = ['1', 'true', 'yes'].includes(
    String(params.get('readonly') || '').toLowerCase()
  );

  // ------------------------------------------------------------- read-only
  /** Κλειδώνει ΚΑΘΕ πεδίο της φόρμας. Τρέχει ξανά μετά από κάθε render. */
  function lockFields() {
    const rec = document.getElementById('records');
    if (rec) {
      rec.querySelectorAll('input, textarea').forEach(f => {
        f.readOnly = true;
        f.required = false;   // αλλιώς τα κενά πεδία βάφονται «λάθος»
      });
      // Τα <select> δεν έχουν readOnly — μόνο disabled τα κλειδώνει πραγματικά.
      rec.querySelectorAll('select').forEach(s => { s.disabled = true; });
    }
    const out = document.getElementById('outText');
    if (out) out.readOnly = true;
  }

  if (readonly) {
    document.body.classList.add('apd-readonly');
    // Κάθε νέο render της φόρμας πρέπει να ξανακλειδώνεται: το
    // fillFormFromParsed ξαναχτίζει το #records από το μηδέν.
    const origFill = (typeof fillFormFromParsed === 'function')
      ? fillFormFromParsed : window.fillFormFromParsed;
    window.fillFormFromParsed = function (records) {
      const r = origFill(records);
      lockFields();
      return r;
    };
    lockFields();
  }

  if (!src) {
    if (readonly) {
      const rec = document.getElementById('records');
      if (rec) rec.innerHTML =
        '<div class="apd-empty">Επίλεξε αρχείο ΑΠΔ από τη λίστα.</div>';
      document.getElementById('outCard')?.style.setProperty('display', 'none');
    }
    bootDone();
    return;
  }

  // ------------------------------------------------- φόρτωση από τον server
  const enc = params.get('enc') || 'windows-1253';
  const encSel = document.getElementById('parseEncoding');
  if (encSel) encSel.value = enc;

  try {
    // Το κατέβασμα το ξεκίνησε ΗΔΗ το loader.js (παράλληλα με τα scripts)·
    // fallback fetch για όποιον φορτώσει το bridge.js μόνο του.
    const buf = await (window.__apdSrcPromise || fetch(src).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.arrayBuffer();
    }));
    let txt = '';
    try { txt = new TextDecoder(enc).decode(buf); }
    catch { txt = new TextDecoder('utf-8').decode(buf); }

    // ίδια σημασιολογία με το parseFile(): κανένας επανυπολογισμός στο import
    importing = true;
    let parsed;
    try {
      parsed = parseAPDText(txt);   // αναγνωρίζει μόνο του ΕΦΚΑ/ΤΕΚΑ (fund.js)
      const out = document.getElementById('parsedJson');
      if (out) out.value = JSON.stringify(parsed, null, 2);
      fillFormFromParsed(parsed.records || []);
    } finally {
      importing = false;
    }

    const raw = document.getElementById('outText');
    if (raw) raw.value = txt;
  } catch (e) {
    alert(`Αποτυχία φόρτωσης αρχείου ΑΠΔ: ${e.message || e}`);
    console.error(e);
  } finally {
    bootDone();
  }
})();

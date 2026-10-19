/*
 * fund.js — υποστήριξη ΔΥΟ γραμμογραφήσεων: ΑΠΔ e-ΕΦΚΑ και ΑΠΔ ΤΕΚΑ.
 *
 * Οι δύο προδιαγραφές διαφέρουν ΜΟΝΟ στην εγγραφή τύπου 3
 * (StoixeiaAsfalisis): η ΤΕΚΑ είναι η ΕΦΚΑ **χωρίς** τα πεδία
 *   No 53 Γραμμάτιο Προείσπραξης Έμμισθων Δικηγόρων
 *   No 54 Φορέας Επικουρικής Ασφάλισης εκτός e-ΕΦΚΑ
 *   No 55 Φορέας Ασθένειας εκτός e-ΕΦΚΑ
 * άρα 162 bytes -> 148. Οι τύποι 1 / 2 / 4 / EOF είναι ίδιοι.
 *
 * Γι' αυτό η ΤΕΚΑ μορφή **ΠΑΡΑΓΕΤΑΙ** από την ΕΦΚΑ του `metadata.js` — μία
 * πηγή αλήθειας, καμία διπλοεγγραφή στηλών.
 *
 * Η εναλλαγή γίνεται μεταλλάσσοντας το ΙΔΙΟ `window.APD_METADATA` object που
 * κρατά ο `const meta` του app.js — έτσι δεν χρειάζεται reload ούτε αλλαγή
 * στο app.js.
 *
 * Φορτώνεται ΜΕΤΑ το app.js (βλ. loader.js).
 */
(() => {
  const DROP = ['53', '54', '55'];  // πεδία που ΔΕΝ υπάρχουν στην ΤΕΚΑ
  const LABELS = { efka: 'e-ΕΦΚΑ', teka: 'ΤΕΚΑ' };

  const efka = window.APD_METADATA.StoixeiaAsfalisis;
  const tekaCols = efka.columns.filter(c => !DROP.includes(String(c.no)));
  const teka = {
    ...efka,
    line_size: tekaCols.reduce((s, c) => s + Number(c.length), 0),  // 148
    columns: tekaCols,
  };
  const VARIANTS = { efka, teka };

  // Μήκος εγγραφής τύπου 3 -> ταμείο (για αυτόματη αναγνώριση κατά την ανάγνωση)
  const BY_LEN = {};
  for (const [f, v] of Object.entries(VARIANTS)) BY_LEN[Number(v.line_size)] = f;

  window.APD_FUND = 'efka';

  /** Θέτει την ενεργή γραμμογράφηση. Επιστρέφει true αν άλλαξε κάτι. */
  window.apdSetFund = function (fund, { rerender = true } = {}) {
    if (!VARIANTS[fund] || fund === window.APD_FUND) return false;
    // Οι ήδη καταχωρημένες τιμές, ΠΡΙΝ αλλάξει η μορφή.
    const kept = (rerender && typeof collectRecords === 'function')
      ? collectRecords() : null;

    window.APD_FUND = fund;
    window.APD_METADATA.StoixeiaAsfalisis = VARIANTS[fund];

    const sel = document.getElementById('fundSel');
    if (sel && sel.value !== fund) sel.value = fund;
    document.body.dataset.apdFund = fund;

    // Ξαναχτίζουμε τη φόρμα: τα πεδία κλειδώνονται με το ΟΝΟΜΑ της στήλης,
    // οπότε οι κοινές τιμές διατηρούνται και οι 53/54/55 απλώς εξαφανίζονται.
    if (kept && kept.length && typeof fillFormFromParsed === 'function') {
      fillFormFromParsed(kept);
    }
    return true;
  };

  /**
   * Αναγνωρίζει το ταμείο από το ΜΗΚΟΣ των εγγραφών τύπου 3 του αρχείου.
   * Επιστρέφει 'efka' | 'teka' | null (αν δεν υπάρχει σαφής ένδειξη).
   */
  window.apdDetectFund = function (text) {
    for (const ln of String(text || '').split(/\r?\n/)) {
      if (ln[0] === '3' && BY_LEN[ln.length]) return BY_LEN[ln.length];
    }
    return null;
  };

  // --- Αυτόματη εναλλαγή κατά την ανάγνωση αρχείου -------------------------
  // Το parseAPDText είναι function declaration => global property· το τυλίγουμε
  // ώστε ΚΑΘΕ ανάγνωση (κουμπί «Διάβασε» ή bridge.js) να διαλέγει μόνη της τη
  // σωστή γραμμογράφηση, αντί να πετάει σιωπηλά τις γραμμές λάθος μήκους.
  const origParse = (typeof parseAPDText === 'function')
    ? parseAPDText : window.parseAPDText;
  window.parseAPDText = function (text) {
    const detected = window.apdDetectFund(text);
    if (detected) window.apdSetFund(detected, { rerender: false });
    return origParse(text);
  };

  // --- Επιλογέας στο toolbar ----------------------------------------------
  const sel = document.getElementById('fundSel');
  if (sel) {
    sel.value = window.APD_FUND;
    sel.addEventListener('change', () => window.apdSetFund(sel.value));
  }
  document.body.dataset.apdFund = window.APD_FUND;

  // Αρχικό ταμείο από το query string (το περνά η σελίδα «Αρχεία ΑΠΔ»).
  const q = new URLSearchParams(location.search).get('fund');
  if (q && VARIANTS[q]) window.apdSetFund(q, { rerender: false });

  window.APD_FUND_LABELS = LABELS;
})();

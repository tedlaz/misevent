/*
 * Φόρτωση των scripts του viewer.
 *
 * Τα αρχεία ΚΑΤΕΒΑΙΝΟΥΝ ΠΑΡΑΛΛΗΛΑ αλλά ΕΚΤΕΛΟΥΝΤΑΙ ΜΕ ΤΗ ΣΕΙΡΑ: τα δυναμικά
 * <script> είναι εξ ορισμού async, οπότε τα σημαδεύουμε `async = false` — έτσι
 * ο browser εγγυάται εκτέλεση κατά τη σειρά εισαγωγής (ίδια σημασιολογία με
 * το παλιό σειριακό await, χωρίς 10 διαδοχικά round-trips: τα μητρώα ΟΣΥΚ
 * είναι ~750KB και σειριακά καθυστερούσαν αισθητά το άνοιγμα του viewer).
 */
const SCRIPTS = [
  './metadata.js',
  './table_options.js',
  './osyk_kad.js',
  './osyk_eid.js',
  './kpk.js',
  './epidotiseis.js',
  './topic.js',
  './app.js',
  './fund.js',    // γραμμογράφηση e-ΕΦΚΑ (162) / ΤΕΚΑ (148)
  './bridge.js',  // ενσωμάτωση με τη μισθοδοσία (?src=…)
];

/* Το ΑΡΧΕΙΟ ΑΠΔ (?src=) κατεβαίνει ΑΜΕΣΩΣ, παράλληλα με τα scripts — αλλιώς
   το κατέβασμα ξεκινούσε μόλις έτρεχε το bridge.js, δηλαδή ΜΕΤΑ τα ~830KB.
   Το bridge.js καταναλώνει αυτή την υπόσχεση (window.__apdSrcPromise). */
(() => {
  const src = new URLSearchParams(location.search).get('src');
  if (!src) return;
  const p = fetch(src).then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.arrayBuffer();
  });
  p.catch(() => {});  // το σφάλμα το χειρίζεται το bridge.js
  window.__apdSrcPromise = p;
})();

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src;
    s.async = false;           // ΣΕΙΡΑ εκτέλεσης, παράλληλο κατέβασμα
    s.onload = resolve;
    s.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.body.appendChild(s);
  });
}

Promise.all(SCRIPTS.map(loadScript)).catch((e) => {
  alert(`Failed to initialize app: ${e.message || e}`);
  console.error(e);
});

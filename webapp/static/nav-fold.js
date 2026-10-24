/* Πτυσσόμενες ομάδες του αριστερού μενού — ΜΟΝΟ η ΜΝΗΜΗ.
 * (+ πτυσσόμενες ΚΑΡΤΕΣ `details[data-fold]`, βλ. κάτω.)
 *
 * Το άνοιγμα/κλείσιμο το κάνει το native <details> (μηδέν JS), και ο server
 * αποδίδει ήδη ανοιχτή την ομάδα της τρέχουσας σελίδας. Χωρίς αυτό το αρχείο
 * το μενού δουλεύει — απλώς ξεχνά τις επιλογές του χρήστη σε κάθε πλοήγηση,
 * που σε πολυσέλιδη εφαρμογή κάνει το fold σχεδόν άχρηστο.
 *
 * ⚠️ Η ομάδα της ΕΝΕΡΓΗΣ σελίδας μένει ΠΑΝΤΑ ανοιχτή: αλλιώς ο χρήστης θα
 * έβλεπε το μενού να κρύβει τη σελίδα στην οποία βρίσκεται.
 */
(() => {
  const KEY = "misevent.nav.closed";

  function closedSet() {
    try {
      return new Set(JSON.parse(localStorage.getItem(KEY) || "[]"));
    } catch {
      return new Set(); // κατεστραμμένη τιμή / αποκλεισμένο storage
    }
  }

  function save(closed) {
    try {
      localStorage.setItem(KEY, JSON.stringify([...closed]));
    } catch {
      /* private mode: το μενού απλώς δεν θυμάται */
    }
  }

  function init() {
    const groups = document.querySelectorAll("details[data-nav-group]");
    if (!groups.length) return;
    const closed = closedSet();

    groups.forEach((group) => {
      const key = group.dataset.navGroup;
      // Ενεργή ομάδα: ό,τι κι αν λέει το storage, μένει ανοιχτή — και
      // καθαρίζεται από τα «κλειστά», ώστε να μην ξανακλείσει αλλού.
      if (group.querySelector("a.on")) {
        group.open = true;
        closed.delete(key);
      } else if (closed.has(key)) {
        group.open = false;
      }

      group.addEventListener("toggle", () => {
        const current = closedSet();
        if (group.open) current.delete(key);
        else current.add(key);
        save(current);
      });
    });

    save(closed);
  }

  // ΚΑΡΤΕΣ (`<details class="card fold" data-fold="…">`): ίδια μνήμη, χωριστό
  // κλειδί. Ξαναεφαρμόζεται μετά από κάθε htmx swap — ένα panel που
  // ξαναφορτώνεται (π.χ. χρονολόγιο υποχρεώσεων) έρχεται από τον server ανοιχτό.
  const CARDS = "misevent.cards.closed";

  function cardsClosed() {
    try {
      return new Set(JSON.parse(localStorage.getItem(CARDS) || "[]"));
    } catch {
      return new Set();
    }
  }

  function cards(root) {
    const closed = cardsClosed();
    root.querySelectorAll("details[data-fold]").forEach((card) => {
      if (card.dataset.foldBound) return;
      card.dataset.foldBound = "1";
      if (closed.has(card.dataset.fold)) card.open = false;
      card.addEventListener("toggle", () => {
        const now = cardsClosed(); // φρέσκο: άλλη κάρτα μπορεί να το άλλαξε
        if (card.open) now.delete(card.dataset.fold);
        else now.add(card.dataset.fold);
        try {
          localStorage.setItem(CARDS, JSON.stringify([...now]));
        } catch {
          /* private mode: η κάρτα απλώς δεν θυμάται */
        }
      });
    });
  }

  function start() {
    init();
    cards(document);
  }

  document.addEventListener("htmx:afterSwap", () => cards(document));

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();

/* Πτυσσόμενες ομάδες του αριστερού μενού — ΜΟΝΟ η ΜΝΗΜΗ.
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

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

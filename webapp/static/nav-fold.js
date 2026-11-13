/* Πτυσσόμενες ΚΑΡΤΕΣ `details[data-fold]` — ΜΟΝΟ η ΜΝΗΜΗ μεταξύ σελίδων.
 * Το άνοιγμα/κλείσιμο το κάνει το native <details> (μηδέν JS).
 */
(() => {
  // `<details class="card fold" data-fold="…">`. Ξαναεφαρμόζεται μετά από κάθε htmx swap — ένα panel που
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
    cards(document);
  }

  document.addEventListener("htmx:afterSwap", () => cards(document));

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();

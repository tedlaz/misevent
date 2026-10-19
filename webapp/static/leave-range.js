(() => {
  const MONTHS = [
    'Ιανουάριος', 'Φεβρουάριος', 'Μάρτιος', 'Απρίλιος', 'Μάιος', 'Ιούνιος',
    'Ιούλιος', 'Αύγουστος', 'Σεπτέμβριος', 'Οκτώβριος', 'Νοέμβριος', 'Δεκέμβριος'
  ];
  let active = null;

  const iso = d => [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'),
    String(d.getDate()).padStart(2, '0')].join('-');
  const parse = value => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return null;
    const [y, m, d] = value.split('-').map(Number);
    const result = new Date(y, m - 1, d);
    return Number.isNaN(result.getTime()) ? null : result;
  };
  const gr = d => `${String(d.getDate()).padStart(2, '0')}/${String(
    d.getMonth() + 1).padStart(2, '0')}/${d.getFullYear()}`;

  function parts(root) {
    return {
      root,
      from: root.querySelector('[name="date_from"]'),
      to: root.querySelector('[name="date_to"]'),
      trigger: root.querySelector('.leave-range-trigger'),
      value: root.querySelector('.leave-range-value'),
      popover: root.querySelector('.leave-range-popover'),
      month: root.querySelector('[data-range-month]'),
      days: root.querySelector('[data-range-days]'),
      hint: root.querySelector('[data-range-hint]')
    };
  }

  function sync(widget) {
    const start = parse(widget.from.value);
    const end = parse(widget.to.value);
    widget.value.textContent = start && end
      ? `${gr(start)} – ${gr(end)}` : 'Επιλέξτε ημερομηνίες';
    widget.hint.textContent = start && end
      ? `Επιλέχθηκαν ${gr(start)} – ${gr(end)}`
      : start ? 'Τώρα επιλέξτε την τελευταία ημέρα της άδειας'
        : 'Επιλέξτε την πρώτη ημέρα της άδειας';
    widget.hint.classList.toggle('complete', Boolean(start && end));
  }

  function render(widget) {
    const year = widget.shown.getFullYear();
    const month = widget.shown.getMonth();
    widget.month.textContent = `${MONTHS[month]} ${year}`;
    widget.days.replaceChildren();

    const first = new Date(year, month, 1);
    const offset = (first.getDay() + 6) % 7; // εβδομάδα από Δευτέρα
    const cursor = new Date(year, month, 1 - offset);
    const start = parse(widget.from.value);
    const end = parse(widget.to.value);
    const today = iso(new Date());

    for (let i = 0; i < 42; i += 1) {
      const d = new Date(cursor);
      d.setDate(cursor.getDate() + i);
      const value = iso(d);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'leave-range-day';
      button.textContent = d.getDate();
      button.dataset.rangeDate = value;
      button.setAttribute('aria-label', gr(d));
      if (d.getMonth() !== month) button.classList.add('outside');
      if (value === today) button.classList.add('today');
      if (start && value === iso(start)) button.classList.add('range-start');
      if (end && value === iso(end)) button.classList.add('range-end');
      if (start && end && d > start && d < end) button.classList.add('in-range');
      widget.days.append(button);
    }
    sync(widget);
  }

  function close() {
    if (!active) return;
    active.popover.hidden = true;
    active.popover.removeAttribute('style');
    if (active.root.isConnected) active.root.append(active.popover);
    active.root.classList.remove('open');
    active.trigger.setAttribute('aria-expanded', 'false');
    active = null;
  }

  function open(root) {
    if (active?.root === root) return close();
    close();
    const widget = parts(root);
    const start = parse(widget.from.value);
    widget.shown = start || new Date();
    widget.shown = new Date(widget.shown.getFullYear(), widget.shown.getMonth(), 1);
    widget.root.classList.add('open');
    widget.trigger.setAttribute('aria-expanded', 'true');
    active = widget;
    // Το .mbody του modal κάνει scroll. Μεταφέρουμε προσωρινά το popover στο
    // body ώστε να μη γίνει clip, χωρίς να αλλάξουν τα πεδία της φόρμας.
    document.body.append(widget.popover);
    widget.popover.hidden = false;
    render(widget);
    position(widget);
  }

  function position(widget) {
    const anchor = widget.trigger.getBoundingClientRect();
    const popup = widget.popover;
    const gap = 7;
    const margin = 12;
    const width = Math.min(330, window.innerWidth - margin * 2);
    popup.style.width = `${width}px`;
    const height = popup.offsetHeight;
    const roomBelow = window.innerHeight - anchor.bottom - margin;
    const roomAbove = anchor.top - margin;
    let top = roomBelow >= height || roomBelow >= roomAbove
      ? anchor.bottom + gap : anchor.top - height - gap;
    top = Math.max(margin, Math.min(top, window.innerHeight - height - margin));
    let left = anchor.left;
    left = Math.max(margin, Math.min(left, window.innerWidth - width - margin));
    popup.style.top = `${top}px`;
    popup.style.left = `${left}px`;
  }

  document.addEventListener('click', event => {
    const trigger = event.target.closest('.leave-range-trigger');
    if (trigger) return open(trigger.closest('[data-leave-range]'));

    if (!active) return;
    if (event.target.closest('[data-range-prev]')) {
      active.shown.setMonth(active.shown.getMonth() - 1);
      return render(active);
    }
    if (event.target.closest('[data-range-next]')) {
      active.shown.setMonth(active.shown.getMonth() + 1);
      return render(active);
    }
    const day = event.target.closest('[data-range-date]');
    if (day) {
      const chosen = day.dataset.rangeDate;
      const start = parse(active.from.value);
      const end = parse(active.to.value);
      if (!start || end) {
        active.from.value = chosen;
        active.to.value = '';
      } else if (chosen < active.from.value) {
        active.to.value = active.from.value;
        active.from.value = chosen;
      } else {
        active.to.value = chosen;
      }
      render(active);
      return;
    }
    if (!active.root.contains(event.target)) close();
  });

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && active) {
      const trigger = active.trigger;
      close();
      trigger.focus();
    }
  });
  window.addEventListener('resize', close);

  // Αρχικές τιμές σε modal που επέστρεψε ο server μετά από validation error.
  function syncAll(scope = document) {
    scope.querySelectorAll('[data-leave-range]').forEach(root => sync(parts(root)));
  }
  document.addEventListener('DOMContentLoaded', () => syncAll());
  document.body.addEventListener('htmx:afterSwap', event => syncAll(event.target));

  // Τα hidden inputs δεν συμμετέχουν στο native required validation.
  document.addEventListener('submit', event => {
    const root = event.target.querySelector?.('[data-leave-range]');
    if (!root) return;
    const widget = parts(root);
    if (widget.from.value && widget.to.value) return;
    event.preventDefault();
    open(root);
    widget.hint.textContent = 'Επιλέξτε και την πρώτη και την τελευταία ημέρα';
    widget.hint.classList.remove('complete');
  });
})();

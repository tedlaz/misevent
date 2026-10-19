(() => {
  const DAY = 86400000;
  const ZOOM_LEVELS = [1, 2, 4, 8];
  let zoomIndex = 0;
  let zoomAnchor = .5;
  const parse = value => {
    const [year, month, day] = value.split('-').map(Number);
    return new Date(Date.UTC(year, month - 1, day));
  };
  const iso = value => value.toISOString().slice(0, 10);
  const addDays = (value, days) => new Date(value.getTime() + days * DAY);
  const format = value => new Intl.DateTimeFormat('el-GR', {
    day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC'
  }).format(value);
  const daysLabel = (panel, from, to) => String(countWorkdays(panel, from, to));
  const amount = value => new Intl.NumberFormat('el-GR', {
    maximumFractionDigits: 0
  }).format(Math.round(value));

  function workdays(panel) {
    if (!panel._leaveWorkdays) {
      panel._leaveWorkdays = new Set(JSON.parse(
        panel.querySelector('[data-workday-offsets]').textContent
      ));
    }
    return panel._leaveWorkdays;
  }

  function accrualDaily(track) {
    if (!track._leaveAccrualDaily) {
      track._leaveAccrualDaily = JSON.parse(
        track.querySelector('[data-accrual-daily]').textContent
      );
    }
    return track._leaveAccrualDaily;
  }

  function yearStart(panel) {
    return new Date(Date.UTC(Number(panel.dataset.year), 0, 1));
  }

  function dateOffset(panel, value) {
    return Math.round((value - yearStart(panel)) / DAY);
  }

  function countWorkdays(panel, from, to) {
    if (to < from) return 0;
    const valid = workdays(panel);
    const first = Math.max(0, dateOffset(panel, from));
    const last = Math.min(Number(panel.dataset.days) - 1, dateOffset(panel, to));
    let total = 0;
    for (let offset = first; offset <= last; offset += 1) {
      if (valid.has(offset)) total += 1;
    }
    return total;
  }

  function accruedAt(track, value) {
    const panel = track.closest('[data-leave-planner]');
    const daily = accrualDaily(track);
    const offset = dateOffset(panel, value);
    if (offset < 0) return 0;
    return Number(daily[Math.min(offset, daily.length - 1)] || 0);
  }

  function annualUsedUntil(track, checkpoint, except = null) {
    const panel = track.closest('[data-leave-planner]');
    let total = 0;
    track.querySelectorAll('[data-leave-bar][data-type="ANNUAL"]').forEach(bar => {
      if (bar === except) return;
      const from = parse(bar.dataset.from);
      const to = parse(bar.dataset.to);
      if (from <= checkpoint) total += countWorkdays(
        panel, from, to < checkpoint ? to : checkpoint
      );
    });
    return total;
  }

  function toneFor(balance, accrued) {
    if (balance < -.005) return 'danger';
    if (balance <= Math.max(1, accrued * .2)) return 'warn';
    return 'good';
  }

  function setTone(element, tone) {
    element.classList.remove('tone-good', 'tone-warn', 'tone-danger', 'tone-neutral');
    element.classList.add(`tone-${tone}`);
  }

  function annualStatus(bar) {
    if (bar.dataset.type !== 'ANNUAL') return null;
    const track = bar.closest('.leave-track');
    const panel = track.closest('[data-leave-planner]');
    const from = parse(bar.dataset.from);
    const to = parse(bar.dataset.to);
    const accrued = accruedAt(track, from);
    const usedBefore = annualUsedUntil(track, addDays(from, -1), bar);
    const requested = countWorkdays(panel, from, to);
    const available = accrued - usedBefore;
    const remaining = available - requested;
    bar.classList.toggle('over-accrual', remaining < -.005);
    return {accrued, available, requested, remaining};
  }

  function refreshAccrual(track, animate = false) {
    const panel = track.closest('[data-leave-planner]');
    const start = yearStart(panel);
    track.querySelectorAll('[data-accrual-month]').forEach(segment => {
      if (segment.dataset.active !== '1') return;
      const checkpoint = addDays(start, Number(segment.dataset.checkpointOffset));
      const accrued = Number(segment.dataset.accrued);
      const used = annualUsedUntil(track, checkpoint);
      const balance = accrued - used;
      const value = segment.querySelector('[data-accrual-value]');
      const nextText = amount(balance);
      if (animate && value.textContent !== nextText) {
        segment.classList.remove('bump');
        void segment.offsetWidth;
        segment.classList.add('bump');
        setTimeout(() => segment.classList.remove('bump'), 320);
      }
      value.textContent = nextText;
      setTone(segment, toneFor(balance, accrued));
      segment.title = `${segment.dataset.monthName} · Δικαίωμα ${amount(accrued)}` +
        ` · Άδειες ${used} · Υπόλοιπο ${amount(balance)}`;
    });

    const summary = track.parentElement.querySelector('[data-balance-summary]');
    if (summary) {
      const reference = addDays(start, Number(summary.dataset.referenceOffset));
      const accrued = accruedAt(track, reference);
      const taken = annualUsedUntil(track, reference);
      const planned = annualUsedUntil(track, addDays(start, Number(panel.dataset.days) - 1));
      const balance = accrued - taken;
      summary.querySelector('[data-summary-accrued]').textContent = amount(accrued);
      summary.querySelector('[data-summary-taken]').textContent = amount(taken);
      summary.querySelector('[data-summary-balance]').textContent = amount(balance);
      setTone(summary, toneFor(balance, accrued));
      summary.title = `Κανονική άδεια έως ${summary.dataset.referenceLabel}` +
        ` · Δικαίωμα ${amount(accrued)} · Άδειες ${taken} · Υπόλοιπο ${amount(balance)}`;
      if (animate) {
        summary.classList.add('bump');
        setTimeout(() => summary.classList.remove('bump'), 240);
      }
      const future = track.parentElement.querySelector('[data-future-planned]');
      const futureDays = Math.max(0, planned - taken);
      future.hidden = futureDays === 0;
      future.textContent = futureDays
        ? `+${futureDays} προγραμματισμένες μετά την ημερομηνία αναφοράς` : '';
    }

    track.querySelectorAll('[data-leave-bar][data-type="ANNUAL"]').forEach(annualStatus);
  }

  function applyZoom(panel, nextIndex, keepCurrentAnchor = true) {
    if (!panel) return;
    const scroll = panel.querySelector('.leave-board-scroll');
    if (keepCurrentAnchor && scroll.scrollWidth) {
      zoomAnchor = (scroll.scrollLeft + scroll.clientWidth / 2) / scroll.scrollWidth;
    }
    zoomIndex = Math.max(0, Math.min(ZOOM_LEVELS.length - 1, nextIndex));
    const level = ZOOM_LEVELS[zoomIndex];
    panel.style.setProperty('--leave-timeline-width', `${850 * level}px`);
    panel.querySelector('[data-zoom-label]').textContent = `${level}×`;
    panel.querySelector('[data-zoom-out]').disabled = zoomIndex === 0;
    panel.querySelector('[data-zoom-in]').disabled = zoomIndex === ZOOM_LEVELS.length - 1;
    // Η ανάγνωση του scrollWidth εφαρμόζει άμεσα το νέο πλάτος, οπότε ακόμη
    // και γρήγορα διαδοχικά κλικ διατηρούν το ίδιο σημείο ενδιαφέροντος.
    scroll.scrollLeft = Math.max(
      0, zoomAnchor * scroll.scrollWidth - scroll.clientWidth / 2
    );
  }

  function setupPlanner(scope = document) {
    const panel = scope.matches?.('[data-leave-planner]')
      ? scope : scope.querySelector?.('[data-leave-planner]');
    if (panel) {
      applyZoom(panel, zoomIndex, false);
      panel.querySelectorAll('.leave-track').forEach(track => refreshAccrual(track));
    }
  }

  document.addEventListener('DOMContentLoaded', () => setupPlanner());
  document.body.addEventListener('htmx:afterSwap', event => setupPlanner(event.target));
  document.addEventListener('scroll', event => {
    if (!event.target.matches?.('.leave-board-scroll') || !event.target.scrollWidth) return;
    zoomAnchor = (event.target.scrollLeft + event.target.clientWidth / 2) /
      event.target.scrollWidth;
  }, true);

  document.addEventListener('click', event => {
    const control = event.target.closest('[data-zoom-in],[data-zoom-out],[data-zoom-reset]');
    if (!control) return;
    const panel = control.closest('[data-leave-planner]');
    if (control.matches('[data-zoom-in]')) applyZoom(panel, zoomIndex + 1);
    else if (control.matches('[data-zoom-out]')) applyZoom(panel, zoomIndex - 1);
    else applyZoom(panel, 0);
  });

  function position(bar) {
    const planner = bar.closest('[data-leave-planner]');
    const year = Number(planner.dataset.year);
    const days = Number(planner.dataset.days);
    const yearStart = new Date(Date.UTC(year, 0, 1));
    const yearEnd = new Date(Date.UTC(year, 11, 31));
    const from = parse(bar.dataset.from);
    const to = parse(bar.dataset.to);
    const shownFrom = from < yearStart ? yearStart : from;
    const shownTo = to > yearEnd ? yearEnd : to;
    const left = (shownFrom - yearStart) / DAY / days * 100;
    const width = ((shownTo - shownFrom) / DAY + 1) / days * 100;
    bar.style.left = `${left}%`;
    bar.style.width = `${width}%`;
    const workingDayCount = Number(daysLabel(planner, from, to));
    const visibleLabel = bar.querySelector('.leave-bar-text');
    if (visibleLabel) visibleLabel.textContent = String(workingDayCount);
    const status = annualStatus(bar);
    refreshAccrual(bar.closest('.leave-track'), true);
    const warning = status?.remaining < -.005 ? ' · Υπέρβαση σωρευμένου δικαιώματος' : '';
    const unit = workingDayCount === 1 ? 'εργάσιμη ημέρα' : 'εργάσιμες ημέρες';
    const description = `${bar.dataset.label} · ${format(from)} – ${format(to)}` +
      ` · ${workingDayCount} ${unit}${warning}`;
    bar.title = description;
    bar.setAttribute('aria-label', description);
    return status;
  }

  async function save(bar, from, to, originalFrom, originalTo) {
    bar.classList.add('saving');
    const body = new URLSearchParams({
      date_from: iso(from),
      date_to: iso(to),
      leave_type: bar.dataset.type,
      notes: bar.dataset.notes || ''
    });
    try {
      const response = await fetch(bar.dataset.updateUrl, {
        method: 'POST', body,
        headers: {'X-Requested-With': 'XMLHttpRequest'}
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.message || 'Η αλλαγή απέτυχε');
      bar.dataset.from = result.date_from;
      bar.dataset.to = result.date_to;
      position(bar);
      toast(result.message, false);
    } catch (error) {
      bar.dataset.from = iso(originalFrom);
      bar.dataset.to = iso(originalTo);
      position(bar);
      toast(error.message || 'Η αλλαγή απέτυχε', true);
    } finally {
      bar.classList.remove('saving');
    }
  }

  // ΚΛΕΙΔΩΜΕΝΟ ΠΑΡΕΛΘΟΝ: μόλις κλείσει η μισθοδοσία ενός μήνα, ο μήνας και
  // κάθε προγενέστερος παγώνουν — καμία άδεια δεν δημιουργείται, μετακινείται
  // ή ακυρώνεται εκεί (services._check_open_period). Η ζώνη είναι βαμμένη
  // μωβ· εδώ κόβουμε την αλληλεπίδραση, η επιβολή είναι στον server.
  function isClosedDay(panel, isoDate) {
    const end = panel?.dataset.closedEnd;
    return Boolean(end) && isoDate <= end;
  }

  function closedToast(panel) {
    const label = panel?.dataset.closedLabel;
    toast(
      label
        ? `Η μισθοδοσία ${label} έχει κλείσει — η περίοδος είναι κλειδωμένη`
        : 'Κλεισμένη περίοδος — δεν επιτρέπονται αλλαγές',
      true
    );
  }

  document.addEventListener('pointerdown', event => {
    const bar = event.target.closest('[data-leave-bar]');
    if (!bar || event.button !== 0 || bar.classList.contains('saving')) return;
    const track = bar.closest('.leave-track');
    const planner = bar.closest('[data-leave-planner]');
    if (isClosedDay(planner, bar.dataset.from)) {
      closedToast(planner);
      event.preventDefault();
      return;
    }
    const startX = event.clientX;
    const originalFrom = parse(bar.dataset.from);
    const originalTo = parse(bar.dataset.to);
    const barRect = bar.getBoundingClientRect();
    // Ακόμη και σε μπάρα μίας ημέρας, το κεντρικό 50% παραμένει διαθέσιμο
    // για μετακίνηση και τα δύο εξωτερικά τεταρτημόρια λειτουργούν ως άκρα.
    const edge = Math.min(8, Math.max(1, barRect.width * .25));
    const localX = event.clientX - barRect.left;
    const mode = localX <= edge ? 'start'
      : localX >= barRect.width - edge ? 'end' : 'move';
    let nextFrom = originalFrom;
    let nextTo = originalTo;
    let moved = false;
    const badge = document.createElement('span');
    badge.className = 'leave-date-badge';
    const showBadge = current => {
      if (!badge.isConnected) document.body.append(badge);
      const status = annualStatus(bar);
      const main = document.createElement('strong');
      main.textContent = `${mode === 'end' ? 'Λήξη' : 'Έναρξη'}: ${format(
        mode === 'end' ? nextTo : nextFrom)}`;
      badge.replaceChildren(main);
      badge.classList.toggle('danger', Boolean(status && status.remaining < -.005));
      if (status) {
        const detail = document.createElement('small');
        detail.textContent = `Δικ. ${amount(status.accrued)} · διαθέσιμες ` +
          `${amount(status.available)} · μετά ${amount(status.remaining)}`;
        badge.append(detail);
      }
      badge.style.left = `${Math.min(current.clientX + 12, window.innerWidth - badge.offsetWidth - 10)}px`;
      badge.style.top = `${Math.max(current.clientY - 38, 8)}px`;
    };
    bar.setPointerCapture(event.pointerId);
    bar.classList.add('dragging');

    const move = current => {
      const dayWidth = track.getBoundingClientRect().width /
        Number(bar.closest('[data-leave-planner]').dataset.days);
      const delta = Math.round((current.clientX - startX) / dayWidth);
      moved = moved || Math.abs(current.clientX - startX) > 3;
      if (mode === 'start') {
        nextFrom = addDays(originalFrom, delta);
        if (nextFrom > originalTo) nextFrom = originalTo;
      } else if (mode === 'end') {
        nextTo = addDays(originalTo, delta);
        if (nextTo < originalFrom) nextTo = originalFrom;
      } else {
        nextFrom = addDays(originalFrom, delta);
        nextTo = addDays(originalTo, delta);
      }
      bar.dataset.from = iso(nextFrom);
      bar.dataset.to = iso(nextTo);
      position(bar);
      if (moved) showBadge(current);
    };

    const finish = current => {
      bar.removeEventListener('pointermove', move);
      bar.removeEventListener('pointerup', finish);
      bar.removeEventListener('pointercancel', cancel);
      bar.classList.remove('dragging');
      if (badge.isConnected) setTimeout(() => badge.remove(), 450);
      if (moved && isClosedDay(planner, iso(nextFrom))) {
        // Μετακίνηση ΜΕΣΑ σε κλεισμένη περίοδο: επαναφορά χωρίς κλήση.
        closedToast(planner);
        bar.dataset.from = iso(originalFrom);
        bar.dataset.to = iso(originalTo);
        position(bar);
      } else if (moved) save(bar, nextFrom, nextTo, originalFrom, originalTo);
      else htmx.ajax('GET', bar.dataset.modalUrl, {target: '#modal-host', swap: 'innerHTML'});
    };
    const cancel = () => {
      bar.removeEventListener('pointermove', move);
      bar.removeEventListener('pointerup', finish);
      bar.removeEventListener('pointercancel', cancel);
      bar.classList.remove('dragging');
      badge.remove();
      bar.dataset.from = iso(originalFrom);
      bar.dataset.to = iso(originalTo);
      position(bar);
    };
    bar.addEventListener('pointermove', move);
    bar.addEventListener('pointerup', finish);
    bar.addEventListener('pointercancel', cancel);
    event.preventDefault();
  });

  document.addEventListener('dblclick', event => {
    const track = event.target.closest('.leave-track');
    if (!track || event.target.closest('[data-leave-bar],.leave-accrual-strip')) return;
    const planner = track.closest('[data-leave-planner]');
    const rect = track.getBoundingClientRect();
    const days = Number(planner.dataset.days);
    const offset = Math.max(0, Math.min(
      days - 1, Math.floor((event.clientX - rect.left) / rect.width * days)
    ));
    const selected = addDays(new Date(Date.UTC(Number(planner.dataset.year), 0, 1)), offset);
    const selectedIso = iso(selected);
    if ((track.dataset.activeFrom && selectedIso < track.dataset.activeFrom) ||
        (track.dataset.activeTo && selectedIso > track.dataset.activeTo)) {
      toast('Ο εργαζόμενος δεν εργάζεται τη συγκεκριμένη ημερομηνία', true);
      return;
    }
    if (isClosedDay(planner, selectedIso)) {
      closedToast(planner);
      return;
    }
    const url = new URL(track.dataset.createUrl, window.location.origin);
    url.searchParams.set('date', selectedIso);
    url.searchParams.set('year', planner.dataset.year);
    htmx.ajax('GET', url.toString(), {target: '#modal-host', swap: 'innerHTML'});
  });

  document.addEventListener('keydown', event => {
    const bar = event.target.closest?.('[data-leave-bar]');
    if (!bar || !['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    const planner = bar.closest('[data-leave-planner]');
    if (isClosedDay(planner, bar.dataset.from)) {
      closedToast(planner);
      return;
    }
    htmx.ajax('GET', bar.dataset.modalUrl, {target: '#modal-host', swap: 'innerHTML'});
  });
})();

(() => {
  const BASE_WIDTH = 850;
  const STICKY_WIDTH = 235;
  const MIN_ZOOM = 1;
  const MAX_ZOOM = 72;
  const BUTTON_FACTOR = 1.8;
  const DAY = 86400000;
  let zoom = 16;  // αρχική προβολή: 16×, κεντραρισμένη στη σημερινή ημέρα
  let centeredYear = null;
  let toastTimer = null;
  let returnView = null;
  let restoringView = false;
  let bulkPanel = null;
  let bulkAnchor = null;
  let bulkSwapInProgress = false;
  let copiedWeek = null;
  let weekMenuTarget = null;
  let weekAnchor = null;
  const bulkSelection = new Set();
  const weekSelection = new Set();  // ISO Δευτέρες επιλεγμένων εβδομάδων (όλη η εταιρεία)

  function weeksToolbar(panel) {
    let tb = panel.querySelector('[data-work-erg-toolbar]');
    if (tb) return tb;
    tb = document.createElement('div');
    tb.className = 'work-erg-weeks-toolbar';
    tb.dataset.workErgToolbar = '';
    tb.hidden = true;
    tb.innerHTML = `
      <strong data-erg-week-count>0</strong>&nbsp;εβδομάδες επιλεγμένες — υποβολή για ΟΛΟΥΣ τους εργαζόμενους
      <span class="spacer"></span>
      <button type="button" data-erg-week-submit>Υποβολή στο ΕΡΓΑΝΗ</button>
      <button type="button" class="ghost" data-erg-week-clear>Καθαρισμός</button>`;
    const legend = panel.querySelector('.work-legend');
    if (legend) legend.after(tb); else panel.prepend(tb);
    return tb;
  }

  function updateWeekSelection(panel) {
    const tb = weeksToolbar(panel);
    tb.hidden = weekSelection.size === 0;
    tb.querySelector('[data-erg-week-count]').textContent = weekSelection.size;
    panel.querySelectorAll('[data-work-week]').forEach(week => {
      week.classList.toggle('week-selected', weekSelection.has(week.dataset.weekStart));
    });
  }

  function clearWeekSelection(panel) {
    weekSelection.clear();
    weekAnchor = null;
    if (panel) updateWeekSelection(panel);
  }

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const zoomText = value => `${value < 10 ? value.toFixed(1).replace('.0', '') : Math.round(value)}×`;
  const formatDate = value => new Intl.DateTimeFormat('el-GR', {
    day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC'
  }).format(value);

  function bulkToolbar(panel) {
    let toolbar = panel.querySelector('[data-work-bulk-toolbar]');
    if (toolbar) return toolbar;
    toolbar = document.createElement('div');
    toolbar.className = 'work-bulk-toolbar';
    toolbar.dataset.workBulkToolbar = '';
    toolbar.hidden = true;
    toolbar.innerHTML = `
      <strong><span data-work-bulk-count>0</span> επιλεγμένα</strong>
      <label>Ωράριο <input data-work-bulk-hours value="09:00-17:00"
        placeholder="09:00-13:00+Τ17:00-21:00"
        aria-label="Ωράριο μαζικής μεταβολής"></label>
      <button type="button" class="save" data-work-bulk-action="save">Αποθήκευση</button>
      <button type="button" class="danger" data-work-bulk-action="delete">Ρεπό / Διαγραφή</button>
      <button type="button" class="revert" data-work-bulk-action="revert">Ακύρωση αλλαγών</button>
      <button type="button" class="save" data-work-bulk-action="leave">Άδεια…</button>
      <button type="button" class="close" data-work-bulk-clear aria-label="Καθαρισμός επιλογής">×</button>`;
    panel.append(toolbar);
    return toolbar;
  }

  function updateBulkSelection(panel) {
    const toolbar = bulkToolbar(panel);
    toolbar.hidden = bulkSelection.size === 0;
    toolbar.querySelector('[data-work-bulk-count]').textContent = bulkSelection.size;
  }

  function clearBulkSelection(panel) {
    bulkSelection.forEach(cell => cell.classList.remove('bulk-selected'));
    bulkSelection.clear();
    bulkAnchor = null;
    if (panel) updateBulkSelection(panel);
  }

  // ΚΛΕΙΔΩΜΕΝΟ ΠΑΡΕΛΘΟΝ: μόλις κλείσει η μισθοδοσία ενός μήνα, ο μήνας και
  // κάθε προγενέστερος παγώνουν (services._check_open_days). Το grid τα βάφει
  // μωβ· εδώ απλώς δεν τα αφήνουμε να επιλεγούν/ανοίξουν — η επιβολή είναι
  // ούτως ή άλλως στον server.
  function isClosedCell(el) {
    return !!el && el.dataset.closed === '1';
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

  function setBulkSelected(cell, selected = true) {
    if (!cell || cell.dataset.active !== '1' || isClosedCell(cell)) return;
    cell.classList.toggle('bulk-selected', selected);
    if (selected) bulkSelection.add(cell);
    else bulkSelection.delete(cell);
  }

  function selectBulkRange(panel, anchor, target, additive) {
    const rows = [...panel.querySelectorAll('.work-row')];
    const anchorRow = rows.indexOf(anchor.closest('.work-row'));
    const targetRow = rows.indexOf(target.closest('.work-row'));
    if (anchorRow < 0 || targetRow < 0) return;
    if (!additive) clearBulkSelection(panel);
    bulkAnchor = anchor;
    const firstRow = Math.min(anchorRow, targetRow);
    const lastRow = Math.max(anchorRow, targetRow);
    const firstDay = Math.min(Number(anchor.dataset.dayIndex), Number(target.dataset.dayIndex));
    const lastDay = Math.max(Number(anchor.dataset.dayIndex), Number(target.dataset.dayIndex));
    rows.slice(firstRow, lastRow + 1).forEach(row => {
      row.querySelectorAll('[data-work-day]').forEach(cell => {
        const day = Number(cell.dataset.dayIndex);
        if (day >= firstDay && day <= lastDay) setBulkSelected(cell);
      });
    });
    updateBulkSelection(panel);
  }

  function weekContextMenu(panel) {
    let menu = panel.querySelector('[data-work-week-menu]');
    if (menu) return menu;
    menu = document.createElement('div');
    menu.className = 'work-shift-menu work-week-menu';
    menu.dataset.workWeekMenu = '';
    menu.hidden = true;
    menu.innerHTML = `
      <small data-work-week-copy-label>Δεν έχει αντιγραφεί εβδομάδα</small>
      <button type="button" data-work-week-action="copy"><span>⧉</span>Αντιγραφή εβδομάδας</button>
      <button type="button" data-work-week-action="paste"><span>⇩</span>Επικόλληση εδώ</button>`;
    panel.append(menu);
    return menu;
  }

  function hideWeekContextMenu(panel = bulkPanel) {
    panel?.querySelector('[data-work-week-menu]')?.setAttribute('hidden', '');
    weekMenuTarget = null;
  }

  function showWeekContextMenu(week, clientX, clientY) {
    const panel = week.closest('[data-work-schedule-planner]');
    const menu = weekContextMenu(panel);
    weekMenuTarget = week;
    const paste = menu.querySelector('[data-work-week-action="paste"]');
    paste.disabled = !copiedWeek || copiedWeek.start === week.dataset.weekStart;
    menu.querySelector('[data-work-week-copy-label]').textContent = copiedWeek
      ? `Αντιγράφηκε: ${copiedWeek.label}` : 'Δεν έχει αντιγραφεί εβδομάδα';
    menu.hidden = false;
    const rect = menu.getBoundingClientRect();
    menu.style.left = `${clamp(clientX, 8, window.innerWidth - rect.width - 8)}px`;
    menu.style.top = `${clamp(clientY, 8, window.innerHeight - rect.height - 8)}px`;
  }

  function showZoomToast(panel, clientX, clientY, ratio) {
    let toast = panel.querySelector('.work-zoom-toast');
    if (!toast) {
      toast = document.createElement('span');
      toast.className = 'work-zoom-toast';
      panel.append(toast);
    }
    const dayIndex = Math.round(clamp(ratio, 0, 1) * (Number(panel.dataset.days) - 1));
    const date = new Date(Date.UTC(Number(panel.dataset.year), 0, 1) + dayIndex * DAY);
    toast.textContent = `${zoomText(zoom)} · ${formatDate(date)}`;
    toast.style.left = `${clamp(clientX + 14, 12, window.innerWidth - 130)}px`;
    toast.style.top = `${clamp(clientY - 42, 12, window.innerHeight - 45)}px`;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 650);
  }

  function applyZoom(
    panel, nextZoom, anchorClientX = null, anchorClientY = null, lockedRatio = null
  ) {
    const scroll = panel.querySelector('[data-work-scroll]');
    if (!scroll) return;
    const timeline = panel.querySelector('[data-work-track]') ||
      panel.querySelector('.work-time-head');
    const rect = scroll.getBoundingClientRect();
    const timelineRect = timeline.getBoundingClientRect();
    const timelineStart = timelineRect.left - rect.left + scroll.scrollLeft;
    const localX = clamp(
      (anchorClientX ?? (rect.left + STICKY_WIDTH + (rect.width - STICKY_WIDTH) / 2)) - rect.left,
      STICKY_WIDTH,
      rect.width
    );
    // Στο 1× το minmax μπορεί να απλώσει το timeline πέρα από τα 850px.
    // Χρησιμοποιούμε το πραγματικό πλάτος, όχι μόνο το θεωρητικό CSS width.
    const oldWidth = timelineRect.width;
    const timelineAtCursor = scroll.scrollLeft + localX - timelineStart;
    const ratio = lockedRatio ?? clamp(timelineAtCursor / oldWidth, 0, 1);

    zoom = clamp(nextZoom, MIN_ZOOM, MAX_ZOOM);
    panel.style.setProperty('--work-timeline-width', `${BASE_WIDTH * zoom}px`);
    panel.classList.toggle('day-detail', zoom >= 5.5);
    panel.classList.toggle('hour-detail', zoom >= 10.5);
    panel.classList.toggle('week-detail', zoom >= 30);
    panel.querySelector('[data-work-zoom-label]').textContent = zoomText(zoom);
    panel.querySelector('[data-work-zoom-out]').disabled = zoom <= MIN_ZOOM + .001;
    panel.querySelector('[data-work-zoom-in]').disabled = zoom >= MAX_ZOOM - .001;

    // Η ανάγνωση εφαρμόζει το νέο πλάτος πριν υπολογιστεί η κύλιση.
    void scroll.scrollWidth;
    const nextWidth = timeline.getBoundingClientRect().width;
    scroll.scrollLeft = timelineStart + ratio * nextWidth - localX;
    if (anchorClientX !== null && anchorClientY !== null) {
      showZoomToast(panel, anchorClientX, anchorClientY, ratio);
    }
  }

  // Κρατά zoom/κύλιση ώστε μετά την αποθήκευση το panel να γυρίσει εκεί.
  function rememberView(panel) {
    const scroll = panel.querySelector('[data-work-scroll]');
    returnView = {
      year: panel.dataset.year,
      zoom,
      scrollLeft: scroll.scrollLeft,
      scrollTop: scroll.scrollTop,
      windowX: window.scrollX,
      windowY: window.scrollY
    };
  }

  function zoomRange(panel, startIndex, endIndex, clientY) {
    const scroll = panel.querySelector('[data-work-scroll]');
    const rect = scroll.getBoundingClientRect();
    const days = Number(panel.dataset.days);
    const rangeDays = endIndex - startIndex + 1;
    const available = Math.max(240, rect.width - STICKY_WIDTH);
    const targetZoom = available * days / (BASE_WIDTH * rangeDays);
    const ratio = (startIndex + rangeDays / 2) / days;
    applyZoom(
      panel, targetZoom, rect.left + STICKY_WIDTH + available / 2,
      clientY, ratio
    );
  }

  function setupPlanner(scope = document) {
    const panel = scope.matches?.('[data-work-schedule-planner]')
      ? scope : scope.querySelector?.('[data-work-schedule-planner]');
    if (!panel) return;
    if (bulkPanel !== panel) {
      bulkSelection.clear();
      bulkAnchor = null;
      weekSelection.clear();
      weekAnchor = null;
      bulkPanel = panel;
      bulkToolbar(panel);
      weeksToolbar(panel);
      weekContextMenu(panel);
    }
    const scroll = panel.querySelector('[data-work-scroll]');
    applyZoom(panel, zoom);
    // Πρώτη εμφάνιση του έτους: κέντρο στη σημερινή ημέρα (ημ. αναφοράς),
    // αν ανήκει στο έτος. Όχι όταν επανέρχεται αποθηκευμένη προβολή.
    const today = panel.querySelector('.work-day-heads .today');
    if (today && !returnView && centeredYear !== panel.dataset.year) {
      centeredYear = panel.dataset.year;
      const ratio = (Number(today.dataset.dayIndex) + .5) / Number(panel.dataset.days);
      requestAnimationFrame(() => applyZoom(panel, zoom, null, null, ratio));
    }
    if (bulkSwapInProgress && returnView?.year === panel.dataset.year) {
      panel.classList.add('bulk-swap-smooth');
      scroll.scrollLeft = returnView.scrollLeft;
      scroll.scrollTop = returnView.scrollTop;
    }
    if (scroll._workWheelBound) return;
    scroll._workWheelBound = true;
    scroll.addEventListener('wheel', event => {
      if (event.shiftKey) {
        event.preventDefault();
        scroll.scrollLeft += event.deltaY || event.deltaX;
        return;
      }
      // Πάνω από τη σταθερή στήλη εργαζομένων διατηρείται το κανονικό scroll.
      const rect = scroll.getBoundingClientRect();
      if (event.clientX < rect.left + STICKY_WIDTH) return;
      event.preventDefault();
      if (!scroll._workWheelAnchor) {
        const timeline = panel.querySelector('[data-work-track]') ||
          panel.querySelector('.work-time-head');
        const timelineRect = timeline.getBoundingClientRect();
        const targetDay = event.target.closest('[data-work-day],[data-work-date]');
        let ratio = clamp(
          (event.clientX - timelineRect.left) / timelineRect.width, 0, 1
        );
        // Η ίδια η κυψέλη είναι ακριβέστερη άγκυρα από τη γεωμετρία ολόκληρου
        // του έτους, ειδικά όταν το πρώτο zoom αλλάζει το min-width του grid.
        if (targetDay) {
          const targetRect = targetDay.getBoundingClientRect();
          const targetDate = targetDay.dataset.date || targetDay.dataset.workDate;
          const start = Date.UTC(Number(panel.dataset.year), 0, 1);
          const dayIndex = Math.round((Date.parse(`${targetDate}T00:00:00Z`) - start) / DAY);
          const fraction = clamp(
            (event.clientX - targetRect.left) / targetRect.width, 0, 1
          );
          ratio = (dayIndex + fraction) / Number(panel.dataset.days);
        }
        scroll._workWheelAnchor = {
          ratio: clamp(ratio, 0, 1),
          clientX: event.clientX,
          clientY: event.clientY,
          timer: null
        };
      }
      const anchor = scroll._workWheelAnchor;
      clearTimeout(anchor.timer);
      // Trackpads/ομαλές ροδέλες μπορεί να έχουν μεγάλα κενά ανά event.
      // Κρατάμε την ίδια ημερομηνία-άγκυρα για ολόκληρη τη χειρονομία.
      anchor.timer = setTimeout(() => { scroll._workWheelAnchor = null; }, 800);
      const factor = Math.exp(-event.deltaY * .0035);
      applyZoom(
        panel, zoom * factor, anchor.clientX, anchor.clientY, anchor.ratio
      );
    }, {passive: false});
  }

  const timeText = minutes => {
    const value = Math.max(0, Math.round(minutes)) % (24 * 60);
    return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
  };

  function setupDayEditor(scope = document) {
    const editor = scope.matches?.('[data-work-day-editor]')
      ? scope : scope.querySelector?.('[data-work-day-editor]');
    if (!editor || editor._workEditorBound) return;
    editor._workEditorBound = true;
    const timelineStart = Number(editor.dataset.start);
    const timelineEnd = Number(editor.dataset.end);
    const step = Number(editor.dataset.step) || 15;
    const duration = timelineEnd - timelineStart;
    const splitGap = 3 * 60;
    const snap = value => Math.round(value / step) * step;
    const workedMinutes = (track, except = null) =>
      [...track.querySelectorAll('[data-shift-bar]')]
        .filter(bar => bar !== except)
        .reduce((total, bar) => total +
          Number(bar.dataset.end) - Number(bar.dataset.start), 0);

    const renderBar = bar => {
      const start = Number(bar.dataset.start);
      const end = Number(bar.dataset.end);
      bar.style.left = `${(start - timelineStart) * 100 / duration}%`;
      bar.style.width = `${(end - start) * 100 / duration}%`;
      bar.querySelector('span').textContent = `${timeText(start)} – ${timeText(end)}`;
    };

    const classifyTrack = track => {
      const bars = [...track.querySelectorAll('[data-shift-bar]')]
        .sort((a, b) => Number(a.dataset.start) - Number(b.dataset.start));
      const restMinimumStart = Number(track.dataset.minStart) || timelineStart;
      const restMaximumEnd = Number(track.dataset.maxEnd) || timelineEnd;
      track.style.setProperty(
        '--rest-previous-width',
        `${clamp(restMinimumStart - timelineStart, 0, duration) * 100 / duration}%`
      );
      track.style.setProperty(
        '--rest-following-left',
        `${clamp(restMaximumEnd - timelineStart, 0, duration) * 100 / duration}%`
      );
      let worked = 0;
      bars.forEach(bar => {
        const barDuration = Number(bar.dataset.end) - Number(bar.dataset.start);
        const normal = clamp(8 * 60 - worked, 0, barDuration);
        const overwork = Math.max(
          0, clamp(9 * 60 - worked, 0, barDuration) - normal
        );
        const normalEnd = barDuration ? normal * 100 / barDuration : 0;
        const overworkEnd = barDuration
          ? (normal + overwork) * 100 / barDuration : 0;
        bar.style.setProperty('--normal-end', `${normalEnd}%`);
        bar.style.setProperty('--overwork-end', `${overworkEnd}%`);
        bar.classList.toggle('has-overwork', overworkEnd > normalEnd);
        bar.classList.toggle('has-overtime', overworkEnd < 100);
        worked += barDuration;
      });
      track.classList.toggle('has-split-gap', bars.length === 2);
      if (bars.length === 2) {
        const gapStart = Number(bars[0].dataset.end);
        const gapEnd = Number(bars[1].dataset.start);
        track.style.setProperty(
          '--split-gap-left', `${(gapStart - timelineStart) * 100 / duration}%`
        );
        track.style.setProperty(
          '--split-gap-width', `${(gapEnd - gapStart) * 100 / duration}%`
        );
      }
      track.classList.toggle('over-maximum', worked > 13 * 60);
      track.title = worked > 13 * 60
        ? 'Μη επιτρεπτό: η ημερήσια εργασία υπερβαίνει τις 13 ώρες'
        : 'Double click για δημιουργία ωραρίου';
    };

    const warnSplitGap = track => {
      const now = performance.now();
      if (now - (track._lastSplitGapWarning || 0) < 700) return;
      track._lastSplitGapWarning = now;
      track.classList.remove('gap-warning');
      void track.offsetWidth;
      track.classList.add('gap-warning');
      clearTimeout(track._splitGapWarningTimer);
      track._splitGapWarningTimer = setTimeout(
        () => track.classList.remove('gap-warning'), 680
      );
    };

    const warnDailyRest = (track, side) => {
      const now = performance.now();
      if (now - (track._lastDailyRestWarning || 0) < 700) return;
      track._lastDailyRestWarning = now;
      const warningClass = side === 'previous'
        ? 'rest-warning-previous' : 'rest-warning-following';
      track.classList.remove(warningClass);
      void track.offsetWidth;
      track.classList.add(warningClass);
      clearTimeout(track._dailyRestWarningTimer);
      track._dailyRestWarningTimer = setTimeout(
        () => track.classList.remove(warningClass), 680
      );
    };

    const syncTrack = track => {
      const bars = [...track.querySelectorAll('[data-shift-bar]')]
        .sort((a, b) => Number(a.dataset.start) - Number(b.dataset.start));
      const value = bars.map(bar =>
        `${bar.dataset.telework === '1' ? 'Τ' : ''}${timeText(Number(bar.dataset.start))}-${timeText(Number(bar.dataset.end))}`
      ).join('+');
      track.querySelector('[data-schedule-input]').value = value;
      const label = track.previousElementSibling?.querySelector('[data-schedule-label]');
      if (label) label.textContent = value || 'ΡΕΠΟ';
      classifyTrack(track);
    };

    const updateCounts = () => {
      const slots = [...editor.querySelectorAll('[data-staff-slot]')];
      slots.forEach((slot, index) => {
        const slotStart = timelineStart + index * 60;
        slot.textContent = [...editor.querySelectorAll('[data-shift-track]')]
          .filter(track => [...track.querySelectorAll('[data-shift-bar]')]
            .some(bar => Number(bar.dataset.start) < slotStart + 60 &&
              Number(bar.dataset.end) > slotStart)).length;
      });
    };

    const createBar = (track, start, end, telework = false) => {
      const bar = document.createElement('div');
      bar.className = `work-shift-bar${telework ? ' telework' : ''}`;
      bar.dataset.shiftBar = '';
      bar.dataset.start = start;
      bar.dataset.end = end;
      bar.dataset.telework = telework ? '1' : '0';
      bar.innerHTML = '<i class="work-shift-handle left" data-shift-resize="start"></i><span></span><i class="work-shift-handle right" data-shift-resize="end"></i>';
      track.append(bar);
      renderBar(bar);
      syncTrack(track);
      updateCounts();
      bar.animate(
        [{opacity: 0, transform: 'scaleX(.7)'}, {opacity: 1, transform: 'scaleX(1)'}],
        {duration: 180, easing: 'ease-out'}
      );
      return bar;
    };

    const contextMenu = document.createElement('div');
    contextMenu.className = 'work-shift-menu';
    contextMenu.setAttribute('role', 'menu');
    contextMenu.hidden = true;
    contextMenu.innerHTML = `
      <button type="button" role="menuitem" data-shift-menu-action="regular">
        <i class="regular"></i><span>Εργασία</span>
      </button>
      <button type="button" role="menuitem" data-shift-menu-action="telework">
        <i class="telework"></i><span>Τηλεργασία</span>
      </button>
      <button type="button" role="menuitem" class="danger" data-shift-menu-action="delete">
        <i class="delete"></i><span>Διαγραφή</span>
      </button>`;
    editor.append(contextMenu);
    let contextBar = null;

    const hideContextMenu = () => {
      contextMenu.hidden = true;
      contextBar = null;
    };

    const showContextMenu = (bar, clientX, clientY) => {
      contextBar = bar;
      const telework = bar.dataset.telework === '1';
      contextMenu.querySelector('[data-shift-menu-action="regular"]')
        .classList.toggle('active', !telework);
      contextMenu.querySelector('[data-shift-menu-action="telework"]')
        .classList.toggle('active', telework);
      contextMenu.hidden = false;
      const rect = contextMenu.getBoundingClientRect();
      contextMenu.style.left = `${clamp(clientX, 8, window.innerWidth - rect.width - 8)}px`;
      contextMenu.style.top = `${clamp(clientY, 8, window.innerHeight - rect.height - 8)}px`;
      contextMenu.querySelector('button.active')?.focus({preventScroll: true});
    };

    editor.addEventListener('contextmenu', event => {
      const bar = event.target.closest('[data-shift-bar]');
      if (!bar) {
        hideContextMenu();
        return;
      }
      event.preventDefault();
      showContextMenu(bar, event.clientX, event.clientY);
    });

    editor.addEventListener('click', event => {
      const control = event.target.closest('[data-shift-menu-action]');
      if (!control || !contextBar) {
        if (!event.target.closest('.work-shift-menu')) hideContextMenu();
        return;
      }
      const bar = contextBar;
      const track = bar.closest('[data-shift-track]');
      const action = control.dataset.shiftMenuAction;
      hideContextMenu();
      if (action === 'delete') {
        const remove = () => {
          if (!bar.isConnected) return;
          bar.remove();
          syncTrack(track);
          updateCounts();
        };
        const animation = bar.animate(
          [{opacity: 1, transform: 'scale(1)'},
           {opacity: 0, transform: 'scale(.55)', filter: 'brightness(1.8)'}],
          {duration: 190, easing: 'ease-in'}
        );
        animation.finished.then(remove, remove);
        return;
      }
      const telework = action === 'telework';
      bar.dataset.telework = telework ? '1' : '0';
      bar.classList.toggle('telework', telework);
      syncTrack(track);
      bar.animate(
        [{filter: 'brightness(1.65)', transform: 'scaleY(1.12)'},
         {filter: 'brightness(1)', transform: 'scaleY(1)'}],
        {duration: 260, easing: 'ease-out'}
      );
    });

    contextMenu.addEventListener('keydown', event => {
      if (event.key === 'Escape') hideContextMenu();
    });
    editor.querySelector('.work-day-body')?.addEventListener(
      'scroll', hideContextMenu, {passive: true}
    );

    editor.addEventListener('dblclick', event => {
      const track = event.target.closest('[data-shift-track]');
      if (!track || event.target.closest('[data-shift-bar]')) return;
      const existingBars = [...track.querySelectorAll('[data-shift-bar]')];
      if (existingBars.length >= 2) {
        toast('Επιτρέπονται έως δύο τμήματα στο σπαστό ωράριο', true);
        return;
      }
      const remaining = 13 * 60 - workedMinutes(track);
      if (remaining < step) {
        toast('Το ημερήσιο όριο των 13 ωρών έχει συμπληρωθεί', true);
        return;
      }
      const rect = track.getBoundingClientRect();
      let start = snap(timelineStart + (event.clientX - rect.left) * duration / rect.width);
      if (start >= 24 * 60) {
        toast('Η έναρξη πρέπει να ανήκει στην επιλεγμένη ημέρα', true);
        return;
      }
      const length = Math.min(8 * 60, remaining);
      let end = Math.min(timelineEnd, start + length);
      start = Math.max(timelineStart, end - length);
      const candidateStarts = existingBars.map(bar => Number(bar.dataset.start));
      const candidateEnds = existingBars.map(bar => Number(bar.dataset.end));
      const earliestStart = Math.min(start, ...candidateStarts);
      const latestEnd = Math.max(end, ...candidateEnds);
      const restMinimumStart = Number(track.dataset.minStart) || timelineStart;
      const restMaximumEnd = Number(track.dataset.maxEnd) || timelineEnd;
      if (earliestStart < restMinimumStart) {
        warnDailyRest(track, 'previous');
        toast('Απαιτούνται 11 ώρες ανάπαυσης από την προηγούμενη βάρδια', true);
        return;
      }
      if (latestEnd > restMaximumEnd) {
        warnDailyRest(track, 'following');
        toast('Απαιτούνται 11 ώρες ανάπαυσης πριν από την επόμενη βάρδια', true);
        return;
      }
      if (existingBars.length) {
        const existingStart = Number(existingBars[0].dataset.start);
        const existingEnd = Number(existingBars[0].dataset.end);
        const gap = end <= existingStart
          ? existingStart - end
          : start >= existingEnd ? start - existingEnd : -1;
        if (gap < splitGap) {
          toast('Το κενό ανάμεσα στα δύο τμήματα πρέπει να είναι τουλάχιστον 3 ώρες', true);
          return;
        }
      }
      createBar(track, start, end);
    });

    editor.addEventListener('pointerdown', event => {
      const bar = event.target.closest('[data-shift-bar]');
      if (!bar || event.button !== 0) return;
      event.preventDefault();
      const track = bar.closest('[data-shift-track]');
      const rect = track.getBoundingClientRect();
      const action = event.target.dataset.shiftResize || 'move';
      const sibling = [...track.querySelectorAll('[data-shift-bar]')]
        .find(candidate => candidate !== bar);
      const siblingStart = sibling ? Number(sibling.dataset.start) : null;
      const siblingEnd = sibling ? Number(sibling.dataset.end) : null;
      const sortedBars = [...track.querySelectorAll('[data-shift-bar]')]
        .sort((a, b) => Number(a.dataset.start) - Number(b.dataset.start));
      const isFirstBar = sortedBars[0] === bar;
      const isLastBar = sortedBars[sortedBars.length - 1] === bar;
      const splitMinimumStart = sibling && Number(bar.dataset.start) > siblingStart
        ? siblingEnd + splitGap : timelineStart;
      const splitMaximumEnd = sibling && Number(bar.dataset.start) < siblingStart
        ? siblingStart - splitGap : timelineEnd;
      const restMinimumStart = Number(track.dataset.minStart) || timelineStart;
      const restMaximumEnd = Number(track.dataset.maxEnd) || timelineEnd;
      const origin = {
        x: event.clientX,
        start: Number(bar.dataset.start),
        end: Number(bar.dataset.end),
        maxLength: Math.max(step, 13 * 60 - workedMinutes(track, bar)),
        splitMinimumStart,
        splitMaximumEnd,
        restMinimumStart,
        restMaximumEnd,
        hasPreviousRestLimit: restMinimumStart > timelineStart,
        hasFollowingRestLimit: restMaximumEnd < timelineEnd,
        minimumStart: Math.max(
          splitMinimumStart, isFirstBar ? restMinimumStart : timelineStart
        ),
        maximumEnd: Math.min(
          splitMaximumEnd, isLastBar ? restMaximumEnd : timelineEnd
        ),
        isFirstBar,
        isLastBar
      };
      bar.classList.add('dragging');
      bar.setPointerCapture?.(event.pointerId);

      const move = moveEvent => {
        const delta = snap((moveEvent.clientX - origin.x) * duration / rect.width);
        const rawStart = origin.start + delta;
        const rawEnd = origin.end + delta;
        const isLaterBar = sibling && origin.splitMinimumStart > timelineStart;
        const isEarlierBar = sibling && origin.splitMaximumEnd < timelineEnd;
        const splitGapBlocked = (
          action === 'start' && isLaterBar && rawStart < origin.splitMinimumStart
        ) || (
          action === 'end' && isEarlierBar && rawEnd > origin.splitMaximumEnd
        ) || (
          action === 'move' && (
            (isLaterBar && rawStart < origin.splitMinimumStart) ||
            (isEarlierBar && rawEnd > origin.splitMaximumEnd)
          )
        );
        const previousRestBlocked = origin.hasPreviousRestLimit && origin.isFirstBar && (
          action === 'start' || action === 'move'
        ) && rawStart < origin.restMinimumStart;
        const followingRestBlocked = origin.hasFollowingRestLimit && origin.isLastBar && (
          action === 'end' || action === 'move'
        ) && rawEnd > origin.restMaximumEnd;
        let start = origin.start;
        let end = origin.end;
        if (action === 'start') {
          start = clamp(
            origin.start + delta,
            Math.max(origin.minimumStart, end - origin.maxLength),
            Math.min(end - step, 24 * 60 - step)
          );
        } else if (action === 'end') {
          end = clamp(
            origin.end + delta, start + step,
            Math.min(origin.maximumEnd, start + origin.maxLength)
          );
        } else {
          const length = end - start;
          start = clamp(
            origin.start + delta, origin.minimumStart,
            Math.min(origin.maximumEnd - length, 24 * 60 - step)
          );
          end = start + length;
        }
        bar.dataset.start = start;
        bar.dataset.end = end;
        renderBar(bar);
        classifyTrack(track);
        if (splitGapBlocked) warnSplitGap(track);
        if (previousRestBlocked) warnDailyRest(track, 'previous');
        if (followingRestBlocked) warnDailyRest(track, 'following');
      };
      const finish = () => {
        bar.classList.remove('dragging');
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', finish);
        syncTrack(track);
        updateCounts();
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', finish, {once: true});
    });
    editor.querySelectorAll('[data-shift-track]').forEach(classifyTrack);
  }

  document.addEventListener('DOMContentLoaded', () => {
    setupPlanner();
    setupDayEditor();
  });
  document.body.addEventListener('htmx:afterSwap', event => {
    setupPlanner(event.target);
    setupDayEditor(event.target);
  });
  document.body.addEventListener('htmx:afterSettle', event => {
    const settledTarget = event.detail?.target || event.target;
    // Το άνοιγμα/η πλοήγηση του modal κάνει επίσης settle στο #modal-host.
    // Η αποθηκευμένη προβολή πρέπει να καταναλωθεί μόνο όταν αντικατασταθεί
    // το ίδιο το κεντρικό panel από επιτυχημένη αποθήκευση.
    if (!settledTarget?.matches?.('#work-schedule-panel')) return;
    const panel = document.querySelector('[data-work-schedule-planner]');
    if (!panel || !returnView || restoringView ||
        returnView.year !== panel.dataset.year ||
        document.querySelector('[data-work-day-editor]')) return;
    restoringView = true;
    const savedView = returnView;
    const scroll = panel.querySelector('[data-work-scroll]');
    // Το HTMX ολοκληρώνει εδώ και την εφαρμογή των attributes του response.
    // Επαναφέρουμε μετά το settle ώστε το inline πλάτος 850px να μην μπορεί
    // να αντικαταστήσει ξανά το προηγούμενο zoom.
    applyZoom(panel, savedView.zoom);
    const restorePosition = () => {
      scroll.scrollLeft = savedView.scrollLeft;
      scroll.scrollTop = savedView.scrollTop;
      window.scrollTo(savedView.windowX, savedView.windowY);
    };
    // Τα μεγάλα εβδομαδιαία grids αποκτούν το τελικό scrollWidth λίγο μετά
    // το πρώτο layout. Η τελευταία επανάληψη αποφεύγει clamp στο προσωρινό max.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      restorePosition();
      setTimeout(restorePosition, 60);
      setTimeout(() => {
        restorePosition();
        returnView = null;
        restoringView = false;
        bulkSwapInProgress = false;
      }, 180);
    }));
  });

  document.addEventListener('click', event => {
    if (event.target.closest('[data-work-day-cancel]')) {
      returnView = null;
    }
    const control = event.target.closest(
      '[data-work-zoom-in],[data-work-zoom-out],[data-work-zoom-reset]'
    );
    if (!control) return;
    const panel = control.closest('[data-work-schedule-planner]');
    if (control.matches('[data-work-zoom-in]')) {
      applyZoom(panel, zoom * BUTTON_FACTOR);
    } else if (control.matches('[data-work-zoom-out]')) {
      applyZoom(panel, zoom / BUTTON_FACTOR);
    } else {
      applyZoom(panel, 1);
    }
  });

  document.addEventListener('contextmenu', event => {
    const week = event.target.closest('[data-work-week]');
    if (!week) {
      hideWeekContextMenu();
      return;
    }
    event.preventDefault();
    showWeekContextMenu(week, event.clientX, event.clientY);
  });

  document.addEventListener('click', event => {
    // Ένα διπλό κλικ παράγει δύο click events· η ενέργεια εκτελείται μόνο μία φορά.
    if (event.detail > 1) return;
    const weekAction = event.target.closest('[data-work-week-action]');
    if (weekAction && weekMenuTarget) {
      const week = weekMenuTarget;
      const panel = week.closest('[data-work-schedule-planner]');
      const action = weekAction.dataset.workWeekAction;
      if (action === 'copy') {
        copiedWeek = {
          start: week.dataset.weekStart,
          label: week.title.replace(/^Κλικ:\s*/, '')
        };
        hideWeekContextMenu(panel);
        toast(`Αντιγράφηκε η ${copiedWeek.label}`);
        return;
      }
      if (action === 'paste' && copiedWeek) {
        const targetStart = week.dataset.weekStart;
        hideWeekContextMenu(panel);
        const panelScroll = panel.querySelector('[data-work-scroll]');
        returnView = {
          year: panel.dataset.year,
          zoom,
          scrollLeft: panelScroll.scrollLeft,
          scrollTop: panelScroll.scrollTop,
          windowX: window.scrollX,
          windowY: window.scrollY
        };
        bulkSwapInProgress = true;
        htmx.ajax('POST', panel.dataset.weekPasteUrl, {
          target: '#work-schedule-panel',
          swap: 'outerHTML transition:true',
          values: {
            year: panel.dataset.year,
            source_start: copiedWeek.start,
            target_start: targetStart
          }
        });
        return;
      }
    }
    if (!event.target.closest('[data-work-week-menu]')) hideWeekContextMenu();

    const bulkClear = event.target.closest('[data-work-bulk-clear]');
    const bulkAction = event.target.closest('[data-work-bulk-action]');
    if (bulkClear || bulkAction) {
      const panel = event.target.closest('[data-work-schedule-planner]');
      if (bulkClear) {
        clearBulkSelection(panel);
        return;
      }
      if (!bulkSelection.size) return;
      const toolbar = bulkAction.closest('[data-work-bulk-toolbar]');
      const action = bulkAction.dataset.workBulkAction;
      // «Άδεια…» → το modal νέας άδειας με το εύρος των επιλεγμένων ημερών.
      if (action === 'leave') {
        const cells = [...bulkSelection];
        const tracks = new Set(cells.map(cell => cell.closest('[data-work-track]')));
        if (tracks.size !== 1) {
          toast('Επιλέξτε ημέρες ενός μόνο εργαζομένου για νέα άδεια', true);
          return;
        }
        const dates = cells.map(cell => cell.dataset.date).sort();
        const track = [...tracks][0];
        rememberView(panel);
        clearBulkSelection(panel);
        htmx.ajax('GET', track.dataset.leaveUrl, {
          target: '#modal-host', swap: 'innerHTML',
          values: {date: dates[0], date_to: dates[dates.length - 1],
                   year: panel.dataset.year, back: 'work'}
        });
        return;
      }
      const hours = toolbar.querySelector('[data-work-bulk-hours]').value.trim();
      if (action === 'save' && !hours) {
        toast('Συμπληρώστε το ωράριο της μαζικής μεταβολής', true);
        return;
      }
      const selection = [...bulkSelection].map(cell => ({
        employment_id: cell.closest('[data-work-track]').dataset.employmentId,
        date: cell.dataset.date
      }));
      const panelScroll = panel.querySelector('[data-work-scroll]');
      returnView = {
        year: panel.dataset.year,
        zoom,
        scrollLeft: panelScroll.scrollLeft,
        scrollTop: panelScroll.scrollTop,
        windowX: window.scrollX,
        windowY: window.scrollY
      };
      toolbar.classList.add('saving');
      bulkSwapInProgress = true;
      // «Ακύρωση αλλαγών» → επαναφορά στη δηλωμένη τιμή ΕΡΓΑΝΗ (revert)
      if (action === 'revert') {
        htmx.ajax('POST', panel.dataset.ergRevertUrl, {
          target: '#work-schedule-panel',
          swap: 'outerHTML transition:true',
          values: {year: panel.dataset.year, cells: JSON.stringify(selection)}
        });
        return;
      }
      htmx.ajax('POST', panel.dataset.bulkUrl, {
        target: '#work-schedule-panel',
        swap: 'outerHTML transition:true',
        values: {
          year: panel.dataset.year,
          action,
          hours,
          selection: JSON.stringify(selection)
        }
      });
      return;
    }

    const cell = event.target.closest('[data-work-day]');
    if (cell && (event.ctrlKey || event.metaKey || event.shiftKey)) {
      event.preventDefault();
      const panel = cell.closest('[data-work-schedule-planner]');
      if (cell.dataset.active !== '1') {
        toast('Ο εργαζόμενος δεν εργάζεται τη συγκεκριμένη ημερομηνία', true);
        return;
      }
      if (isClosedCell(cell)) {
        closedToast(panel);
        return;
      }
      if (event.shiftKey && bulkAnchor?.isConnected) {
        selectBulkRange(panel, bulkAnchor, cell, event.ctrlKey || event.metaKey);
      } else {
        setBulkSelected(cell, !bulkSelection.has(cell));
        bulkAnchor = cell;
        updateBulkSelection(panel);
      }
      return;
    }

    // Ctrl/Shift + κλικ σε εβδομάδα = ΕΠΙΛΟΓΗ διαδοχικών εβδομάδων για υποβολή
    // στο ΕΡΓΑΝΗ (χωρίς modifier → zoom, όπως πριν).
    const weekHead = event.target.closest('[data-work-week]');
    if (weekHead && (event.ctrlKey || event.metaKey || event.shiftKey)) {
      event.preventDefault();
      const panel = weekHead.closest('[data-work-schedule-planner]');
      const allWeeks = [...panel.querySelectorAll('[data-work-week]')];
      const ws = weekHead.dataset.weekStart;
      if (event.shiftKey && weekAnchor) {
        const a = allWeeks.findIndex(w => w.dataset.weekStart === weekAnchor);
        const b = allWeeks.indexOf(weekHead);
        if (a >= 0 && b >= 0) {
          if (!(event.ctrlKey || event.metaKey)) weekSelection.clear();
          for (let i = Math.min(a, b); i <= Math.max(a, b); i++) {
            weekSelection.add(allWeeks[i].dataset.weekStart);
          }
        }
      } else {
        if (weekSelection.has(ws)) weekSelection.delete(ws);
        else weekSelection.add(ws);
        weekAnchor = ws;
      }
      updateWeekSelection(panel);
      return;
    }

    const range = event.target.closest('[data-work-range]');
    if (range) {
      zoomRange(
        range.closest('[data-work-schedule-planner]'),
        Number(range.dataset.startIndex), Number(range.dataset.endIndex),
        event.clientY
      );
      return;
    }
    const dayHead = event.target.closest('[data-work-date]');
    if (!cell && !dayHead) return;
    if (bulkSelection.size) clearBulkSelection((cell || dayHead).closest('[data-work-schedule-planner]'));
    if (cell && cell.dataset.active !== '1') {
      toast('Ο εργαζόμενος δεν εργάζεται τη συγκεκριμένη ημερομηνία', true);
      return;
    }
    const panel = (cell || dayHead).closest('[data-work-schedule-planner]');
    // Κλεισμένη περίοδος: ούτε καρτέλα ημέρας ούτε μαζική επεξεργασία
    if (isClosedCell(cell) || isClosedCell(dayHead)) {
      closedToast(panel);
      return;
    }
    rememberView(panel);
    // Ημέρα με άδεια → επεξεργασία της άδειας (όχι η καρτέλα ημέρας)
    if (cell?.dataset.leaveModalUrl) {
      htmx.ajax('GET', cell.dataset.leaveModalUrl, {target: '#modal-host', swap: 'innerHTML'});
      return;
    }
    const url = new URL(panel.dataset.dayUrl, window.location.origin);
    url.searchParams.set('date', (cell || dayHead).dataset.date || dayHead.dataset.workDate);
    url.searchParams.set('year', panel.dataset.year);
    if (cell) {
      url.searchParams.set('focus', cell.closest('[data-work-track]').dataset.employmentId);
    }
    htmx.ajax('GET', url.toString(), {target: '#modal-host', swap: 'innerHTML'});
  });

  // Toolbar επιλεγμένων εβδομάδων — καθαρισμός ή μαζική υποβολή στο ΕΡΓΑΝΗ.
  document.addEventListener('click', event => {
    const clear = event.target.closest('[data-erg-week-clear]');
    const submit = event.target.closest('[data-erg-week-submit]');
    if (!clear && !submit) return;
    const panel = event.target.closest('[data-work-schedule-planner]') ||
      document.querySelector('[data-work-schedule-planner]');
    if (!panel) return;
    if (clear) {
      clearWeekSelection(panel);
      return;
    }
    if (!weekSelection.size) return;
    const weeks = [...weekSelection];
    const scroll = panel.querySelector('[data-work-scroll]');
    returnView = {
      year: panel.dataset.year,
      zoom,
      scrollLeft: scroll.scrollLeft,
      scrollTop: scroll.scrollTop,
      windowX: window.scrollX,
      windowY: window.scrollY
    };
    bulkSwapInProgress = true;
    weekSelection.clear();
    weekAnchor = null;
    htmx.ajax('POST', panel.dataset.ergWeeksUrl, {
      target: '#work-schedule-panel',
      swap: 'outerHTML transition:true',
      values: {year: panel.dataset.year, weeks: JSON.stringify(weeks)}
    });
  });
})();

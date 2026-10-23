/* Επεξεργαστής «Πρόγραμμα ημέρας» (modal των Ζωντανών): συρόμενες μπάρες
   βαρδιών ανά εργαζόμενο, βήμα 15', έλεγχοι ανάπαυσης. */
(() => {
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

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
      // ημερήσια όρια από τις παραμέτρους ωραρίου (βλ. work_schedules._day_context)
      const overworkFrom = Number(track.dataset.overworkFrom) || 8 * 60;
      const overtimeFrom = Number(track.dataset.overtimeFrom) || 9 * 60;
      let worked = 0;
      bars.forEach(bar => {
        const barDuration = Number(bar.dataset.end) - Number(bar.dataset.start);
        const normal = clamp(overworkFrom - worked, 0, barDuration);
        const overwork = Math.max(
          0, clamp(overtimeFrom - worked, 0, barDuration) - normal
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

  document.addEventListener('DOMContentLoaded', () => setupDayEditor());
  document.body.addEventListener('htmx:afterSwap', event => setupDayEditor(event.target));
})();

/* Ζωντανή εικόνα ημέρας (/live) — ΟΜΑΛΗ κύλιση του grid με τον χρόνο.
 *
 * ΣΥΣΤΗΜΑ ΣΥΝΤΕΤΑΓΜΕΝΩΝ: ο server δίνει ΟΛΕΣ τις θέσεις σε ΑΠΟΛΥΤΑ ΛΕΠΤΑ από
 * τα μεσάνυχτα της `base` του παραθύρου (--s / --d)· εδώ ορίζονται μόνο τρεις
 * μεταβλητές πάνω στο board:
 *   --ppm    pixel ανά λεπτό  (εύρος προβολής / πλάτος λωρίδας)
 *   --shift  μετατόπιση της λωρίδας ώστε το `center` να πέφτει στο ΚΕΝΤΡΟ
 *   --nowx   η θέση της ΤΡΕΧΟΥΣΑΣ ΩΡΑΣ μέσα στη λωρίδα (δείκτης + σκίαση
 *            του παρελθόντος — ΜΙΑ πηγή και για τα δύο)
 *
 * ΟΜΑΛΟΤΗΤΑ — ΜΟΝΤΕΛΟ «ΣΤΟΧΟΥ + ΕΛΑΤΗΡΙΟΥ»: κάθε είσοδος μετακινεί τον ΣΤΟΧΟ
 * (`view.target`) και το ορατό `view.center` τον κυνηγά με κρίσιμα
 * αποσβεσμένο ελατήριο (OMEGA/DAMP, ανεξάρτητο από τον ρυθμό καρέ). Έτσι ΚΑΘΕ
 * κύλιση αριστερά/δεξιά ξεκινά απαλά (ΕΠΙΤΑΧΥΝΣΗ) και σβήνει απαλά
 * (ΕΠΙΒΡΑΔΥΝΣΗ):
 *   - ρόδα/trackpad: τα διακριτά γεγονότα συσσωρεύονται στον στόχο αντί να
 *     τραβούν την εικόνα βήμα-βήμα,
 *   - απελευθέρωση συρσίματος: ο στόχος προβάλλεται μπροστά κατά την
 *     ταχύτητα του χεριού (FLING) και το ελατήριο τον φτάνει σβήνοντας,
 *   - κουμπιά ημέρας / επιστροφή στο «τώρα»: easeInOutCubic,
 *   - σύρσιμο: 1:1 με το χέρι (ο άμεσος χειρισμός ΔΕΝ εξομαλύνεται),
 *   - ζωντανή κύλιση: ακριβής ώρα με δευτερόλεπτα, συνεχής κίνηση,
 *   - ΖΟΥΜ (ημέρα/εβδομάδα/μήνας/έτος): το ΕΥΡΟΣ της λωρίδας (`view.zoom`,
 *     λεπτά) ταξιδεύει ΓΕΩΜΕΤΡΙΚΑ προς το νέο επίπεδο — βλ. `setZoom`.
 *
 * ΠΑΡΑΘΥΡΟ ΗΜΕΡΩΝ: το grid δεν σταματά στα μεσάνυχτα — μόλις η μετακίνηση
 * πλησιάσει την άκρη, ζητείται νέο παράθυρο (`/live/rows?base=…&days=…`) και
 * το `center` προσαρμόζεται στη νέα `base`, οπότε η εικόνα δεν αναπηδά.
 * Η ίδια διαδρομή κάνει και την ΑΝΑΝΕΩΣΗ κάθε λεπτό (νέα χτυπήματα κάρτας).
 */
(() => {
  // ΕΠΙΠΕΔΑ ZOOM: ημέρα · εβδομάδα · μήνας. Κάθε επίπεδο ορίζει ΚΑΙ το
  // παράθυρο δεδομένων που ζητείται από τον server (`days`) ΚΑΙ το βήμα
  // μετακύλισής του (`page`, ημέρες) — αλλιώς στον μήνα θα ζητούσαμε νέο
  // παράθυρο σε κάθε ημέρα μετακίνησης.
  // ⚠️ Στην προβολή ΕΤΟΥΣ ο server δεν στέλνει ημερήσιες μπάρες αλλά
  // ΕΒΔΟΜΑΔΙΑΙΑ ΣΥΝΟΨΗ (queries.AGG_MIN_DAYS) — μια ημέρα θα ήταν ~2px.
  const SPANS = [
    { h: 24, key: "day", days: 3, page: 1, grid: [60, 1440], refresh: 60000 },
    { h: 24 * 7, key: "week", days: 15, page: 4, grid: [360, 1440],
      refresh: 60000 },
    { h: 24 * 30, key: "month", days: 61, page: 15, grid: [1440, 10080],
      refresh: 300000 },
    // grid [0,0] = ΚΑΝΕΝΑ κατακόρυφο πλέγμα: στο έτος η ίδια η εικόνα είναι
    // κάθετες γραμμές ημερών — εβδομαδιαίες γραμμές υποβάθρου θα ήταν θόρυβος
    { h: 24 * 365, key: "year", days: 548, page: 90, grid: [0, 0],
      refresh: 900000 },
  ];
  const KEY = "live-span";           // προτίμηση χρήστη (τοπικά)
  const DAY = 24 * 60;
  // ΖΟΥΜ: το εύρος της λωρίδας δεν αλλάζει απότομα — ταξιδεύει ΓΕΩΜΕΤΡΙΚΑ
  // (ημέρα→εβδομάδα→μήνας→έτος είναι λόγοι, όχι διαφορές), οπότε η κίνηση
  // γίνεται αντιληπτή ως σταθερό «τράβηγμα φακού». Η διάρκεια μεγαλώνει
  // λογαριθμικά με τον λόγο: ημέρα→εβδομάδα (x7) κοντά στο ZOOM_MIN_MS,
  // ημέρα→έτος (x365) κοντά στο ZOOM_MAX_MS.
  const ZOOM_MIN_MS = 340;
  const ZOOM_MAX_MS = 780;
  // Πόσο περιμένουμε το ΝΕΟ παράθυρο δεδομένων πριν ξεκινήσει το ζουμ ΕΞΩ
  // (αλλιώς η λωρίδα θα «μίκραινε» μέσα σε κενό χώρο μέχρι να έρθει).
  const ZOOM_WAIT_MS = 280;
  // ΕΛΑΤΗΡΙΟ ΚΙΝΗΣΗΣ (κρίσιμα αποσβεσμένο): OMEGA = πόσο «σφιχτό» είναι το
  // κυνήγι του στόχου (rad/s — κάθισμα ~0,6΄΄), DAMP = συντελεστής απόσβεσης
  // (2 = κρίσιμη· λίγο πάνω από 2 σβήνει τυχόν υπέρβαση).
  const OMEGA = 9;
  const DAMP = 2.1;
  // Πόσο ΜΠΡΟΣΤΑ προβάλλεται ο στόχος με την ταχύτητα του χεριού όταν
  // αφήνεις το σύρσιμο (δευτ.) — αυτό δίνει την «αδράνεια».
  const FLING = 0.38;
  const DOW = ["Δε", "Τρ", "Τε", "Πε", "Πα", "Σα", "Κυ"];
  // ΣΦΥΓΜΟΣ ΨΗΦΙΑΚΗΣ ΚΑΡΤΑΣ: κάθε πόσο ρωτάμε αν καταγράφηκε νέα διέλευση.
  // Είναι μια ΜΙΚΡΗ απάντηση (ένας αριθμός) — γι' αυτό επιτρέπεται τόσο
  // συχνά· το βαρύ παράθυρο γραμμών κατεβαίνει ΜΟΝΟ όταν ο αριθμός αλλάξει.
  const PULSE_MS = 5000;

  const view = {
    center: null,    // λεπτά από τη base που βρίσκονται στο ΚΕΝΤΡΟ (ό,τι ΒΛΕΠΕΙΣ)
    target: null,    // πού ΘΕΛΕΙ να πάει — το `center` το κυνηγά εκθετικά
    ts: 0,           // χρόνος προηγούμενου καρέ (για dt)
    free: false,     // ξεκλείδωτη (παγωμένη) προβολή
    vel: 0,          // ταχύτητα του ΚΕΝΤΡΟΥ (λεπτά/δευτ.) — την οδηγεί το ελατήριο
    v: 0,            // ταχύτητα του ΧΕΡΙΟΥ στο σύρσιμο (λεπτά ανά ms)
    drag: null,      // {from, x0, t, x} όσο κρατιέται το ποντίκι
    anim: null,      // {from, to, t0, dur} ομαλή μετάβαση
    zoom: null,      // ΟΡΑΤΟ εύρος λωρίδας σε λεπτά (κινείται στο ζουμ)
    zoomAnim: null,  // {from, to, t0, dur} γεωμετρική μετάβαση εύρους
    warping: false,  // σβήσιμο/άλμα/εμφάνιση (επιστροφή από μακριά)
    loading: false,
    lastMin: -1,
  };

  const board = () => document.getElementById("lboard");
  const win = () => document.querySelector("#lb-rows .lb-win");

  /** Το ενεργό επίπεδο zoom (αντικείμενο του SPANS). */
  function zoomLevel() {
    const v = parseInt(localStorage.getItem(KEY), 10);
    return SPANS.find((s) => s.h === v) || SPANS[0];
  }

  /** Ώρες που χωρά η λωρίδα στο ΕΝΕΡΓΟ (λογικό) zoom. */
  function span() {
    return zoomLevel().h;
  }

  /** ΛΕΠΤΑ που χωρά η λωρίδα ΑΥΤΟ ΤΟ ΚΑΡΕ — διαφέρει από το `span()` όσο
   *  τρέχει η μετάβαση ζουμ. ΟΛΗ η γεωμετρία (--ppm, ετικέτα άκρης) διαβάζει
   *  αυτό· οι αποφάσεις (παράθυρο δεδομένων, βήμα κουμπιών) το λογικό. */
  function spanMin() {
    return view.zoom || span() * 60;
  }

  /** Το επίπεδο που ΦΑΙΝΕΤΑΙ τώρα (πλησιέστερο σε λογαριθμική απόσταση):
   *  στη μετάβαση η πυκνότητα χάρακα/πλέγματος αλλάζει στη μέση της κίνησης,
   *  όχι με το πάτημα του κουμπιού. */
  function shownLevel(mins) {
    let best = SPANS[0], bd = Infinity;
    SPANS.forEach((s) => {
      const d = Math.abs(Math.log(mins / (s.h * 60)));
      if (d < bd) { bd = d; best = s; }
    });
    return best;
  }

  /** Πόσο κοντά στην άκρη του παραθύρου ζητάμε νέο (μισή οθόνη). */
  function edgeMargin() {
    return span() * 60 * 0.55;
  }

  function baseDate(w) {
    const [y, m, d] = (w.dataset.base || "").split("-").map(Number);
    return new Date(y, m - 1, d);
  }

  /** Λεπτά (από τη base του παραθύρου) της ΠΡΑΓΜΑΤΙΚΗΣ τρέχουσας στιγμής.
   *  Σε ιστορική ημέρα δεν υπάρχει «τώρα»: κεντράρουμε στο μεσημέρι της
   *  ημέρας αναφοράς και ο δείκτης κρύβεται από το CSS (.nolive). */
  function nowMinutes(el, w) {
    const base = baseDate(w);
    if (el.dataset.live !== "1") {
      const [y, m, d] = el.dataset.day.split("-").map(Number);
      return (new Date(y, m - 1, d) - base) / 60000 + 12 * 60;
    }
    return (Date.now() - base) / 60000;
  }

  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
  function pad(n) { return String(n).padStart(2, "0"); }
  function ease(t) {  // easeInOutCubic
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  /** Ομαλή μετάβαση του κέντρου σε νέα θέση (λεπτά). */
  function glide(to, dur) {
    view.anim = { from: view.center, to, t0: performance.now(), dur: dur || 650 };
    view.v = 0;
  }

  // --------------------------------------------------------------- render
  function render(ts) {
    const el = board(), w = win();
    if (!el || !w) return;
    const lane = el.querySelector(".lb-lane");
    const width = lane ? lane.clientWidth : 0;
    if (!width) return;
    const total = parseFloat(w.dataset.span) || DAY;
    // ΖΟΥΜ: το ορατό εύρος ταξιδεύει ΓΕΩΜΕΤΡΙΚΑ προς τον στόχο (ίδιος λόγος
    // ανά καρέ), με easeInOutCubic — έτσι η κίνηση φαίνεται ομοιόμορφη σε
    // κάθε κλίμακα και ξεκινά/σβήνει απαλά, όπως το ελατήριο της κύλισης.
    if (view.zoomAnim) {
      const z = view.zoomAnim;
      const t = clamp((ts - z.t0) / z.dur, 0, 1);
      view.zoom = z.from * Math.pow(z.to / z.from, ease(t));
      if (t >= 1) {
        view.zoom = z.to;
        view.zoomAnim = null;
        el.classList.remove("zooming");
      }
    }
    const mins = spanMin();
    const ppm = width / mins;
    const now = nowMinutes(el, w);

    // --- πού βρίσκεται το κέντρο αυτό το καρέ
    // ΜΟΝΤΕΛΟ ΚΙΝΗΣΗΣ: κάθε είσοδος (ρόδα, απελευθέρωση συρσίματος, κουμπιά)
    // μετακινεί τον ΣΤΟΧΟ· το ορατό κέντρο τον κυνηγά με ΕΛΑΤΗΡΙΟ, άρα κάθε
    // μετακίνηση ξεκινά απαλά (ΕΠΙΤΑΧΥΝΣΗ) και σβήνει απαλά (ΕΠΙΒΡΑΔΥΝΣΗ),
    // ανεξάρτητα από το πόσο απότομη ήταν η είσοδος.
    if (view.center === null) { view.center = now; view.target = now; }
    const dt = clamp(ts - (view.ts || ts), 1, 64);
    view.ts = ts;
    if (view.anim) {
      const a = view.anim;
      const t = clamp((ts - a.t0) / a.dur, 0, 1);
      view.center = a.from + (a.to - a.from) * ease(t);
      view.target = view.center;
      view.vel = 0;
      if (t >= 1) view.anim = null;
    } else if (view.drag) {
      // το pointermove ορίζει ΚΑΙ τα δύο (άμεσος χειρισμός: 1:1 με το χέρι)
      view.vel = 0;
    } else if (view.warping) {
      // σβησμένη εικόνα: κρατάμε τη θέση μέχρι να έρθει το νέο παράθυρο
    } else if (view.free) {
      // ΕΛΑΤΗΡΙΟ: η επιτάχυνση είναι ανάλογη της απόστασης από τον στόχο
      // ΜΕΙΟΝ την απόσβεση της ταχύτητας. Ξεκινά από μηδενική ταχύτητα
      // (ΕΠΙΤΑΧΥΝΣΗ) και μπαίνει στον στόχο σβήνοντας (ΕΠΙΒΡΑΔΥΝΣΗ).
      const dtS = clamp(dt, 1, 40) / 1000;
      view.target = clamp(view.target, 0, total);
      const dx = view.center - view.target;
      view.vel += (-OMEGA * OMEGA * dx - DAMP * OMEGA * view.vel) * dtS;
      view.center += view.vel * dtS;
      if (Math.abs(dx) < 0.02 && Math.abs(view.vel) < 0.05) {
        view.center = view.target;
        view.vel = 0;
      }
    } else {
      view.vel = 0;
      view.center = now;                        // ζωντανή κύλιση
      view.target = now;
    }
    view.center = clamp(view.center, 0, total);

    el.style.setProperty("--ppm", ppm);
    const shift = width / 2 - view.center * ppm;
    el.style.setProperty("--shift", shift.toFixed(2) + "px");
    const nowx = now * ppm + shift;
    el.style.setProperty("--nowx", nowx.toFixed(2) + "px");
    el.classList.toggle("nowoff", nowx < 0 || nowx > width);
    // Πυκνότητα χάρακα/υποβάθρου ανά επίπεδο zoom (κατά τη μετάβαση:
    // το επίπεδο που ΦΑΙΝΕΤΑΙ, ώστε να αλλάζει στη μέση της κίνησης)
    const lvl = shownLevel(mins);
    SPANS.forEach((s) => el.classList.toggle("z-" + s.key, s === lvl));
    // Περίοδος των κατακόρυφων γραμμών του υποβάθρου (λεπτά): λεπτές/έντονες
    el.style.setProperty("--gridmin", lvl.grid[0]);
    el.style.setProperty("--majormin", lvl.grid[1]);

    openBars(el, now);
    dayLabel(w, view.center - mins / 2);      // ημέρα στην αριστερή άκρη
    clockText(el, w);
    // Οι καταστάσεις αφορούν την ΤΡΕΧΟΥΣΑ ώρα και βγαίνουν από την ημέρα
    // αναφοράς (data-ref-*), άρα ισχύουν όπου κι αν κοιτά ο χρήστης —
    // αρκεί μία ενημέρωση ανά λεπτό.
    const minute = Math.floor(Date.now() / 60000);
    if (minute !== view.lastMin) {
      view.lastMin = minute;
      status(el, now, w.dataset.agg === "1");
    }
    // ⚠️ Καμία μετακύλιση παραθύρου όσο τρέχει το ζουμ: το παράθυρο του νέου
    // επιπέδου έχει ήδη ζητηθεί από το `setZoom` (διαφορετικό `days`).
    if (!view.loading && !view.drag && !view.warping && !view.zoomAnim) {
      edge(el, w, total);
    }
  }

  /** ΑΝΟΙΧΤΕΣ ΒΑΡΔΙΕΣ (ψηφιακή κάρτα: μπήκε, δεν έχει βγει ακόμη).
   *
   *  Ο server ΔΕΝ μπορεί να δώσει το πλάτος τους — μεγαλώνει με τον χρόνο.
   *  Στέλνει την αρχή (`--s`) και το ΦΡΑΓΜΑ (`--emax`, τα μεσάνυχτα της
   *  ημέρας της διέλευσης) και εδώ γράφεται μόνο το `--d`: έτσι η μπάρα
   *  «τρέχει» ζωντανά μέχρι την τρέχουσα ώρα, ενώ μια ΞΕΧΑΣΜΕΝΗ έξοδος
   *  παλιάς ημέρας σταματά στα μεσάνυχτά της αντί να σαρώνει την οθόνη. */
  function openBars(el, now) {
    el.querySelectorAll(".lb-act.open").forEach((b) => {
      const s = parseFloat(b.style.getPropertyValue("--s")) || 0;
      const emax = parseFloat(b.style.getPropertyValue("--emax")) || s;
      b.style.setProperty("--d", clamp(now, s, emax) - s);
    });
  }

  /** Ημερομηνία στην ΑΡΙΣΤΕΡΗ ΑΚΡΗ της προβολής — «ποια ημέρα βλέπω». */
  function dayLabel(w, leftMin) {
    const chip = document.getElementById("lb-daynow");
    if (!chip) return;
    const d = new Date(baseDate(w).getTime() + leftMin * 60000);
    chip.textContent = `${DOW[(d.getDay() + 6) % 7]} ${pad(d.getDate())}/`
      + `${pad(d.getMonth() + 1)}/${d.getFullYear()}`;
  }

  function clockText(el, w) {
    const clock = document.getElementById("lb-time");
    if (clock) {
      // Η ώρα είναι το ΠΡΑΓΜΑΤΙΚΟ ρολόι — ανεξάρτητη από το πού κοιτά ο
      // χρήστης (το `now` σε λεπτά μπορεί να είναι εκτός του παραθύρου).
      const t = new Date();
      clock.textContent = el.dataset.live === "1"
        ? pad(t.getHours()) + ":" + pad(t.getMinutes()) : "—";
    }
    const at = document.getElementById("lb-at");
    if (!at) return;
    if (!at.dataset.home) at.dataset.home = at.textContent.trim();
    if (!view.free) { at.textContent = at.dataset.home; return; }
    // Πλήρης ημερομηνία ΗΗ/ΜΜ/ΕΕΕΕ: στην προβολή έτους το «05/08» χωρίς έτος
    // δεν ξεχωρίζει το 2025 από το 2026.
    const d = new Date(baseDate(w).getTime() + view.center * 60000);
    at.textContent = `κέντρο: ${pad(d.getDate())}/${pad(d.getMonth() + 1)}/`
      + `${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  /** Κατάσταση κάθε γραμμής ΤΩΡΑ + τα σύνολα της κεφαλίδας.
   *
   *  ⚠️ Διαβάζει ΠΑΝΤΑ το πρόγραμμα της ΗΜΕΡΑΣ ΑΝΑΦΟΡΑΣ (data-ref-*, σε
   *  λεπτά της ημέρας) — ΠΟΤΕ τις μπάρες του παραθύρου: αυτές μπορεί να
   *  ανήκουν σε άλλη ημερομηνία (ο χρήστης μετακινήθηκε) ή να είναι
   *  εβδομαδιαία σύνολα (προβολή έτους), οπότε η σύγκριση με την ώρα θα
   *  έδινε λάθος κατάσταση. `nowAbs` χρειάζεται ΜΟΝΟ για να βρεθεί ποια
   *  μπάρα του παραθύρου θα βαφτεί πορτοκαλί.
   */
  function status(el, nowAbs, agg) {
    const live = el.dataset.live === "1";
    const t = new Date();
    const min = t.getHours() * 60 + t.getMinutes() + t.getSeconds() / 60;
    const tally = { on: 0, off: 0, leave: 0, miss: 0 };
    el.querySelectorAll(".lb-row[data-eid]").forEach((row) => {
      const starts = nums(row.dataset.refSegs);
      const ends = nums(row.dataset.refEnds);
      const tele = nums(row.dataset.refTele);
      // Η νυχτερινή βάρδια συνεχίζει μετά τα μεσάνυχτα (λήξη > 24:00)
      const inSeg = (i) => (min >= starts[i] && min < ends[i]) ||
        (min + DAY >= starts[i] && min + DAY < ends[i]);
      const at = starts.findIndex((_s, i) => inSeg(i));
      const inside = at >= 0;
      // ΤΗΛΕΕΡΓΑΣΙΑ: δεν καταγράφεται από την κάρτα — λογίζεται δεδομένη
      // (§5.7), οπότε δεν είναι «ασυμφωνία» η απουσία χτυπήματος.
      const telework = inside && tele[at] === 1;
      const punches = parseInt(row.dataset.refPunches, 10) || 0;
      const onLeave = row.dataset.leave === "1";
      const today = starts.map((s, i) => [s, ends[i]]);
      const between = today.length > 1 && !inside &&
        min > today[0][1] && min < today[today.length - 1][0];
      let cls = "off", txt = "εκτός ωραρίου";
      if (onLeave) { cls = "leave"; txt = "σε άδεια"; tally.leave++; }
      else if (!today.length) { cls = "rest"; txt = "ρεπό"; }
      // Ιστορική ημέρα: δεν υπάρχει «τώρα» — καμία στιγμιαία κατάσταση
      else if (!live) { cls = "off"; txt = "—"; }
      else if (inside) {
        cls = "on"; txt = telework ? "τηλεεργασία" : "σε εργασία"; tally.on++;
      }
      else if (between) { cls = "break"; txt = "σε διάλειμμα"; tally.off++; }
      else if (min < today[0][0]) { txt = "δεν ξεκίνησε ακόμη"; tally.off++; }
      else { txt = "ολοκλήρωσε"; tally.off++; }
      // ΑΣΥΜΦΩΝΙΑ ΜΕ ΤΗΝ ΚΑΡΤΑ: το πρόγραμμα λέει «εργάζεται» αλλά η κάρτα
      // όχι — είτε δεν χτύπησε καθόλου, είτε έχει ήδη χτυπήσει έξοδο.
      const aS = nums(row.dataset.refActs), aE = nums(row.dataset.refActe);
      const punchedIn = aS.some((s, i) => min >= s && min < aE[i]);
      if (live && inside && !onLeave && !telework && !punchedIn) {
        cls = "miss";
        txt = punches === 0 ? "χωρίς χτύπημα" : "εκτός με κάρτα";
        tally.miss++;
      }
      // Η κατάσταση είναι ΚΟΥΚΙΔΑ με tooltip (μία γραμμή ανά εργαζόμενο)
      const dot = row.querySelector(".lb-dot");
      if (dot) { dot.className = "lb-dot st-" + cls; dot.title = txt; }
      // ΑΣΥΜΦΩΝΙΑ: βάφεται ΠΟΡΤΟΚΑΛΙ η μπάρα του τρέχοντος ωραρίου. Οι
      // μπάρες είναι στην ΙΔΙΑ σειρά με τα data-segs του ΠΑΡΑΘΥΡΟΥ, οπότε
      // εντοπίζεται με την ΑΠΟΛΥΤΗ ώρα. Σε εβδομαδιαία σύνοψη (έτος) δεν
      // υπάρχει μπάρα-διάστημα να βαφτεί.
      const bars = row.querySelectorAll(".lb-bar");
      bars.forEach((b) => b.classList.remove("miss"));
      if (cls === "miss" && !agg) {
        const ws = nums(row.dataset.segs), we = nums(row.dataset.ends);
        const wi = ws.findIndex((s, i) => nowAbs >= s && nowAbs < we[i]);
        if (wi >= 0 && bars[wi]) {
          bars[wi].classList.add("miss");
          bars[wi].dataset.miss = txt;
        }
      }
    });
    const box = document.getElementById("lb-tally");
    if (box) {
      const b = box.querySelectorAll("b");
      [tally.on, tally.off, tally.leave, tally.miss].forEach((v, i) => {
        if (b[i]) b[i].textContent = v;
      });
    }
  }

  function nums(s) {
    return (s || "").split(",").filter(Boolean).map(Number);
  }

  // ------------------------------------------------- παράθυρο ημερών
  /** Φέρνει το παράθυρο που ξεκινά από `base` και κρατά το κέντρο σταθερό:
   *  το `center` μετριέται από τη base, άρα προσαρμόζεται στη μετατόπιση. */
  async function load(el, base, days) {
    const w = win();
    const url = el.dataset.rowsUrl + "?base=" + base
      + "&days=" + (days || zoomLevel().days);
    view.loading = true;
    try {
      const html = await (await fetch(url, { headers: { "HX-Request": "1" } })).text();
      const oldBase = w ? baseDate(w) : null;
      document.getElementById("lb-rows").innerHTML = html;
      const nw = win();
      if (nw && oldBase) {
        // ⚠️ Η μετατόπιση της base πρέπει να εφαρμοστεί σε ΟΛΕΣ τις θέσεις
        // (κέντρο, στόχος, τρέχον animation) — αλλιώς η εικόνα πηδά μία ημέρα
        const d = (oldBase - baseDate(nw)) / 60000;
        view.center += d;
        view.target += d;
        if (view.anim) { view.anim.from += d; view.anim.to += d; }
      }
      view.lastMin = -1;                       // ξαναγράψε τις καταστάσεις
    } catch (err) {
      /* προβολή μόνο: μια αποτυχία δικτύου δεν χαλά την τρέχουσα εικόνα */
    } finally {
      view.loading = false;
    }
  }

  function iso(d) {
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }

  /** Η αρχή του παραθύρου ώστε η ημέρα ΑΝΑΦΟΡΑΣ να πέφτει στη μέση του. */
  function homeBase(el) {
    const [y, m, d] = el.dataset.day.split("-").map(Number);
    return new Date(y, m - 1, d - Math.floor(zoomLevel().days / 2));
  }

  /** Κοντά στην άκρη του παραθύρου -> μετακύλισέ το μία ημέρα. Κρίνεται από
   *  τον ΣΤΟΧΟ (πού πάει η κίνηση), όχι από το ορατό κέντρο που τον κυνηγά. */
  function edge(el, w, total) {
    const base = baseDate(w);
    const margin = edgeMargin();
    const page = zoomLevel().page * DAY * 60000;   // βήμα μετακύλισης (ms)
    if (Math.max(view.center, view.target) > total - margin) {
      load(el, iso(new Date(base.getTime() + page)));
    } else if (Math.min(view.center, view.target) < margin) {
      load(el, iso(new Date(base.getTime() - page)));
    }
  }

  // ------------------------------------------------------- αλληλεπίδραση
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  /** Εμφάνιση του κουμπιού κλειδώματος. */
  function lockUI(on) {
    const el = board(), btn = document.getElementById("lb-lock");
    if (el) el.classList.toggle("free", on);
    if (btn) {
      btn.classList.toggle("on", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.textContent = on ? "🔒" : "🔓";
    }
  }

  function setFree(on) {
    view.free = on;
    view.v = 0;
    lockUI(on);
  }

  /** ΕΠΙΣΤΡΟΦΗ ΣΤΟ «ΤΩΡΑ» — δύο διαφορετικές, εξίσου ομαλές διαδρομές:
   *
   *  ΚΟΝΤΑ (< 1,5 οθόνη): απλό glide πάνω στο ΗΔΗ φορτωμένο περιεχόμενο.
   *  ΜΑΚΡΙΑ: ⚠️ ΠΟΤΕ μη «τρέξεις» πολλές ημέρες — η εικόνα περνούσε τρεις
   *  οθόνες σε 0,7΄΄ ΚΑΙ ταυτόχρονα άλλαζε παράθυρο (fetch + αντικατάσταση
   *  DOM) στη μέση του animation, οπότε τα γραφικά «έσπαγαν». Αντ' αυτού:
   *  σβήσιμο (fade) → φόρτωση του παραθύρου του «τώρα» → εμφάνιση.
   */
  async function toNow() {
    const el = board();
    let w = win();
    if (!el || !w || view.warping) return;
    const far = Math.abs(nowMinutes(el, w) - view.center) > span() * 60 * 1.5;
    if (!far) {
      view.free = true;                    // μη «κολλήσει» στη ζωντανή θέση
      glide(nowMinutes(el, w), 620);
      lockUI(false);
      await sleep(660);
      view.anim = null;
      setFree(false);
      return;
    }
    view.warping = true;
    lockUI(false);
    el.classList.add("warp");              // fade out
    view.anim = null;
    view.v = 0;
    await sleep(190);
    await load(el, iso(homeBase(el)));       // παράθυρο γύρω από το «τώρα»
    w = win();
    view.free = false;
    if (w) view.center = view.target = nowMinutes(el, w);
    render(performance.now());             // τοποθέτηση ΠΡΙΝ την εμφάνιση
    el.classList.remove("warp");           // fade in
    view.warping = false;
  }

  function drag(el) {
    const grid = el.querySelector(".lb-grid");
    if (!grid) return;
    grid.addEventListener("pointerdown", (e) => {
      if (!view.free || e.button !== 0) return;
      if (e.target.closest(".lb-name")) return;  // η στήλη ονομάτων δεν σέρνει
      view.drag = { from: view.center, x0: e.clientX, x: e.clientX,
                    t: performance.now() };
      view.v = 0;
      view.anim = null;
      el.classList.add("drag");
      grid.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    grid.addEventListener("pointermove", (e) => {
      if (!view.drag) return;
      const ppm = parseFloat(getComputedStyle(el).getPropertyValue("--ppm")) || 1;
      // Σύρσιμο δεξιά = πάμε πίσω στον χρόνο (1:1 — ο άμεσος χειρισμός δεν
      // εξομαλύνεται, αλλιώς η εικόνα «ξεφεύγει» από το δάχτυλο)
      view.center = view.drag.from - (e.clientX - view.drag.x0) / ppm;
      view.target = view.center;
      const t = performance.now(), dt = t - view.drag.t;
      if (dt > 0) {
        // ταχύτητα σε λεπτά/ms (για την αδράνεια μετά την απελευθέρωση)
        view.v = -(e.clientX - view.drag.x) / ppm / dt;
        view.drag.x = e.clientX;
        view.drag.t = t;
      }
    });
    const stop = (e) => {
      if (!view.drag) return;
      // Αν το τελευταίο move ήταν παλιό, το χέρι είχε ήδη σταματήσει
      if (performance.now() - view.drag.t > 80) view.v = 0;
      // ΑΔΡΑΝΕΙΑ: ο στόχος προβάλλεται μπροστά κατά την ταχύτητα του χεριού
      // και το ελατήριο τον φτάνει επιβραδύνοντας (καμία απότομη στάση).
      const total = parseFloat(win().dataset.span) || DAY;
      view.target = clamp(view.center + view.v * 1000 * FLING, 0, total);
      view.vel = view.v * 1000;                 // συνέχεια χωρίς «κόψιμο»
      view.v = 0;
      view.ts = performance.now();
      view.drag = null;
      el.classList.remove("drag");
      if (e.pointerId !== undefined && grid.hasPointerCapture(e.pointerId)) {
        grid.releasePointerCapture(e.pointerId);
      }
    };
    grid.addEventListener("pointerup", stop);
    grid.addEventListener("pointercancel", stop);

    // Ρόδα ποντικιού/trackpad: οριζόντια κύλιση (ή Shift+ρόδα). Ξεκλειδώνει
    // μόνη της, αλλιώς η κίνηση θα «έσβηνε» αμέσως από τη ζωντανή κύλιση.
    grid.addEventListener("wheel", (e) => {
      const dx = Math.abs(e.deltaX) > Math.abs(e.deltaY)
        ? e.deltaX : (e.shiftKey ? e.deltaY : 0);
      if (!dx) return;
      e.preventDefault();
      if (!view.free) setFree(true);
      const ppm = parseFloat(getComputedStyle(el).getPropertyValue("--ppm")) || 1;
      view.anim = null;
      view.v = 0;
      // Ο βρόχος μπορεί να «κοιμόταν» (250ms): χωρίς reset, το πρώτο καρέ
      // θα είχε τεράστιο dt και η κίνηση θα ξεκινούσε με άλμα αντί για ράμπα
      view.ts = performance.now();
      // Μετακινείται ο ΣΤΟΧΟΣ, όχι η εικόνα: συνεχόμενες κινήσεις της ρόδας
      // συσσωρεύονται (επιτάχυνση) και όταν σταματήσουν η εικόνα φτάνει
      // ομαλά (επιβράδυνση) — χωρίς «σκαλοπάτια» ανά γεγονός.
      view.target = clamp(view.target + dx / ppm, 0,
                          parseFloat(win().dataset.span) || DAY);
    }, { passive: false });
  }

  let zoomSeq = 0;                 // ταυτότητα του τελευταίου ζουμ (§setZoom)

  function zoomUI() {
    const el = board();
    if (!el) return;
    el.querySelectorAll("#lb-zoom button").forEach((b) => {
      b.classList.toggle("on", parseInt(b.dataset.span, 10) === span());
    });
  }

  /** Αλλαγή επιπέδου zoom: κρατά το ΙΔΙΟ κέντρο, αλλά ζητά το παράθυρο που
   *  χρειάζεται το νέο εύρος (η εβδομάδα/ο μήνας θέλουν πολύ περισσότερες
   *  ημέρες δεδομένων από την ημέρα).
   *
   *  ΤΟ ΕΥΡΟΣ ΤΑΞΙΔΕΥΕΙ, ΔΕΝ ΠΗΔΑ: το ορατό `view.zoom` κινείται γεωμετρικά
   *  προς το νέο εύρος και ΟΛΟ το περιεχόμενο ανοίγει/κλείνει μαζί του (μία
   *  μεταβλητή, `--ppm` — καμία αλλαγή ανά μπάρα).
   *  ⚠️ Η ΣΕΙΡΑ ΕΞΑΡΤΑΤΑΙ ΑΠΟ ΤΗΝ ΚΑΤΕΥΘΥΝΣΗ:
   *   - ζουμ ΕΞΩ (ευρύτερο): ΠΡΩΤΑ το νέο, πλατύτερο παράθυρο δεδομένων και
   *     μετά η κίνηση — αλλιώς η λωρίδα μίκραινε μέσα σε κενό χώρο μέχρι να
   *     έρθει το fetch (με φρένο ZOOM_WAIT_MS, να μη «κολλάει» το κουμπί)·
   *   - ζουμ ΜΕΣΑ (στενότερο): η κίνηση ξεκινά ΑΜΕΣΩΣ (το φορτωμένο παράθυρο
   *     είναι ήδη υπερσύνολο του στόχου) και η λεπτομέρεια έρχεται στην πορεία.
   */
  async function setZoom(hours, animate) {
    const el = board(), w = win();
    const from = spanMin();
    localStorage.setItem(KEY, hours);
    zoomUI();
    if (!el || !w) return;
    const lvl = zoomLevel();
    const to = lvl.h * 60;
    const mid = new Date(baseDate(w).getTime() + view.center * 60000);
    mid.setHours(0, 0, 0, 0);
    mid.setDate(mid.getDate() - Math.floor(lvl.days / 2));
    view.ts = performance.now();
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (animate === false || still || to === from) {
      zoomSeq++;                           // ακυρώνει τυχόν ζουμ που περιμένει
      view.zoom = to;
      view.zoomAnim = null;
      el.classList.remove("zooming");
      await load(el, iso(mid), lvl.days);
      return;
    }
    el.classList.add("zooming");           // fade-in του νέου παραθύρου
    // ⚠️ ΠΡΙΝ από κάθε await: το `localStorage` δείχνει ήδη το ΝΕΟ επίπεδο,
    // οπότε χωρίς ρητό `view.zoom` το `spanMin()` θα πηδούσε αμέσως εκεί.
    view.zoom = from;
    // Δεύτερο πάτημα όσο περιμένουμε το παράθυρο: ΜΟΝΟ το τελευταίο ζουμ
    // επιτρέπεται να ξεκινήσει κίνηση (αλλιώς θα «γύριζε» στον παλιό στόχο).
    const seq = ++zoomSeq;
    const pending = load(el, iso(mid), lvl.days);
    if (to > from) await Promise.race([pending, sleep(ZOOM_WAIT_MS)]);
    if (seq !== zoomSeq) return;
    // Διάρκεια ανάλογη του ΛΟΓΟΥ των δύο ευρών (x7 σύντομο, x365 μακρύ)
    const oct = Math.log2(Math.max(to / from, from / to));
    const dur = clamp(ZOOM_MIN_MS + oct * 55, ZOOM_MIN_MS, ZOOM_MAX_MS);
    view.zoomAnim = { from, to, t0: performance.now(), dur };
    view.ts = performance.now();
  }

  /** Μετακίνηση μία «οθόνη» πίσω/μπροστά (ημέρα/εβδομάδα/μήνας, ανάλογα με
   *  το zoom), με ομαλό animation. */
  function step(dir) {
    if (!view.free) setFree(true);
    view.ts = performance.now();
    glide(view.center + dir * span() * 60, 800);
  }

  function init() {
    const el = board();
    if (!el) return;
    zoomUI();
    el.querySelectorAll("#lb-zoom button").forEach((b) => {
      b.addEventListener("click", () => setZoom(parseInt(b.dataset.span, 10)));
    });
    // Το αρχικό παράθυρο του server είναι 3 ημερών· αν η αποθηκευμένη
    // προτίμηση είναι εβδομάδα/μήνας, ζήτα αμέσως το σωστό μέγεθος.
    // ⚠️ ΧΩΡΙΣ animation: η φόρτωση της σελίδας δεν είναι «αλλαγή ζουμ» του
    // χρήστη — ένα ζουμ που τρέχει μόνο του στο άνοιγμα είναι θόρυβος.
    view.zoom = span() * 60;
    if (zoomLevel().days > 3) setZoom(span(), false);
    const lock = document.getElementById("lb-lock");
    if (lock) {
      lock.addEventListener("click", () => {
        if (view.free) toNow(); else setFree(true);
      });
    }
    const prev = document.getElementById("lb-prev");
    if (prev) prev.addEventListener("click", () => step(-1));
    const next = document.getElementById("lb-next");
    if (next) next.addEventListener("click", () => step(1));
    drag(el);

    // Βρόχος καρέ: κάθε καρέ ΜΟΝΟ όταν υπάρχει κίνηση (σύρσιμο/αδράνεια/
    // animation). Στη ζωντανή κύλιση η μετατόπιση είναι ~1,7px/λεπτό, οπότε
    // 4 ενημερώσεις/δευτ. είναι οπτικά ταυτόσημες και δεν καίνε CPU.
    let last = 0;
    const loop = (ts) => {
      // «Απασχολημένο» = υπάρχει κίνηση να αποδοθεί: animation, σύρσιμο,
      // αδράνεια, ή το κέντρο κυνηγά ακόμη τον στόχο (accel/decel).
      const busy = view.anim || view.drag || view.vel || view.zoomAnim ||
        (view.target !== null && Math.abs(view.target - view.center) > 0.02);
      if (busy || ts - last > 250) { last = ts; render(ts); }
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);

    // Ανανέωση δεδομένων (νέα χτυπήματα κάρτας, αλλαγές προγράμματος/αδειών)
    // — ίδια διαδρομή με τη μετακύλιση του παραθύρου, ώστε να μη χαθεί η θέση.
    // ⚠️ Ο ρυθμός ΑΡΑΙΩΝΕΙ με το εύρος: το παράθυρο του έτους είναι 548
    // ημέρες × εργαζόμενους — δεν το ξαναζητάμε κάθε λεπτό για να δούμε ένα
    // χτύπημα που ούτως ή άλλως δεν σχεδιάζεται σε αυτή την κλίμακα.
    let due = 0;
    setInterval(() => {
      const w = win();
      due += 10000;
      if (due < zoomLevel().refresh) return;
      due = 0;
      if (w && !view.drag && !view.loading) load(el, w.dataset.base);
    }, 10000);

    // ΨΗΦΙΑΚΗ ΚΑΡΤΑ — ΠΡΑΓΜΑΤΙΚΟΣ ΧΡΟΝΟΣ (δύο διαδρομές, ίδιο αποτέλεσμα):
    // (α) καταχώρηση από ΑΥΤΗ την οθόνη: ο server απαντά με `HX-Trigger:
    //     lb-refresh` και οι γραμμές ξαναφορτώνονται ΑΜΕΣΩΣ·
    // (β) καταχώρηση από ΑΛΛΟΥ (άλλη οθόνη, άλλος χρήστης, μηχάνημα κάρτας):
    //     ρωτάμε τον «σφυγμό» — έναν αριθμό που αλλάζει σε κάθε διέλευση —
    //     και κατεβάζουμε το παράθυρο ΜΟΝΟ όταν όντως άλλαξε κάτι.
    document.body.addEventListener("lb-refresh", () => refresh(el));
    const purl = el.dataset.pulseUrl;
    if (purl) {
      let seen = null;
      setInterval(async () => {
        if (view.drag || view.loading || document.hidden) return;
        try {
          const v = (await (await fetch(purl)).json()).v;
          if (seen !== null && v !== seen) refresh(el);
          seen = v;
        } catch (err) { /* προβολή μόνο: η αποτυχία δεν χαλά την εικόνα */ }
      }, PULSE_MS);
    }
  }

  /** Άμεση επαναφόρτωση του ΤΡΕΧΟΝΤΟΣ παραθύρου (χωρίς αλλαγή θέσης). */
  function refresh(el) {
    const w = win();
    if (w && !view.drag && !view.loading) load(el, w.dataset.base);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

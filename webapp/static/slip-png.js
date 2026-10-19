/* Αποθήκευση της ΑΝΑΛΥΣΗΣ ενός εργαζομένου (modal «Ανάλυση» μισθοδοσίας /
   Δώρων / Επιδόματος Αδείας) σε εικόνα PNG.

   ΓΙΑΤΙ ΥΠΑΡΧΕΙ JS ΕΔΩ: η παραγωγή εικόνας είναι καθαρά λειτουργία του
   browser (καμβάς) — δεν υπάρχει server-side rasterizer στο project (το
   fpdf2 βγάζει PDF, όχι PNG) και δεν προστίθεται εξάρτηση. Δεν αλλάζει
   κατάσταση: κανένα event, καμία εντολή — μόνο μια λήψη αρχείου.

   ΠΩΣ: το <details> του εργαζομένου κλωνοποιείται, «ξεδιπλώνεται» ολόκληρο
   (foldables ανοιχτά, scroll περιοχές σε πλήρες ύψος) και σερβίρεται μέσα σε
   SVG <foreignObject> μαζί με το ΙΔΙΟ app.css. Το SVG ζωγραφίζεται σε canvas
   (x2 για ευκρίνεια) και κατεβαίνει ως PNG. Καμία εξωτερική βιβλιοθήκη. */
(function () {
  var cssText = null;  // το app.css, κατεβασμένο ΜΙΑ φορά

  function css() {
    if (cssText !== null) return Promise.resolve(cssText);
    var link = document.querySelector('link[href*="app.css"]');
    if (!link) return Promise.resolve((cssText = ''));
    return fetch(link.getAttribute('href'))
      .then(function (r) { return r.text(); })
      .then(function (t) { return (cssText = t); })
      .catch(function () { return (cssText = ''); });
  }

  function toast(msg, err) {
    // Ίδιος μηχανισμός με τα toasts του base.html (event «flash»)
    document.body.dispatchEvent(
      new CustomEvent('flash', { detail: { msg: msg, err: !!err } }));
  }

  /* <details>/<summary> -> <div> με τις ΙΔΙΕΣ κλάσεις (+ `sumdiv` για τα
     summary, βλ. app.css). ΓΙΑΤΙ: μέσα σε SVG <foreignObject> ο Chrome
     αφήνει ΚΕΝΟ το σώμα των <details> (δεν ζωγραφίζει το slotted
     περιεχόμενο) — το ύψος δεσμεύεται, αλλά η εικόνα βγαίνει άδεια. */
  function undetails(root) {
    root.querySelectorAll('details').forEach(function (d) {
      var div = document.createElement('div');
      for (var i = 0; i < d.attributes.length; i++) {
        div.setAttribute(d.attributes[i].name, d.attributes[i].value);
      }
      while (d.firstChild) {
        var ch = d.firstChild;
        if (ch.nodeType === 1 && ch.tagName.toLowerCase() === 'summary') {
          var s = document.createElement('div');
          for (var j = 0; j < ch.attributes.length; j++) {
            s.setAttribute(ch.attributes[j].name, ch.attributes[j].value);
          }
          s.className = (ch.className ? ch.className + ' ' : '') + 'sumdiv';
          while (ch.firstChild) s.appendChild(ch.firstChild);
          d.removeChild(ch);
          div.appendChild(s);
        } else {
          div.appendChild(ch);
        }
      }
      d.parentNode.replaceChild(div, d);
    });
  }

  /* Κλώνος «έτοιμος για εκτύπωση»: όλα ανοιχτά, τίποτα κρυμμένο σε scroll. */
  function flatten(slip) {
    var clone = slip.cloneNode(true);
    clone.setAttribute('open', 'open');
    clone.querySelectorAll('details').forEach(function (d) {
      d.setAttribute('open', 'open');
    });
    clone.querySelectorAll('.pd-scroll').forEach(function (d) {
      d.style.maxHeight = 'none';
      d.style.overflow = 'visible';
    });
    clone.querySelectorAll('.png-save, .chev').forEach(function (el) {
      el.remove();  // κουμπί/βελάκι δεν έχουν νόημα στην εικόνα
    });
    var box = document.createElement('div');
    box.appendChild(clone);
    undetails(box);
    return box;
  }

  function save(btn) {
    var slip = btn.closest('.pslip');
    if (!slip) return;
    var name = (btn.dataset.pngName || 'analysis') + '.png';
    css().then(function (style) {
      // Μετράμε τον κλώνο εκτός οθόνης, στο ΙΔΙΟ πλάτος με το πρωτότυπο
      var width = Math.ceil(slip.getBoundingClientRect().width) || 900;
      var stage = document.createElement('div');
      stage.className = 'payslip';
      stage.style.cssText =
        'position:fixed;left:-10000px;top:0;width:' + width + 'px;' +
        'background:#fff;padding:16px';
      // Το app.css μπαίνει ΜΕΣΑ στον κόμβο και σειριοποιείται μαζί του: ο
      // XMLSerializer κάνει escape τα «&»/«<» των σχολίων του CSS — με σκέτη
      // συνένωση string το SVG δεν θα ήταν έγκυρο XML και δεν θα φόρτωνε.
      var st = document.createElement('style');
      // Οι animations «παγώνουν» στο καρέ 0 μέσα σε SVG image· με
      // `animation-fill-mode: both` (π.χ. `.pslip[open] .pslip-body`) αυτό
      // σημαίνει opacity:0 — δηλαδή ΚΕΝΗ εικόνα. Τις απενεργοποιούμε.
      st.textContent = style +
        '\n*{animation:none !important;transition:none !important}';
      stage.appendChild(st);
      stage.appendChild(flatten(slip));
      document.body.appendChild(stage);

      var w = width + 32;
      var h = Math.ceil(stage.getBoundingClientRect().height);
      // ΠΡΟΣΟΧΗ: το inline style της μέτρησης σειριοποιείται ΚΙ ΑΥΤΟ — με
      // «position:fixed;left:-10000px» το περιεχόμενο ζωγραφιζόταν εκτός
      // καμβά και το PNG έβγαινε ΚΕΝΟ. Το καθαρίζουμε πριν τη σειριοποίηση.
      stage.style.cssText =
        'width:' + width + 'px;background:#fff;padding:16px;' +
        // Οι γραμματοσειρές του UI έρχονται από το δίκτυο και ΔΕΝ φορτώνουν
        // μέσα στο SVG image — ορίζουμε ρητά τοπικό sans stack (τα mono
        // πεδία κρατούν το δικό τους var(--mono)).
        "font-family:'IBM Plex Sans','Segoe UI',Arial,Helvetica,sans-serif";
      var xml = new XMLSerializer().serializeToString(stage);
      document.body.removeChild(stage);

      var svg =
        '<svg xmlns="http://www.w3.org/2000/svg" width="' + w + '" height="' + h + '">' +
        '<foreignObject x="0" y="0" width="' + w + '" height="' + h + '">' +
        '<div xmlns="http://www.w3.org/1999/xhtml">' + xml + '</div>' +
        '</foreignObject></svg>';

      var img = new Image();
      img.onload = function () {
        var scale = 2;  // ευκρίνεια
        var cv = document.createElement('canvas');
        cv.width = w * scale;
        cv.height = h * scale;
        var ctx = cv.getContext('2d');
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, cv.width, cv.height);
        ctx.setTransform(scale, 0, 0, scale, 0, 0);
        ctx.drawImage(img, 0, 0);
        cv.toBlob(function (blob) {
          if (!blob) { toast('Αποτυχία δημιουργίας εικόνας', true); return; }
          var url = URL.createObjectURL(blob);
          var a = document.createElement('a');
          a.href = url;
          a.download = name;
          document.body.appendChild(a);
          a.click();
          a.remove();
          setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
          toast('Αποθηκεύτηκε: ' + name);
        }, 'image/png');
      };
      img.onerror = function () { toast('Αποτυχία δημιουργίας εικόνας', true); };
      img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
    });
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.png-save');
    if (!btn) return;
    // Το κουμπί ζει μέσα σε <summary>: χωρίς αυτό το κλικ θα δίπλωνε την κάρτα
    e.preventDefault();
    e.stopPropagation();
    save(btn);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    var btn = e.target.closest && e.target.closest('.png-save');
    if (!btn) return;
    e.preventDefault();
    save(btn);
  });
})();

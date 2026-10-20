// Αλγοριθμικοί έλεγχοι της αίτησης υποψηφίου — αντίγραφα των
// `afm_checksum_ok` / `amka_checksum_ok` + `amka_birth_ok` του
// misevent/rules/constraints.py (το test τα συγκρίνει μέσω node).
function afmOk(v) {
  if (!/^\d{9}$/.test(v) || /^0+$/.test(v)) return false;
  var s = 0;
  for (var i = 0; i < 8; i++) s += Number(v[i]) * Math.pow(2, 8 - i);
  return s % 11 % 10 === Number(v[8]);
}

function amkaOk(v) {
  if (!/^\d{11}$/.test(v) || /^0+$/.test(v)) return false;
  // ΗΗΜΜΕΕ: υπαρκτή σε 19ΕΕ ή 20ΕΕ — το 20ΕΕ καλύπτει και τα δύο (29/02/1900 όχι)
  var d = Number(v.substr(0, 2)), m = Number(v.substr(2, 2)), y = Number(v.substr(4, 2));
  var dt = new Date(2000 + y, m - 1, d);
  if (dt.getMonth() !== m - 1 || dt.getDate() !== d) return false;
  var t = Number(v[10]);  // Luhn — το ψηφίο ελέγχου δεν διπλασιάζεται
  for (var j = 0; j < 10; j++) {
    var n = Number(v[j]);
    if (j % 2) { n *= 2; if (n > 9) n -= 9; }
    t += n;
  }
  return t % 10 === 0;
}

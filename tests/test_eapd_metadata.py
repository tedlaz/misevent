"""Ο eΑΠΔ viewer πρέπει να συμφωνεί με τη ΓΡΑΜΜΟΓΡΑΦΗΣΗ του apd_files.py.

Ο viewer (`webapp/eapd/`) είναι ξεχωριστή, browser-only εφαρμογή με ΔΙΚΟ της
μητρώο πεδίων (`metadata.js`). Αν αλλάξει η γραμμογράφηση στο `apd_files.py`
και ΟΧΙ στο `metadata.js` (ή αντίστροφα), ο viewer θα απορρίπτει σιωπηλά τις
γραμμές του αρχείου μας (το `parseAPDText` πετάει όσες δεν έχουν το ακριβές
μήκος). Αυτό το test τα δένει μεταξύ τους.

Επίσης επαληθεύει την ΤΕΚΑ μορφή: παράγεται στο `fund.js` από την ΕΦΚΑ
αφαιρώντας τα πεδία 53/54/55 — ακριβώς όσα παραλείπει και το `_rec3()`.
"""


def test_eapd_metadata():
    import json
    import re

    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from misevent.integrations.apd_files import APDFileBuilder

    fails = []


    def ok(desc, cond):
        print(f"  {'OK  ' if cond else 'FAIL'} {desc}")
        if not cond:
            fails.append(desc)


    def _load(path, var):
        """Διαβάζει το JSON literal ενός `window.X = {...};` αρχείου."""
        src = open(path, encoding="utf-8").read()
        body = src[src.index("=", src.index(var)) + 1 : src.rindex("}") + 1]
        return json.loads(body)


    print("=== metadata.js vs apd_files.py (γραμμογράφηση) ===")
    meta = _load("webapp/eapd/metadata.js", "APD_METADATA")

    for line, expected in [
        ("GenikaStoixeia", APDFileBuilder.REC1_LEN),
        ("StoixeiaAsfalismenou", APDFileBuilder.REC2_LEN),
        ("StoixeiaAsfalisis", APDFileBuilder.REC3_LEN["efka"]),
        ("StoixeiaEpidotiseon", APDFileBuilder.REC4_LEN),
    ]:
        d = meta[line]
        size = int(d["line_size"])
        total = sum(int(c["length"]) for c in d["columns"])
        ok(f"{line}: line_size {size} == apd_files {expected}", size == expected)
        ok(f"{line}: άθροισμα στηλών {total} == {size}", total == size)

    print("\n=== ΤΕΚΑ: η μορφή παράγεται από την ΕΦΚΑ (fund.js) ===")
    fund_js = open("webapp/eapd/fund.js", encoding="utf-8").read()
    m = re.search(r"const DROP = \[([^\]]*)\]", fund_js)
    drop = set(re.findall(r"'(\d+)'", m.group(1))) if m else set()
    ok("το fund.js αφαιρεί τα πεδία 53/54/55", drop == {"53", "54", "55"})

    cols = meta["StoixeiaAsfalisis"]["columns"]
    teka = [c for c in cols if str(c["no"]) not in drop]
    teka_len = sum(int(c["length"]) for c in teka)
    ok(
        f"ΤΕΚΑ τύπος 3: {teka_len} == apd_files {APDFileBuilder.REC3_LEN['teka']}",
        teka_len == APDFileBuilder.REC3_LEN["teka"],
    )
    ok("η ΤΕΚΑ κρατά τα πεδία 52 και 56", {"52", "56"} <= {str(c["no"]) for c in teka})

    # Τα πεδία που ΔΕΝ γράφει το _rec3() για ΤΕΚΑ, όπως τεκμηριώνονται εκεί:
    # Γραμμάτιο προείσπραξης / Φορέας Επικ. εκτός eΕΦΚΑ / Φορέας Ασθένειας εκτός.
    dropped_names = [c["name"] for c in cols if str(c["no"]) in drop]
    ok(
        f"τα αφαιρούμενα είναι τα σωστά: {', '.join(dropped_names)}",
        any("Γραμμάτιο" in n for n in dropped_names)
        and any("Επικ" in n for n in dropped_names)
        and any("Ασθένειας" in n for n in dropped_names),
    )

    print("\n=== Ο viewer φορτώνει fund.js και bridge.js ===")
    loader = open("webapp/eapd/loader.js", encoding="utf-8").read()
    order = [f for f in ("app.js", "fund.js", "bridge.js") if f in loader]
    ok("σειρά φόρτωσης app.js -> fund.js -> bridge.js",
       order == ["app.js", "fund.js", "bridge.js"])

    assert not fails, "ΑΠΟΤΥΧΙΕΣ: " + "; ".join(str(x) for x in fails)
    print("\nΟλα τα tests περασαν.")

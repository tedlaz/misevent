# Εγκατάσταση online έκδοσης — τοπικό VM σε Proxmox

Πρώτη φάση: ένα VM στο τοπικό δίκτυο, πρόσβαση με `https://<IP του VM>`.
Το ίδιο setup περνά αργότερα σε δημόσιο domain αλλάζοντας ΜΟΝΟ το `.env`.

## 1. VM
- Debian 12/13 (ή Ubuntu Server LTS), **2 vCPU, 2–4 GB RAM, 20 GB δίσκος**.
- **Σταθερή IP** (στατική ή DHCP reservation στον router) — είναι το `DOMAIN`.
- VM, όχι LXC: το Docker σε LXC θέλει nesting/privileged και δεν αξίζει τον κόπο.

## 2. Docker
```bash
curl -fsSL https://get.docker.com | sh
```

## 3. Εφαρμογή
```bash
git clone <repo> misevent && cd misevent     # ή rsync/scp του φακέλου
cp .env.example .env && nano .env            # DOMAIN=<IP>, CADDY_TLS=internal
docker compose up -d --build
docker compose logs -f app                   # «Δεδομένα: /data» = ξεκίνησε
```
Άνοιξε `https://<IP>` και **κάνε αμέσως την «Αρχική ρύθμιση»**: όποιος την κάνει
πρώτος γίνεται ο διαχειριστής του συστήματος.

## 4. Πιστοποιητικό (CADDY_TLS=internal)
Ο Caddy βγάζει πιστοποιητικό από δική του τοπική CA, οπότε ο browser δείχνει
προειδοποίηση. Για να φύγει, εμπιστεύσου τη ρίζα της σε κάθε υπολογιστή:
```bash
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./misevent-root.crt
```
Windows: διπλό κλικ → «Εγκατάσταση πιστοποιητικού» → Τοπικός υπολογιστής →
«Αξιόπιστες κεντρικές αρχές έκδοσης πιστοποιητικών».

⚠️ HTTPS είναι υποχρεωτικό, όχι διακοσμητικό: η online έκδοση στέλνει το cookie
σύνδεσης μόνο πάνω από HTTPS — με σκέτο `http://` η σύνδεση δεν «κρατά».

## 5. Αντίγραφα ασφαλείας
- **Proxmox**: Datacenter → Backup → προγραμματισμένο backup του VM (π.χ. κάθε
  βράδυ, σε άλλο δίσκο/PBS). Περιέχει και το volume με τα δεδομένα.
- **Εφαρμογή**: «Backup» → πλήρες `.zip` (διαχειριστής συστήματος). ⚠️ Περιέχει
  και το κλειδί των κωδικών ΕΡΓΑΝΗ — φύλαξέ το ανάλογα.

## 6. Αναβάθμιση
```bash
git pull && docker compose up -d --build     # τα δεδομένα μένουν στο volume
```

## Αργότερα: δημόσιο domain
`.env`: `DOMAIN=misthodosia.example.gr`, `CADDY_TLS=you@example.gr`, port forward
80/443 στο VM, και ενεργοποίηση του HSTS στο `Caddyfile`. Πριν ανοίξεις σε
πελάτες: SMTP στο `.env` (επιβεβαίωση email στην εγγραφή) και off-site backups.

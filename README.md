# Pokémon-TCG Discord-Bot

Hält dich auf deinem Discord-Server auf dem Laufenden:

- **#neue-sets**: Meldung mit Logo, Release-Datum und Kartenanzahl, sobald ein neues Set erscheint (Quelle: pokemontcg.io). Pingt die Rolle **Set-News**.
- **#tcg-news**: frühe News und Reveals zu kommenden Sets aus PokéBeach, PokéGuardian und PokemonBlog (gefiltert nach Stichworten wie „set“, „elite trainer“, „reveal“).
- **#preisalarme**: Pingt dich, wenn ein Produkt auf deiner Watchlist günstig ist (Quelle: Cardmarket).
- **#preis-graphen**: Hier nutzt du die Befehle, z.B. `/graph` für einen Preisverlauf als Bild.

## #tcg-info

Nur offizielle Pokémon-News (pokemon.com, über Google News), jede Meldung mit Kennzeichnung (🚀 Release, 📣 Ankündigung, 🗓️ Vorbestellung, 💶 Preise & Markt, 🏆 Turniere, 📰 News), Quelle und Datum. Oben angepinnt steht der **Release-Kalender**: kommende und zuletzt erschienene Sets mit Erscheinungsdatum, Countdown und aktuellen Cardmarket-Preisen für Top-Trainer-Box und Display. Er aktualisiert sich alle 6 Stunden. Die Set-Liste steht in `config.json` → `release_calendar`. `/info` postet Kalender und neueste Meldung sofort.

## Shop- und Deal-Monitor

| Kanal | Was dort landet |
|---|---|
| **#vorbestellung** | Neue Vorbestellungen aus Shops, mydealz und Google News |
| **#tcg-artikel** | Neue Pokémon-TCG-Produkte in den überwachten Shops und Pokémon-Deals von mydealz |
| **#restock** | Wieder lieferbare versiegelte Produkte (Displays, TTBs, Tins, Kollektionen, Bundles), keine Einzelbooster oder Einzel-/Grading-Karten (Ping-Rolle **Restock**) |
| **📦 Top-Trainer-Boxen → #ttb-neu** | Neue deutsche, versiegelte TTBs bei seriösen Händlern |
| **📦 Top-Trainer-Boxen → #ttb-billigste** | Angepinnte Übersicht der aktuell günstigsten TTBs (alle 15 Min. aktualisiert), plus Ping sobald eine TTB irgendwo günstiger ist als das bisher günstigste aktuelle Angebot |
| **📦 Top-Trainer-Boxen → #pokemon-center-ttb** | Neue Top-Trainer-Boxen im Pokémon Center, sofort mit Ping der Rolle **Pokémon-Center** (siehe unten) |

**Quellen:** Shops mit offenem Produktkatalog (Shopify/WooCommerce, Liste in `config.json` → `monitor.shops`, erweiterbar per `/shop_hinzufuegen`), mydealz (auch Amazon, MediaMarkt, Pokémon Center), Google News, optional eBay. Shops werden alle 5 Minuten geprüft, mydealz alle 3 Minuten.

**Seriosität:** Nur Shops aus der Liste (geprüft: echter Shop mit Impressum), eBay nur Verkäufer mit ≥ 99 % positiv und ≥ 100 Bewertungen, nur Artikel aus Deutschland. Angebote mit Wörtern wie „leer“, „geöffnet“, „ohne Folie“, „Replica“ oder gegradete Karten werden ignoriert. TTB-Preise unter 25 € gelten als unseriös und werden ignoriert.

**Grenzen:** Amazon, Smyths, Pokémon Center und Shops, die automatische Abfragen verbieten, werden nicht direkt abgefragt, sondern nur über mydealz/Google News erfasst. Shop-Preise sind ohne Versand.

**eBay einrichten (optional):** Auf https://developer.ebay.com ein kostenloses Konto anlegen, eine „Production“-App erstellen und `EBAY_CLIENT_ID` (App ID) und `EBAY_CLIENT_SECRET` (Cert ID) in die `.env` auf dem Server eintragen.

Weitere Befehle: `/ttb` (aktuell günstigste TTBs), `/quellen` (Status aller Quellen), `/shop_hinzufuegen`, `/shop_entfernen`.

Nach einem Update einmal `/setup` ausführen, damit die neuen Kanäle und Rollen angelegt werden.

## Pokémon-Center-Alarm (#pokemon-center-ttb)

Der Bot prüft jede Minute die TTB-Seiten des Pokémon Centers (`config.json` → `pokemon_center.pages`, Deutschland und UK) und postet jede **neue** Top-Trainer-Box sofort mit Ping der Rolle **Pokémon-Center**. Beim ersten Durchlauf merkt er sich nur, was schon da ist.

Ehrlich gesagt: Das Pokémon Center hat einen starken Bot-Schutz (Imperva). Ob der Server durchkommt, zeigt erst der echte Betrieb. Prüfen kannst du es mit `/pokemoncenter` (🟢 = klappt, 🔴 = gesperrt) oder `/quellen`.
- Bei einer Sperre wartet der Bot automatisch länger (2, 4, 8 … bis 60 Min.), damit die Server-IP nicht dauerhaft gesperrt wird.
- **Ersatzweg, läuft immer mit:** mydealz-Deals, bei denen Pokémon Center als Händler genannt ist, landen auch in diesem Kanal (meist wenige Minuten nach Erscheinen).
- Falls die Server-IP dauerhaft gesperrt bleibt: In `.env` kann `PC_PROXY=http://user:pass@host:port` (z.B. ein Wohnnetz-Proxy) eingetragen werden.

## Befehle

| Befehl | Was er macht |
|---|---|
| `/setup` | Legt Kategorie, Kanäle und Ping-Rollen an (nur für Server-Admins). Einmal ausführen. |
| `/preis produkt` | Aktuelle Cardmarket-Preise: günstigstes Angebot, Trend, Ø Verkaufspreis 1/7/30 Tage. |
| `/graph produkt [tage]` | Preisverlauf als Graph (Ø Verkaufspreis, Trend, günstigstes Angebot). |
| `/watch produkt [zielpreis]` | Produkt beobachten. Mit Zielpreis: Alarm sobald es so günstig ist. Ohne: Alarm bei 30-Tage-Tief mind. 10 % unter Trend. |
| `/unwatch produkt` / `/watchlist` | Beobachtung beenden / deine Liste ansehen. |
| `/sets` | Die neuesten Sets. |
| `/pokemoncenter` | Pokémon Center jetzt prüfen: kommt der Bot durch, welche TTBs sind gelistet. |

Produktnamen sind die englischen Cardmarket-Namen. Beim Tippen schlägt der Bot passende Produkte vor (📦 = Sealed, 🃏 = Einzelkarte). Abkürzungen wie `etb`, `upc`, `display` funktionieren.

**Wichtig zum Graph:** Cardmarket stellt nur die *aktuellen* Preise bereit, keine alten Verläufe. Der Bot speichert deshalb jeden Tag die Preise aller Sealed-Produkte (und aller Karten auf einer Watchlist). Die Graphen werden also mit jedem Tag länger, nach ein paar Wochen sind sie richtig aussagekräftig. „Ø Verkaufspreis“ ist der Durchschnitt der tatsächlich auf Cardmarket verkauften Artikel, das kommt den „zuletzt verkauften Preisen“ am nächsten.

---

## Einrichtung (ca. 20 Minuten, einmalig)

### 1. Bot bei Discord anlegen

1. https://discord.com/developers/applications öffnen → **New Application** → Name z.B. „TCG Bot“.
2. Links **Bot** → **Reset Token** → Token kopieren und sicher aufbewahren. Wer den Token hat, kontrolliert den Bot, also nirgends posten.
3. Links **General Information** → **Application ID** kopieren.
4. Diesen Link öffnen (deine ID einsetzen) und deinen Server auswählen:

   `https://discord.com/oauth2/authorize?client_id=DEINE_APPLICATION_ID&scope=bot+applications.commands&permissions=268553232`

   Der Bot bekommt damit nur: Kanäle verwalten, Rollen verwalten, Nachrichten lesen/schreiben, Links einbetten, Dateien anhängen. Kein volles Admin.
5. Optional: In Discord unter *Einstellungen → Erweitert* den **Entwicklermodus** einschalten, Rechtsklick auf deinen Server → **Server-ID kopieren**. Mit der ID erscheinen die Befehle sofort statt erst nach bis zu einer Stunde.

### 2. Kostenlosen Server anlegen, damit der Bot rund um die Uhr läuft

Der Bot muss dauerhaft laufen, auch wenn deine Geräte aus sind. Discord selbst hostet keine Bots, aber Google Cloud hat einen dauerhaft kostenlosen Mini-Server („e2-micro“), der dafür völlig reicht.

1. https://console.cloud.google.com öffnen und mit deinem Google-Konto anmelden. Den kostenlosen Testzeitraum starten. Google will dafür eine Kreditkarte oder PayPal **nur zur Identitätsprüfung**, es wird nichts abgebucht.
2. Oben in der Suche **„Compute Engine“** eingeben → **VM-Instanzen** → API aktivieren, falls gefragt → **Instanz erstellen**.
3. Diese Einstellungen sind wichtig, sonst ist es nicht kostenlos:
   - **Region:** `us-central1` (Iowa), `us-west1` oder `us-east1`. Nur die US-Regionen sind gratis.
   - **Maschinentyp:** `e2-micro`
   - **Bootlaufwerk** → Ändern: **Ubuntu 24.04 LTS**, Laufwerkstyp **„Nichtflüchtiger Standardspeicher“** (Standard persistent disk), Größe **30 GB** oder weniger.
4. **Erstellen** klicken. Rechts oben zeigt Google zwar einen geschätzten Monatspreis, der wird aber durch das kostenlose Kontingent komplett verrechnet.
5. **Nach den 90 Testtagen** fragt Google, ob du auf ein „vollständiges Konto“ upgraden willst. Das musst du bestätigen, damit der Server weiterläuft. Solange nur dieser eine e2-micro läuft, bleibt es bei 0 €. Tipp: Unter *Abrechnung → Budgets* ein Budget von 1 € mit E-Mail-Warnung anlegen, dann merkst du sofort, falls doch mal etwas kostet.

### 3. Bot auf den Server bringen und starten

1. In der Liste der VM-Instanzen bei deinem Server auf **SSH** klicken. Es öffnet sich ein Terminal direkt im Browser, du brauchst nichts zu installieren.
2. Oben rechts im SSH-Fenster auf **„Datei hochladen“** klicken und `tcg-bot.zip` hochladen.
3. Dann diese Befehle einzeln reinkopieren:

```bash
# Docker installieren
curl -fsSL https://get.docker.com | sudo sh

# Bot entpacken
sudo apt-get install -y unzip && unzip tcg-bot.zip -d tcg-bot
cd tcg-bot

# Token eintragen
cp .env.example .env
nano .env      # DISCORD_TOKEN=... und GUILD_ID=... eintragen, dann Strg+O, Enter, Strg+X

# Starten (startet auch nach einem Server-Neustart automatisch wieder)
sudo docker compose up -d --build

# Log ansehen (Strg+C beendet nur die Anzeige, nicht den Bot)
sudo docker compose logs -f
```

Im Log sollte nach kurzer Zeit `Katalog aktualisiert: ... Produkte` stehen. Das SSH-Fenster kannst du danach einfach schließen, der Bot läuft weiter.

### 4. In Discord einrichten

1. In irgendeinem Kanal `/setup` eingeben.
2. Der Bot legt die Kategorie **📈 Pokémon TCG** mit den vier Kanälen und die Rollen **Set-News** und **Preisalarm** an.
3. In #preis-graphen postet er Buttons, mit denen sich jeder die Ping-Rollen selbst geben kann.
4. Los geht's, z.B.: `/watch produkt:prismatic etb zielpreis:85`

## Anpassen

In `config.json` kannst du Kanalnamen, News-Quellen, Stichworte, Prüf-Intervalle und die Prozentschwelle für Preisstürze ändern. Danach `sudo docker compose up -d --build`.

## Update / Neustart / Stoppen

```bash
cd tcg-bot
sudo docker compose restart        # neu starten
sudo docker compose down           # stoppen
sudo docker compose up -d --build  # nach Änderungen neu bauen
```

Der Preisverlauf liegt in `data/tcgbot.db` und bleibt bei Updates erhalten.

## Grenzen

- Preise kommen von Cardmarket (Deutschland/EU). Amazon und eBay sind nicht drin, weil es dafür keine frei nutzbaren Preisdaten gibt.
- Cardmarket aktualisiert seine Preisdatei einmal täglich. Alarme kommen also höchstens einmal am Tag pro Produkt, nicht in Echtzeit.
- „Günstigstes Angebot“ gilt über alle Sprachen und Zustände. Für Sealed passt das meist, bei Einzelkarten lohnt ein Blick auf Cardmarket.
- Neue Sets erscheinen bei pokemontcg.io meist kurz vor Release. Die frühen Infos (Ankündigungen, Japan-Sets, Reveals) kommen über #tcg-news.

## Automatische Updates

Der Server holt sich alle 15 Minuten den neuesten Stand aus diesem GitHub-Repo (`update.sh` per cron) und baut den Bot neu, wenn sich etwas geändert hat. Log: `sudo tail /var/log/tcgbot-update.log`.

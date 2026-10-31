# Pokémon-TCG Discord-Bot

Hält dich auf deinem Discord-Server auf dem Laufenden:

- **#neue-sets**: Meldung mit Logo, Release-Datum und Kartenanzahl, sobald ein neues Set erscheint (Quelle: pokemontcg.io). Pingt die Rolle **Set-News**.
- **#tcg-news**: frühe News und Reveals zu kommenden Sets aus PokéBeach, PokéGuardian und PokemonBlog (gefiltert nach Stichworten wie „set“, „elite trainer“, „reveal“).
- **#preisalarme**: Pingt dich, wenn ein Produkt auf deiner Watchlist günstig ist (Quelle: Cardmarket).
- **#preis-graphen**: Hier nutzt du die Befehle, z.B. `/graph` für einen Preisverlauf als Bild.

## Befehle

| Befehl | Was er macht |
|---|---|
| `/setup` | Legt Kategorie, Kanäle und Ping-Rollen an (nur für Server-Admins). Einmal ausführen. |
| `/preis produkt` | Aktuelle Cardmarket-Preise: günstigstes Angebot, Trend, Ø Verkaufspreis 1/7/30 Tage. |
| `/graph produkt [tage]` | Preisverlauf als Graph (Ø Verkaufspreis, Trend, günstigstes Angebot). |
| `/watch produkt [zielpreis]` | Produkt beobachten. Mit Zielpreis: Alarm sobald es so günstig ist. Ohne: Alarm bei 30-Tage-Tief mind. 10 % unter Trend. |
| `/unwatch produkt` / `/watchlist` | Beobachtung beenden / deine Liste ansehen. |
| `/sets` | Die neuesten Sets. |

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

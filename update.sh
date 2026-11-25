#!/bin/sh
# Wird per cron alle 15 Minuten ausgeführt: holt neuen Code von GitHub und baut/startet den Bot neu,
# falls sich etwas geändert hat. Schlägt der Bau fehl (z.B. Speicher voll), wird es beim nächsten Lauf
# erneut versucht (Merker .last-built), statt den alten Stand für immer weiterlaufen zu lassen.
cd "$(dirname "$0")" || exit 1
G="git -c safe.directory=*"
$G fetch -q origin main || exit 1
[ "$($G rev-parse HEAD)" != "$($G rev-parse origin/main)" ] && $G reset -q --hard origin/main
HEAD=$($G rev-parse --short HEAD)
[ "$(cat .last-built 2>/dev/null)" = "$HEAD" ] && exit 0
docker image prune -f >/dev/null 2>&1   # alte Images löschen, damit der kleine Server nicht vollläuft
if docker compose up -d --build; then
    echo "$HEAD" > .last-built
    echo "$(date): aktualisiert auf $HEAD"
else
    echo "$(date): FEHLER beim Bauen von $HEAD, neuer Versuch in 15 Min."
fi

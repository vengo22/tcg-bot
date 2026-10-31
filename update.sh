#!/bin/sh
# Wird per cron alle 15 Minuten ausgeführt: holt neuen Code von GitHub und startet den Bot neu, falls sich etwas geändert hat.
cd "$(dirname "$0")" || exit 1
git -c safe.directory='*' fetch -q origin main || exit 1
if [ "$(git -c safe.directory='*' rev-parse HEAD)" != "$(git -c safe.directory='*' rev-parse origin/main)" ]; then
    git -c safe.directory='*' reset -q --hard origin/main
    docker compose up -d --build
    echo "$(date): aktualisiert auf $(git -c safe.directory='*' rev-parse --short HEAD)"
fi

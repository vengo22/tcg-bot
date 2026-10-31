"""Datenquellen: Cardmarket-Preisdateien, pokemontcg.io-Sets, News-RSS-Feeds."""
import asyncio
import json
import logging

import aiohttp
import feedparser

log = logging.getLogger(__name__)

# Cardmarket veröffentlicht täglich öffentliche JSON-Dateien. 6 = Pokémon.
CM_BASE = "https://downloads.s3.cardmarket.com/productCatalog"
CM_PRICE_GUIDE = f"{CM_BASE}/priceGuide/price_guide_6.json"
CM_SEALED = f"{CM_BASE}/productList/products_nonsingles_6.json"
CM_SINGLES = f"{CM_BASE}/productList/products_singles_6.json"
CM_PRODUCT_URL = "https://www.cardmarket.com/de/Pokemon/Products?idProduct={id}"

PTCG_SETS = "https://api.pokemontcg.io/v2/sets"

HEADERS = {"User-Agent": "tcg-discord-bot/1.0 (privater Hobby-Bot)"}
# Manche News-Seiten blocken unbekannte Clients, daher für Feeds ein Browser-Header
FEED_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
}
TIMEOUT = aiohttp.ClientTimeout(total=120)


async def _get_json(session: aiohttp.ClientSession, url: str, **kw):
    async with session.get(url, headers=HEADERS | kw.pop("headers", {}), timeout=TIMEOUT, **kw) as r:
        r.raise_for_status()
        raw = await r.read()
    # Große Dateien im Hintergrund-Thread parsen, damit der Bot weiter auf Befehle reagiert
    return await asyncio.to_thread(json.loads, raw)


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


# --- Cardmarket ---------------------------------------------------------------
async def fetch_cardmarket_catalog(session):
    """Liefert [(id, name, category, expansion_id, sealed)] für Sealed + Einzelkarten."""
    rows = []
    for url, sealed in ((CM_SEALED, 1), (CM_SINGLES, 0)):
        try:
            data = await _get_json(session, url)
        except Exception:
            log.exception("Cardmarket-Katalog %s nicht ladbar", url)
            continue
        rows.extend(await asyncio.to_thread(_catalog_rows, data, sealed))
    return rows


async def fetch_cardmarket_prices(session):
    """Liefert (createdAt, {product_id: (low, trend, avg1, avg7, avg30)}).

    avg1/avg7/avg30 = Durchschnittspreis der tatsächlich VERKAUFTEN Artikel
    der letzten 1/7/30 Tage; low = günstigstes aktuelles Angebot; trend = Cardmarket-Trendpreis.
    """
    data = await _get_json(session, CM_PRICE_GUIDE)
    out = await asyncio.to_thread(_price_map, data)
    return data.get("createdAt", ""), out


def _catalog_rows(data, sealed):
    return [
        (int(p["idProduct"]), p["name"], p.get("categoryName"), p.get("idExpansion"), sealed)
        for p in data.get("products", []) if p.get("idProduct") and p.get("name")
    ]


def _price_map(data):
    out = {}
    for g in data.get("priceGuides", []):
        pid = g.get("idProduct")
        if pid is not None:
            out[int(pid)] = tuple(_num(g.get(k)) for k in ("low", "trend", "avg1", "avg7", "avg30"))
    return out


# --- Sets (pokemontcg.io, englische Sets) --------------------------------------
async def fetch_latest_sets(session, api_key: str | None = None, count: int = 15):
    headers = {"X-Api-Key": api_key} if api_key else {}
    data = await _get_json(
        session, PTCG_SETS,
        params={"orderBy": "-releaseDate", "pageSize": str(count)}, headers=headers,
    )
    return data.get("data", [])


# --- News-Feeds -----------------------------------------------------------------
async def fetch_feed(session, url: str):
    """Liefert [(id, titel, link, zusammenfassung)]."""
    async with session.get(url, headers=FEED_HEADERS, timeout=TIMEOUT) as r:
        r.raise_for_status()
        text = await r.text()
    feed = await asyncio.to_thread(feedparser.parse, text)
    items = []
    for e in feed.entries:
        key = e.get("id") or e.get("link") or e.get("title")
        if key:
            items.append((key, e.get("title", ""), e.get("link", ""), e.get("summary", "")))
    return items

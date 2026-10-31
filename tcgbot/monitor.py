"""Shop- und Deal-Monitor: neue Produkte, Vorbestellungen, Restocks und günstigste Top-Trainer-Boxen.

Quellen:
- Shops mit offenem Produktkatalog (Shopify `/products.json`, WooCommerce Store-API)
- mydealz (Community-geprüfte Deals, auch Amazon/MediaMarkt/Pokémon Center)
- Google News (Artikel auch von kleinen Seiten)
- eBay Browse-API (optional, braucht EBAY_CLIENT_ID / EBAY_CLIENT_SECRET)
"""
import asyncio
import base64
import difflib
import html
import json
import logging
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus, urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger("tcgbot.monitor")

TIMEOUT = aiohttp.ClientTimeout(total=40)
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.6",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    url TEXT PRIMARY KEY,
    kind TEXT NOT NULL,          -- shopify | woocommerce | mydealz | gnews | ebay
    name TEXT NOT NULL,
    added_by INTEGER,
    last_ok INTEGER,
    last_error TEXT,
    items INTEGER DEFAULT 0,
    seeded INTEGER DEFAULT 0     -- 1 = erster Durchlauf erledigt (vorher wird nichts gepostet)
);
CREATE TABLE IF NOT EXISTS offers (
    url TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    price REAL,
    available INTEGER,
    ttb_key TEXT,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_offers_ttb ON offers(ttb_key);
"""

# ------------------------------------------------------------------ Erkennung
POKEMON_RE = re.compile(r"pok[eé]mon", re.I)
TCG_RE = re.compile(r"karte|card|booster|display|tcg|sammelkarten|trainer|blister|tin\b|kollektion|"
                    r"collection|deck|etb|ttb|bundle|premium|box", re.I)
TTB_RE = re.compile(r"top[\s-]*trainer[\s-]*box|elite[\s-]*trainer[\s-]*box|\bttb\b|\betb\b", re.I)
GERMAN_RE = re.compile(r"deutsch|\(de\)|\bde\b|german|\bger\b", re.I)
FOREIGN_RE = re.compile(r"englisch|english|\(en\)|\ben\b|japan|\bjp\b|\bjpn\b|korea|chines|franz|french|"
                        r"\(fr\)|italien|\(it\)|spanisch|\(es\)|thai|indones", re.I)
NOT_SEALED_RE = re.compile(r"\bleer|ohne inhalt|ge[öo]ffnet|opened|empty|nur (die )?box|besch[äa]digt|defekt|"
                           r"ohne folie|ohne booster|replica|fake|proxy|sleeves|h[üu]llen|nur promo|einzeln|"
                           r"lose|deko|aufbewahrung|organizer|3d[- ]?druck|\bcase\b|acryl|acrylic|\bpsa\b|"
                           r"\bcgc\b|\bgrad", re.I)
PREORDER_RE = re.compile(r"vorbestell|pre[\s-]?order|vorverkauf|vorab|erscheint am|release[: ]|"
                         r"erscheinungstermin|reservier", re.I)
# Wörter, die beim Vergleich von TTBs verschiedener Shops ignoriert werden
TTB_NOISE = set("""pokemon pokémon top trainer box ttb etb elite deutsch de german ger karmesin purpur kp sv
sammelkartenspiel tcg the company nintendo und & - + ovp sealed neu new versiegelt original edition mega
entwicklung karten sammelkarten booster packs pack inkl mit promo promokarte 9 10 x the of zubehoer zubehor
vorbestellung vorbestellen preorder pre order vorverkauf release vorab neuheit lieferbar ab""".split())


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower().replace("ß", "ss"))
    return "".join(c for c in s if not unicodedata.combining(c))


def classify(title: str, extra: str = "", default_german: bool = True) -> dict:
    t = f"{title} {extra}"
    german = bool(GERMAN_RE.search(title)) or (default_german and not FOREIGN_RE.search(title))
    return {
        "pokemon": bool(POKEMON_RE.search(t)),
        "tcg": bool(TCG_RE.search(title)),
        "ttb": bool(TTB_RE.search(title)),
        "german": german and not FOREIGN_RE.search(title),
        "sealed": not NOT_SEALED_RE.search(title),
        "preorder": bool(PREORDER_RE.search(t)),
    }


def ttb_key(title: str) -> str:
    t = norm(title)
    t = re.sub(r"\b(kp|sv|swsh|sm|me)\s?\d+(\.\d+)?\b", " ", t)      # Set-Kürzel wie KP08.5
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    words = sorted({w for w in t.split() if w not in TTB_NOISE and len(w) > 1 and not w.isdigit()})
    return " ".join(words)


def parse_price(text) -> float | None:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = re.search(r"(\d{1,4}(?:[.\s]\d{3})*(?:[,.]\d{1,2})?)\s*(?:€|eur)", str(text), re.I) \
        or re.search(r"(\d{1,4}(?:[,.]\d{1,2})?)", str(text))
    if not m:
        return None
    v = m.group(1).replace(" ", "")
    if "," in v:
        v = v.replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def euro(v) -> str:
    return "–" if v is None else f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def host(url: str) -> str:
    return urlparse(url if "//" in url else "https://" + url).netloc.removeprefix("www.")


# ------------------------------------------------------------------ Abruf
async def fetch_shopify(session, base: str, pages: int):
    items = []
    for page in range(1, pages + 1):
        async with session.get(f"{base}/products.json", params={"limit": "250", "page": str(page)},
                               headers=BROWSER_HEADERS, timeout=TIMEOUT) as r:
            r.raise_for_status()
            data = json.loads(await r.read())
        products = data.get("products", [])
        for p in products:
            variants = p.get("variants") or []
            avail = [v for v in variants if v.get("available")]
            price_src = avail or variants
            prices = [parse_price(v.get("price")) for v in price_src if v.get("price") is not None]
            tags = p.get("tags") or []
            tags = tags if isinstance(tags, list) else str(tags).split(",")
            items.append({
                "url": f"{base}/products/{p.get('handle')}",
                "title": p.get("title", ""),
                "extra": " ".join(tags) + " " + (p.get("product_type") or ""),
                "price": min(prices) if prices else None,
                "available": bool(avail),
                "image": ((p.get("images") or [{}])[0] or {}).get("src"),
            })
        if len(products) < 250:
            break
    return items


async def fetch_woocommerce(session, base: str, pages: int):
    items = []
    for page in range(1, pages + 1):
        async with session.get(f"{base}/wp-json/wc/store/v1/products",
                               params={"search": "pokemon", "per_page": "100", "page": str(page),
                                       "orderby": "date", "order": "desc"},
                               headers=BROWSER_HEADERS, timeout=TIMEOUT) as r:
            r.raise_for_status()
            data = json.loads(await r.read())
        for p in data:
            pr = p.get("prices") or {}
            if pr.get("currency_code") not in (None, "EUR"):
                continue
            minor = int(pr.get("currency_minor_unit") or 2)
            price = int(pr["price"]) / 10 ** minor if str(pr.get("price", "")).isdigit() else None
            stock = (p.get("stock_availability") or {}).get("text", "")
            items.append({
                "url": p.get("permalink"),
                "title": html.unescape(p.get("name", "")),
                "extra": stock,
                "price": price,
                "available": bool(p.get("is_in_stock")),
                "image": ((p.get("images") or [{}])[0] or {}).get("src"),
            })
        if len(data) < 100:
            break
    return items


def _rss_items(raw: bytes):
    root = ET.fromstring(raw)
    for it in root.iter("item"):
        d = {"title": html.unescape(it.findtext("title") or ""), "url": it.findtext("link") or "",
             "description": it.findtext("description") or "", "merchant": None, "price": None}
        for child in it:
            if child.tag.endswith("merchant"):  # mydealz: <pepper:merchant name=".." price=".."/>
                d["merchant"] = child.attrib.get("name")
                d["price"] = parse_price(child.attrib.get("price"))
        yield d


async def fetch_rss(session, url: str):
    async with session.get(url, headers=BROWSER_HEADERS, timeout=TIMEOUT) as r:
        r.raise_for_status()
        raw = await r.read()
    return await asyncio.to_thread(lambda: list(_rss_items(raw)))


class EbayClient:
    """eBay Browse-API (offiziell, kostenlos mit Entwicklerkonto)."""

    def __init__(self, client_id, client_secret):
        self.client_id, self.client_secret = client_id, client_secret
        self.token, self.expires = None, 0

    async def _token(self, session):
        if self.token and time.time() < self.expires - 60:
            return self.token
        auth = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        async with session.post("https://api.ebay.com/identity/v1/oauth2/token",
                                headers={"Authorization": f"Basic {auth}",
                                         "Content-Type": "application/x-www-form-urlencoded"},
                                data={"grant_type": "client_credentials",
                                      "scope": "https://api.ebay.com/oauth/api_scope"},
                                timeout=TIMEOUT) as r:
            r.raise_for_status()
            d = await r.json()
        self.token, self.expires = d["access_token"], time.time() + int(d.get("expires_in", 7200))
        return self.token

    async def search(self, session, query, min_feedback_pct, min_feedback_score):
        token = await self._token(session)
        params = {
            "q": query, "limit": "100", "sort": "newlyListed",
            "filter": "conditions:{NEW},buyingOptions:{FIXED_PRICE},itemLocationCountry:DE,"
                      "price:[15..300],priceCurrency:EUR",
        }
        async with session.get("https://api.ebay.com/buy/browse/v1/item_summary/search", params=params,
                               headers={"Authorization": f"Bearer {token}",
                                        "X-EBAY-C-MARKETPLACE-ID": "EBAY_DE"}, timeout=TIMEOUT) as r:
            r.raise_for_status()
            d = await r.json()
        items = []
        for it in d.get("itemSummaries", []):
            seller = it.get("seller") or {}
            pct = float(seller.get("feedbackPercentage") or 0)
            score = int(seller.get("feedbackScore") or 0)
            if pct < min_feedback_pct or score < min_feedback_score:
                continue  # nicht seriös genug
            price = parse_price((it.get("price") or {}).get("value"))
            ship = None
            for so in it.get("shippingOptions") or []:
                ship = parse_price((so.get("shippingCost") or {}).get("value"))
                break
            items.append({
                "url": it.get("itemWebUrl"), "title": it.get("title", ""), "extra": "",
                "price": (price or 0) + (ship or 0) if price is not None else None,
                "available": True, "image": (it.get("image") or {}).get("imageUrl"),
                "seller": f"{seller.get('username')} ({pct:g} %, {score} Bewertungen)",
                "shipping_included": True,
            })
        return items


# ------------------------------------------------------------------ Cog
class MonitorCog(commands.Cog):
    def __init__(self, bot, config: dict, send_to):
        self.bot, self.db, self.cfg, self.send_to = bot, bot.db, config["monitor"], send_to
        self.db.conn.executescript(SCHEMA)
        self.db.conn.commit()
        for url in self.cfg["shops"]:
            self._ensure_source(url.rstrip("/"), None, host(url))
        for url in self.cfg["deal_feeds"]:
            self._ensure_source(url, "mydealz", "mydealz")
        for q in self.cfg["google_news"]:
            self._ensure_source(self._gnews_url(q), "gnews", f"Google News: {q['q']}")
        cid, secret = os.getenv("EBAY_CLIENT_ID"), os.getenv("EBAY_CLIENT_SECRET")
        self.ebay = EbayClient(cid, secret) if cid and secret else None
        if self.ebay:
            for q in self.cfg["ebay"]["queries"]:
                self._ensure_source(f"ebay:{q}", "ebay", f"eBay: {q}")
        iv = self.cfg["intervals_minutes"]
        self.shop_loop.change_interval(minutes=iv["shops"])
        self.deal_loop.change_interval(minutes=iv["deals"])
        self.gnews_loop.change_interval(minutes=iv["google_news"])
        self.ebay_loop.change_interval(minutes=iv["ebay"])
        for loop in (self.shop_loop, self.deal_loop, self.gnews_loop, self.ebay_loop):
            loop.start()

    def cog_unload(self):
        for loop in (self.shop_loop, self.deal_loop, self.gnews_loop, self.ebay_loop):
            loop.cancel()

    @staticmethod
    def _gnews_url(q):
        lang = q.get("lang", "de")
        region = "DE" if lang == "de" else "US"
        return (f"https://news.google.com/rss/search?q={quote_plus(q['q'])}"
                f"&hl={lang}&gl={region}&ceid={region}:{lang}")

    def _ensure_source(self, url, kind, name, added_by=None):
        self.db.conn.execute(
            "INSERT OR IGNORE INTO sources (url, kind, name, added_by) VALUES (?,?,?,?)",
            (url, kind or "unknown", name, added_by))
        self.db.conn.commit()

    def _sources(self, kinds):
        q = ",".join("?" * len(kinds))
        return self.db.conn.execute(f"SELECT * FROM sources WHERE kind IN ({q})", kinds).fetchall()

    def _mark(self, url, ok: bool, items=0, error=None):
        """Status einer Quelle speichern (schreibt auch die gesammelten Angebots-Änderungen)."""
        if ok:
            self.db.conn.execute("UPDATE sources SET last_ok=?, last_error=NULL, items=?, seeded=1 WHERE url=?",
                                 (int(time.time()), items, url))
        else:
            self.db.conn.execute("UPDATE sources SET last_error=? WHERE url=?", (str(error)[:200], url))
        self.db.conn.commit()

    async def detect_platform(self, base: str):
        s = self.bot.http_session
        for kind, fn in (("shopify", fetch_shopify), ("woocommerce", fetch_woocommerce)):
            try:
                items = await fn(s, base, 1)
                return kind, len(items)
            except Exception:
                continue
        return None, 0

    # -------------------------------------------------------------- Angebote verarbeiten
    def _ttb_best(self, key, exclude_url=None, max_age_h=48):
        """Aktuell günstigstes gültiges Angebot für eine TTB (ähnliche Namen werden zusammengefasst)."""
        return self.db.conn.execute(
            "SELECT * FROM offers WHERE ttb_key=? AND available=1 AND price IS NOT NULL AND url != ? "
            "AND last_seen >= ? ORDER BY price LIMIT 1",
            (key, exclude_url or "", int(time.time()) - max_age_h * 3600)).fetchone()

    def _canonical_key(self, key):
        """Fasst leicht unterschiedlich benannte TTBs (verschiedene Shops) zu einem Schlüssel zusammen."""
        if not key:
            return key
        keys = [r[0] for r in self.db.conn.execute("SELECT DISTINCT ttb_key FROM offers WHERE ttb_key IS NOT NULL")]
        if key in keys:
            return key
        best = difflib.get_close_matches(key, keys, n=1, cutoff=0.82)
        return best[0] if best else key

    async def handle_offer(self, src, item, seeded: bool, post_new=True, default_german=True):
        title, url = item["title"], item["url"]
        if not url or not title:
            return
        c = classify(title, item.get("extra", ""), default_german)
        if not c["pokemon"]:
            return
        now = int(time.time())
        price, available = item.get("price"), item.get("available", True)
        is_ttb_deal = c["ttb"] and c["german"] and c["sealed"] and price is not None \
            and self.cfg["ttb_min_price"] <= price <= self.cfg["ttb_max_price"]

        old = self.db.conn.execute("SELECT * FROM offers WHERE url=?", (url,)).fetchone()
        if old is not None and old["price"] == price and bool(old["available"]) == bool(available) \
                and old["title"] == title:
            # unverändert: nur "zuletzt gesehen" aktualisieren (Commit erfolgt gesammelt pro Quelle)
            self.db.conn.execute("UPDATE offers SET last_seen=? WHERE url=?", (now, url))
            return
        key = self._canonical_key(ttb_key(title)) if is_ttb_deal else None
        prev_best = self._ttb_best(key, exclude_url=url) if key else None
        self.db.conn.execute(
            "INSERT INTO offers (url, source, title, price, available, ttb_key, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET title=excluded.title, price=excluded.price, "
            "available=excluded.available, ttb_key=excluded.ttb_key, last_seen=excluded.last_seen",
            (url, src["name"], title, price, int(bool(available)), key, now, now))
        if not seeded:
            return  # erster Durchlauf einer Quelle: nur merken, nichts posten

        shop = src["name"]
        if old is None and post_new:
            if item.get("deal") and not c["preorder"] and not c["ttb"]:
                await self.post("articles", "🔥 Deal", item, shop, c)
            if c["preorder"]:
                await self.post("preorder", "🗓️ Vorbestellung", item, shop, c, ping="preorder")
            if c["ttb"] and c["german"] and c["sealed"]:
                await self.post("ttb_new", "🆕 Neue Top-Trainer-Box", item, shop, c, ping="ttb")
            elif not c["preorder"] and c["tcg"] and not item.get("deal"):
                await self.post("articles", "🆕 Neu im Shop", item, shop, c)
        elif old is not None and available and not old["available"]:
            await self.post("restock", "🔁 Wieder lieferbar", item, shop, c, ping="restock")

        if key and available:
            beats = prev_best is None or price < prev_best["price"] - 0.01
            was_cheaper_before = old is not None and old["price"] is not None and old["available"] \
                and old["price"] <= price
            first_ever = prev_best is None and old is None  # steht dann schon in #ttb-neu
            if beats and not was_cheaper_before and not first_ever:
                note = (f"Bisher günstigstes: {euro(prev_best['price'])} bei {prev_best['source']}"
                        if prev_best else "Erstes seriöses Angebot für diese Box")
                await self.post("ttb_cheap", "💸 Neuer Tiefstpreis", item, shop, c, ping="ttb", note=note)

    async def post(self, kind, headline, item, shop, c, ping=None, note=None):
        e = discord.Embed(title=item["title"][:256], url=item["url"], color=0xFFCB05)
        e.set_author(name=f"{headline} · {shop}")
        price_txt = euro(item.get("price"))
        if item.get("price") is not None and not item.get("shipping_included"):
            price_txt += " zzgl. Versand"
        e.add_field(name="Preis", value=price_txt)
        e.add_field(name="Status", value="✅ lieferbar" if item.get("available", True) else "❌ ausverkauft")
        if c["preorder"]:
            e.add_field(name="Art", value="Vorbestellung")
        if item.get("seller"):
            e.add_field(name="Verkäufer", value=item["seller"], inline=False)
        if note:
            e.description = note
        if item.get("image"):
            e.set_thumbnail(url=item["image"])
        await self.send_to(kind, embed=e, ping_role=ping)

    # -------------------------------------------------------------- Schleifen
    @tasks.loop(minutes=5)
    async def shop_loop(self):
        s = self.bot.http_session
        for src in self._sources(["shopify", "woocommerce", "unknown"]):
            kind = src["kind"]
            if kind == "unknown":
                kind, _ = await self.detect_platform(src["url"])
                if not kind:
                    self._mark(src["url"], False, error="Kein lesbarer Produktkatalog (kein Shopify/WooCommerce)")
                    continue
                self.db.conn.execute("UPDATE sources SET kind=? WHERE url=?", (kind, src["url"]))
            fn = fetch_shopify if kind == "shopify" else fetch_woocommerce
            try:
                items = await fn(s, src["url"], self.cfg["shop_pages"])
            except Exception as ex:
                self._mark(src["url"], False, error=ex)
                log.warning("Shop %s: %s", src["url"], ex)
                continue
            for it in items:
                await self.handle_offer(src, it, bool(src["seeded"]))
            self._mark(src["url"], True, len(items))
            await asyncio.sleep(1)  # Shops nicht gleichzeitig belasten

    @tasks.loop(minutes=3)
    async def deal_loop(self):
        for src in self._sources(["mydealz"]):
            try:
                items = await fetch_rss(self.bot.http_session, src["url"])
            except Exception as ex:
                self._mark(src["url"], False, error=ex)
                continue
            for it in items:
                merchant = it["merchant"] or "mydealz"
                price = it["price"] or parse_price(it["title"])
                fake_src = {"name": f"{merchant} (via mydealz)"}
                c = classify(it["title"])
                if not (c["pokemon"] and c["tcg"]):
                    continue
                await self.handle_offer(fake_src, {"url": it["url"], "title": it["title"], "price": price,
                                                   "available": True, "extra": "", "deal": True},
                                        bool(src["seeded"]))
            self._mark(src["url"], True, len(items))

    @tasks.loop(minutes=20)
    async def gnews_loop(self):
        for q in self.cfg["google_news"]:
            url = self._gnews_url(q)
            src = self.db.conn.execute("SELECT * FROM sources WHERE url=?", (url,)).fetchone()
            try:
                items = await fetch_rss(self.bot.http_session, url)
            except Exception as ex:
                self._mark(url, False, error=ex)
                continue
            channel = "preorder" if q.get("kind") == "preorder" else "news"
            for it in items:
                if not POKEMON_RE.search(it["title"]):
                    continue
                if not self.db.mark_seen("gnews", it["title"][:200]) or not src["seeded"]:
                    continue
                e = discord.Embed(title=it["title"][:256], url=it["url"], color=0x3B4CCA)
                e.set_footer(text="Google News")
                await self.send_to(channel, embed=e)
            self._mark(url, True, len(items))

    @tasks.loop(minutes=15)
    async def ebay_loop(self):
        if not self.ebay:
            return
        e = self.cfg["ebay"]
        for q in e["queries"]:
            url = f"ebay:{q}"
            src = self.db.conn.execute("SELECT * FROM sources WHERE url=?", (url,)).fetchone()
            try:
                items = await self.ebay.search(self.bot.http_session, q, e["min_feedback_percent"],
                                               e["min_feedback_score"])
            except Exception as ex:
                self._mark(url, False, error=ex)
                continue
            for it in items:
                # eBay: keine "Neu"-Meldungen für jedes Inserat, nur Tiefstpreise
                await self.handle_offer({"name": "eBay"}, it, bool(src["seeded"]), post_new=False)
            self._mark(url, True, len(items))

    @shop_loop.before_loop
    @deal_loop.before_loop
    @gnews_loop.before_loop
    @ebay_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    # -------------------------------------------------------------- Befehle
    @app_commands.command(name="shop_hinzufuegen", description="Einen Online-Shop zur Überwachung hinzufügen.")
    @app_commands.describe(link="Startseite des Shops, z.B. https://beispiel-shop.de")
    @app_commands.default_permissions(manage_guild=True)
    async def shop_add(self, interaction: discord.Interaction, link: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if "//" not in link:
            link = "https://" + link
        p = urlparse(link)
        base = f"{p.scheme}://{p.netloc}"
        kind, n = await self.detect_platform(base)
        if not kind:
            return await interaction.followup.send(
                f"❌ Bei **{p.netloc}** kann ich den Produktkatalog nicht automatisch lesen "
                "(kein Shopify/WooCommerce oder gesperrt).", ephemeral=True)
        self._ensure_source(base, kind, host(base), interaction.user.id)
        self.db.conn.execute("UPDATE sources SET kind=? WHERE url=?", (kind, base))
        self.db.conn.commit()
        await interaction.followup.send(
            f"✅ **{host(base)}** wird jetzt überwacht ({kind}, {n} Produkte gefunden). "
            "Beim ersten Durchlauf merke ich mir nur den Bestand, danach melde ich Neues.", ephemeral=True)

    @app_commands.command(name="shop_entfernen", description="Einen Shop nicht mehr überwachen.")
    @app_commands.default_permissions(manage_guild=True)
    async def shop_remove(self, interaction: discord.Interaction, link: str):
        n = self.db.conn.execute("DELETE FROM sources WHERE url LIKE ? AND kind IN ('shopify','woocommerce','unknown')",
                                 (f"%{host(link)}%",)).rowcount
        self.db.conn.commit()
        await interaction.response.send_message("Entfernt." if n else "Nicht gefunden.", ephemeral=True)

    @app_commands.command(name="quellen", description="Status aller überwachten Shops und Quellen.")
    async def sources_cmd(self, interaction: discord.Interaction):
        rows = self.db.conn.execute("SELECT * FROM sources ORDER BY kind, name").fetchall()
        lines = []
        for r in rows:
            ok = r["last_ok"] and (not r["last_error"])
            icon = "🟢" if ok else ("🟡" if r["last_ok"] else "🔴")
            extra = f"{r['items']} Einträge" if ok else (r["last_error"] or "noch nicht geprüft")
            lines.append(f"{icon} **{r['name']}** ({r['kind']}) – {extra}"[:150])
        text = "\n".join(lines) or "Keine Quellen."
        if not self.ebay:
            text += "\n⚪ eBay – nicht eingerichtet (braucht eBay-Entwicklerkonto)"
        chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)]
        await interaction.response.send_message(chunks[0], ephemeral=True)
        for ch in chunks[1:]:
            await interaction.followup.send(ch, ephemeral=True)

    @app_commands.command(name="ttb", description="Aktuell günstigste deutsche Top-Trainer-Boxen (seriöse Quellen).")
    async def ttb_cmd(self, interaction: discord.Interaction):
        rows = self.db.conn.execute(
            "SELECT ttb_key, title, url, source, MIN(price) AS price FROM offers "
            "WHERE ttb_key IS NOT NULL AND available=1 AND price IS NOT NULL AND last_seen >= ? "
            "GROUP BY ttb_key ORDER BY title LIMIT 25", (int(time.time()) - 48 * 3600,)).fetchall()
        if not rows:
            return await interaction.response.send_message("Noch keine Daten, ich sammle gerade.", ephemeral=True)
        lines = [f"• [{r['title'][:70]}]({r['url']}) – **{euro(r['price'])}** ({r['source']})" for r in rows]
        await interaction.response.send_message("\n".join(lines)[:1990], suppress_embeds=True)

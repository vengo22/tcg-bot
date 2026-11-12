"""Pokémon-Center-Wächter: meldet neue Top-Trainer-Boxen (Elite Trainer Boxes) im Pokémon Center.

Zwei Wege, weil das Pokémon Center einen Bot-Schutz (Imperva) hat:
1. Direkt: Kategorie-/Suchseiten aus config.json → pokemon_center.pages werden jede Minute abgerufen
   und nach Produkt-Links (/xx-xx/product/...) durchsucht. Neue TTB-Links → sofort Post mit Ping.
   Wird der Bot ausgesperrt, wartet er automatisch länger (kein Dauerfeuer, das die IP sperren lässt)
   und zeigt den Status in /quellen bzw. /pokemoncenter.
2. Ersatzweg: mydealz-Deals, bei denen Pokémon Center als Händler genannt ist (läuft immer mit).

Optional: PC_PROXY=http://user:pass@host:port in .env, falls die Server-IP dauerhaft gesperrt ist.
"""
import asyncio
import html
import json
import logging
import os
import random
import re
import time
from urllib.parse import urljoin, urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from .monitor import BROWSER_HEADERS, TTB_RE, fetch_rss, parse_price, euro

log = logging.getLogger("tcgbot.pokemoncenter")

TIMEOUT = aiohttp.ClientTimeout(total=30)
PRODUCT_PATH_RE = re.compile(r"""(?:https?://www\.pokemoncenter\.com)?(/[a-z]{2}-[a-z]{2}/product/[A-Za-z0-9\-]+/[A-Za-z0-9\-]+)""")
BLOCK_RE = re.compile(r"Pardon Our Interruption|_Incapsula_Resource|Incapsula incident|Request unsuccessful|"
                      r"queue-it|Access Denied|captcha", re.I)
PC_RE = re.compile(r"pok[eé]mon[\s-]*center|pokemoncenter", re.I)
# Slugs/Namen einer TTB (EN: elite trainer box, DE: Top-Trainer-Box)
TTB_SLUG_RE = re.compile(r"elite[\s-]*trainer[\s-]*box|top[\s-]*trainer[\s-]*box|\betb\b|\bttb\b", re.I)
# Kein eigenes Produkt, nur Zubehör mit TTB im Namen
NOT_TTB_RE = re.compile(r"sleeve|h[üu]lle|binder|ordner|playmat|spielmatte|dice|w[üu]rfel|card[\s-]*case|"
                        r"deck[\s-]*box|pin\b|plush|pl[üu]sch", re.I)

SOURCE_KIND = "pokemoncenter"


class Blocked(Exception):
    pass


def _slug_title(path: str) -> str:
    slug = path.rstrip("/").rsplit("/", 1)[-1]
    words = slug.replace("-", " ").split()
    return " ".join(w if w.lower() in ("tcg", "ex", "gx", "v", "vmax", "vstar") and w.isupper() else w.capitalize()
                    for w in words).replace("Pokemon", "Pokémon").replace("Tcg", "TCG")


def parse_products(text: str, base: str) -> dict[str, dict]:
    """Findet Produkt-Links (HTML oder eingebettetes JSON) und – wenn möglich – Name, Preis, Bild.

    Name/Preis/Bild werden nur aus dem Abschnitt zwischen dem vorigen und dem nächsten Produkt-Link
    genommen, damit nichts vom Nachbarprodukt übernommen wird.
    """
    found: dict[str, dict] = {}
    t = html.unescape(text).replace("\\/", "/").replace("\\u002F", "/")
    matches = list(PRODUCT_PATH_RE.finditer(t))
    for n, m in enumerate(matches):
        path = m.group(1)
        url = urljoin(base, path)
        item = found.setdefault(url, {"url": url, "title": _slug_title(path), "price": None, "image": None})
        before = t[max(matches[n - 1].end() if n else 0, m.start() - 800): m.start()]
        after = t[m.end(): min(matches[n + 1].start() if n + 1 < len(matches) else len(t), m.end() + 800)]
        names = [re.sub(r"<[^>]+>", " ", x).strip() for x in re.findall(r"^[^<]*>(.{8,200}?)</a>", after, re.S)]
        names = [x for x in names if len(x) >= 8] \
            or re.findall(r'"(?:name|title|productName)"\s*:\s*"([^"]{8,200})"', before)
        if names and item["title"] == _slug_title(path):
            item["title"] = re.sub(r"\s+", " ", names[-1]).strip() or item["title"]
        if item["price"] is None:
            pr = re.search(r'"(?:price|formattedPrice|displayPrice|amount)"\s*:\s*"?([0-9][0-9.,]*\s*(?:€|EUR)?)',
                           after) or re.search(r"([0-9]{1,3}[,.][0-9]{2})\s*€", after)
            if pr:
                item["price"] = parse_price(pr.group(1))
        if item["image"] is None:
            img = re.search(r'(https://[^"\s]+\.(?:jpg|jpeg|png|webp))', after)
            if img:
                item["image"] = img.group(1)
    return found


def is_ttb(item: dict) -> bool:
    """Entscheidung über den URL-Slug (beim Pokémon Center beschreibend, z.B. …-elite-trainer-box)."""
    slug = item["url"].rstrip("/").rsplit("/", 1)[-1]
    return bool(TTB_SLUG_RE.search(slug)) and not NOT_TTB_RE.search(slug)


class PokemonCenterCog(commands.Cog):
    def __init__(self, bot, config: dict, send_to):
        self.bot, self.db, self.send_to = bot, bot.db, send_to
        self.cfg = config.get("pokemon_center", {})
        self.pages = self.cfg.get("pages", [])
        self.proxy = os.getenv("PC_PROXY") or None
        self.backoff_until: dict[str, float] = {}
        self.fails: dict[str, int] = {}
        for url in self.pages:
            self.db.conn.execute("INSERT OR IGNORE INTO sources (url, kind, name) VALUES (?,?,?)",
                                 (url, SOURCE_KIND, f"Pokémon Center {urlparse(url).path.split('/')[1]}"))
        self.db.conn.commit()
        self.check_loop.change_interval(seconds=max(30, int(self.cfg.get("interval_seconds", 60))))
        self.check_loop.start()
        self.deal_loop.start()

    def cog_unload(self):
        self.check_loop.cancel()
        self.deal_loop.cancel()

    # -------------------------------------------------------------- Abruf
    async def fetch_page(self, url: str) -> dict[str, dict]:
        headers = dict(BROWSER_HEADERS)
        headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.pokemoncenter.com/",
        })
        async with self.bot.http_session.get(url, headers=headers, timeout=TIMEOUT, proxy=self.proxy,
                                             allow_redirects=True) as r:
            text = await r.text(errors="replace")
            if r.status in (403, 429, 503) or (BLOCK_RE.search(text[:5000]) and "/product/" not in text):
                raise Blocked(f"Bot-Schutz aktiv (HTTP {r.status})")
            r.raise_for_status()
        items = parse_products(text, "https://www.pokemoncenter.com")
        if not items:
            raise Blocked("Seite geladen, aber keine Produkte gefunden (Seite wird per JavaScript geladen "
                          "oder Bot-Schutz)")
        return items

    def _mark(self, url, ok, items=0, error=None):
        if ok:
            self.db.conn.execute("UPDATE sources SET last_ok=?, last_error=NULL, items=?, seeded=1 WHERE url=?",
                                 (int(time.time()), items, url))
        else:
            self.db.conn.execute("UPDATE sources SET last_error=? WHERE url=?", (str(error)[:200], url))
        self.db.conn.commit()

    # -------------------------------------------------------------- Posten
    async def announce(self, item: dict, via: str):
        if not self.db.mark_seen("pc_ttb", item["url"]):
            return
        e = discord.Embed(title=item["title"][:256], url=item["url"], color=0xE3350D,
                          description="**Neue Top-Trainer-Box im Pokémon Center!** Schnell sein 🏃")
        e.set_author(name=f"🆕 Pokémon Center · {via}")
        if item.get("price") is not None:
            e.add_field(name="Preis", value=euro(item["price"]))
        e.add_field(name="Link", value=f"[Zum Shop]({item['url']})")
        if item.get("image"):
            e.set_thumbnail(url=item["image"])
        e.set_footer(text="Gefunden")
        e.timestamp = discord.utils.utcnow()
        await self.send_to("pc_ttb", embed=e, ping_role="pc_ttb")

    # -------------------------------------------------------------- Schleifen
    @tasks.loop(seconds=60)
    async def check_loop(self):
        await asyncio.sleep(random.uniform(0, 15))  # nicht immer zur gleichen Sekunde anfragen
        for url in self.pages:
            if time.time() < self.backoff_until.get(url, 0):
                continue
            src = self.db.conn.execute("SELECT * FROM sources WHERE url=?", (url,)).fetchone()
            try:
                items = await self.fetch_page(url)
            except Exception as ex:
                n = self.fails[url] = self.fails.get(url, 0) + 1
                wait = min(60 * 60, 60 * 2 ** min(n, 6))  # 2, 4, 8 … bis 60 Minuten Pause
                self.backoff_until[url] = time.time() + wait
                self._mark(url, False, error=f"{ex} – nächster Versuch in {wait // 60} Min.")
                log.warning("Pokémon Center %s: %s (Pause %ss)", url, ex, wait)
                continue
            self.fails[url] = 0
            ttbs = [i for i in items.values() if is_ttb(i)]
            for it in ttbs:
                if not src["seeded"]:
                    self.db.mark_seen("pc_ttb", it["url"])  # erster Durchlauf: Bestand nur merken
                else:
                    await self.announce(it, "direkt")
            self._mark(url, True, len(items))
            await asyncio.sleep(random.uniform(2, 5))

    @tasks.loop(minutes=2)
    async def deal_loop(self):
        """Ersatzweg: mydealz meldet Pokémon-Center-Deals oft schon Minuten nach Erscheinen."""
        for feed in self.cfg.get("deal_feeds", []):
            try:
                items = await fetch_rss(self.bot.http_session, feed)
            except Exception as ex:
                log.warning("mydealz für Pokémon Center: %s", ex)
                continue
            seeded = self.db.get_meta("pc_deals_seeded")
            for it in items:
                t = f"{it['title']} {it.get('merchant') or ''}"
                if not (PC_RE.search(t) and TTB_RE.search(it["title"])) or NOT_TTB_RE.search(it["title"]):
                    continue
                item = {"url": it["url"], "title": it["title"],
                        "price": it.get("price") or parse_price(it["title"]), "image": None}
                if not seeded:
                    self.db.mark_seen("pc_ttb", item["url"])
                else:
                    await self.announce(item, "via mydealz")
            if not seeded:
                self.db.set_meta("pc_deals_seeded", "1")

    @check_loop.before_loop
    @deal_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    # -------------------------------------------------------------- Befehl
    @app_commands.command(name="pokemoncenter", description="Pokémon Center jetzt prüfen und Status zeigen.")
    async def pc_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        lines = []
        for url in self.pages:
            try:
                items = await self.fetch_page(url)
                ttbs = [i for i in items.values() if is_ttb(i)]
                lines.append(f"🟢 {url}\n   {len(items)} Produkte, davon {len(ttbs)} TTBs"
                             + "".join(f"\n   • [{t['title'][:80]}]({t['url']})" for t in ttbs[:5]))
            except Exception as ex:
                lines.append(f"🔴 {url}\n   {ex}")
        lines.append("ℹ️ mydealz-Ersatzweg läuft immer mit (alle 2 Min.).")
        if self.proxy:
            lines.append("🔀 Abruf über Proxy (PC_PROXY).")
        await interaction.followup.send("\n".join(lines)[:1990], ephemeral=True, suppress_embeds=True)

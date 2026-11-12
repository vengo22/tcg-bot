"""Pokémon-TCG Discord-Bot: Set-News, Preisalarme und Preis-Graphen (Cardmarket)."""
import asyncio
import json
import logging
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from . import charts, sources
from .db import DB
from .info import InfoCog, news_embed
from .monitor import MonitorCog
from .pokemoncenter import PokemonCenterCog

log = logging.getLogger("tcgbot")
ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def euro(v) -> str:
    if v is None:
        return "–"
    return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


class RoleButtons(discord.ui.View):
    """Buttons zum Selbst-Zuweisen der Ping-Rollen (bleiben nach Neustart aktiv)."""

    def __init__(self):
        super().__init__(timeout=None)
        for kind, name in CONFIG["roles"].items():
            btn = discord.ui.Button(label=f"🔔 {name}", style=discord.ButtonStyle.secondary,
                                    custom_id=f"tcgbot:role:{kind}")
            btn.callback = self._make_cb(name)
            self.add_item(btn)

    @staticmethod
    def _make_cb(role_name):
        async def cb(interaction: discord.Interaction):
            role = discord.utils.get(interaction.guild.roles, name=role_name)
            if role is None:
                return await interaction.response.send_message("Rolle fehlt – bitte `/setup` erneut ausführen.", ephemeral=True)
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role)
                msg = f"Rolle **{role_name}** entfernt."
            else:
                await interaction.user.add_roles(role)
                msg = f"Du bekommst jetzt Pings für **{role_name}**."
            await interaction.response.send_message(msg, ephemeral=True)
        return cb


class TCGBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.db = DB(os.getenv("TCGBOT_DB", ROOT / "tcgbot.db"))
        self.http_session: aiohttp.ClientSession | None = None
        self.api_key = os.getenv("POKEMONTCG_API_KEY") or None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()
        self.add_view(RoleButtons())
        cog = TCGCog(self)
        await self.add_cog(cog)
        monitor = MonitorCog(self, CONFIG, cog.send_to)
        await self.add_cog(monitor)
        await self.add_cog(PokemonCenterCog(self, CONFIG, cog.send_to))
        await self.add_cog(InfoCog(self, CONFIG, cog.send_to, monitor.latest_news))
        guild_id = os.getenv("GUILD_ID")
        if guild_id:
            g = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=g)
            await self.tree.sync(guild=g)
        else:
            await self.tree.sync()

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()


class TCGCog(commands.Cog):
    def __init__(self, bot: TCGBot):
        self.bot = bot
        self.db = bot.db
        iv = CONFIG["intervals_minutes"]
        self.sets_loop.change_interval(minutes=iv["sets"])
        self.news_loop.change_interval(minutes=iv["news"])
        self.price_loop.change_interval(minutes=iv["prices"])
        self.sets_loop.start()
        self.news_loop.start()
        self.price_loop.start()

    def cog_unload(self):
        self.sets_loop.cancel()
        self.news_loop.cancel()
        self.price_loop.cancel()

    # ---------------------------------------------------------------- Hilfen
    async def send_to(self, kind: str, guild_id: int | None = None, ping_role: str | None = None,
                      content: str | None = None, **kwargs):
        for row in self.db.channels(kind):
            if guild_id is not None and row["guild_id"] != guild_id:
                continue
            channel = self.bot.get_channel(row["channel_id"])
            if channel is None:
                continue
            text = content
            if ping_role:
                role = discord.utils.get(channel.guild.roles, name=CONFIG["roles"].get(ping_role))
                text = role.mention if role else text
            try:
                await channel.send(content=text, **kwargs)
            except discord.HTTPException:
                log.exception("Senden in Kanal %s fehlgeschlagen", row["channel_id"])

    def resolve_product(self, value: str):
        if value.isdigit():
            p = self.db.product(int(value))
            if p:
                return p
        hits = self.db.search_products(value, limit=1)
        return hits[0] if hits else None

    async def product_autocomplete(self, interaction: discord.Interaction, current: str):
        if len(current) < 2:
            return []
        return [
            app_commands.Choice(name=(("📦 " if p["sealed"] else "🃏 ") + p["name"])[:100], value=str(p["id"]))
            for p in self.db.search_products(current, limit=25)
        ]

    # ---------------------------------------------------------------- /setup
    @app_commands.command(name="setup", description="Legt Kategorie, Kanäle und Ping-Rollen automatisch an.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def setup_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        for name in CONFIG["roles"].values():
            if not discord.utils.get(guild.roles, name=name):
                await guild.create_role(name=name, mentionable=True, reason="TCG-Bot Setup")

        categories = {}

        async def get_category(name):
            if name not in categories:
                cat = discord.utils.get(guild.categories, name=name)
                categories[name] = cat or await guild.create_category(name, reason="TCG-Bot Setup")
            return categories[name]

        # Nur der Bot schreibt in die Info-Kanäle, alle anderen lesen.
        read_only = {
            guild.default_role: discord.PermissionOverwrite(send_messages=False),
            guild.me: discord.PermissionOverwrite(send_messages=True, embed_links=True, attach_files=True),
        }
        topics = {
            "sets": "Neue Pokémon-TCG-Sets, sobald sie bekannt sind.",
            "news": "Alle TCG-News, Release-Kalender (angepinnt) mit Erscheinungsdaten und Preisen.",
            "alerts": "Preisalarme aus deiner Watchlist (/watch).",
            "charts": "Hier dürfen alle Befehle nutzen: /preis, /graph, /ttb, /quellen …",
            "preorder": "Neue Vorbestellungen aus Shops, mydealz und News.",
            "articles": "Neue Pokémon-TCG-Produkte in Shops und Deals von mydealz.",
            "restock": "Ausverkaufte Produkte, die wieder lieferbar sind – schnell sein!",
            "ttb_new": "Neue deutsche, versiegelte Top-Trainer-Boxen bei seriösen Händlern.",
            "pc_ttb": "Neue Top-Trainer-Boxen im Pokémon Center – sofort, mit Ping (Rolle Pokémon-Center).",
            "ttb_cheap": "Neuer Tiefstpreis für eine deutsche Top-Trainer-Box (seriöse Händler).",
        }
        created = []
        for kind, name in CONFIG["channels"].items():
            category = await get_category(CONFIG["channel_categories"].get(kind, CONFIG["category_name"]))
            ch = discord.utils.get(category.text_channels, name=name)
            if ch is None:
                overwrites = {} if kind == "charts" else read_only
                ch = await guild.create_text_channel(name, category=category, topic=topics.get(kind),
                                                     overwrites=overwrites, reason="TCG-Bot Setup")
                created.append(ch.mention)
            self.db.set_channel(guild.id, kind, ch.id)

        info = self.bot.get_cog("InfoCog")
        if info:  # Kalender anlegen/aktualisieren; neu angelegtes #tcg-info bekommt gleich die neueste Meldung
            await info.post_overview(guild.id)
            news_ch = guild.get_channel(self.db_channel(guild.id, "news"))
            if news_ch and news_ch.mention in created:
                await info.post_test_news(guild.id)

        charts_ch = guild.get_channel(self.db_channel(guild.id, "charts"))
        if charts_ch and created:
            await charts_ch.send(
                "**Willkommen beim TCG-Bot!** Wähle, wofür du gepingt werden willst:",
                view=RoleButtons(),
            )
        await interaction.followup.send(
            "Fertig! " + (f"Neu angelegt: {', '.join(created)}" if created else "Alles war schon da."),
            ephemeral=True,
        )

    def db_channel(self, guild_id, kind):
        for row in self.db.channels(kind):
            if row["guild_id"] == guild_id:
                return row["channel_id"]
        return None

    # ---------------------------------------------------------------- /preis
    @app_commands.command(name="preis", description="Aktuelle Cardmarket-Preise für ein Produkt.")
    @app_commands.describe(produkt="z.B. 'Prismatic Evolutions Elite Trainer Box'")
    @app_commands.autocomplete(produkt=product_autocomplete)
    async def preis(self, interaction: discord.Interaction, produkt: str):
        p = self.resolve_product(produkt)
        if p is None:
            return await interaction.response.send_message("Nichts gefunden. Tipp: englischen Namen verwenden.", ephemeral=True)
        await interaction.response.defer()
        price = self.db.latest_price(p["id"])
        if price is None:
            await self.snapshot_now([p["id"]])
            price = self.db.latest_price(p["id"])
        e = discord.Embed(title=p["name"], url=sources.CM_PRODUCT_URL.format(id=p["id"]), color=0xFFCB05)
        e.set_footer(text=f"Cardmarket · {p['category'] or ''}")
        if price is None:
            e.description = "Noch keine Preisdaten vorhanden."
        else:
            e.add_field(name="Günstigstes Angebot", value=euro(price["low"]))
            e.add_field(name="Trendpreis", value=euro(price["trend"]))
            e.add_field(name="​", value="​")
            e.add_field(name="Ø verkauft (1 Tag)", value=euro(price["avg1"]))
            e.add_field(name="Ø verkauft (7 Tage)", value=euro(price["avg7"]))
            e.add_field(name="Ø verkauft (30 Tage)", value=euro(price["avg30"]))
            low30 = self.db.lowest_since(p["id"], 30)
            if low30 is not None:
                e.description = f"Tiefster Preis der letzten 30 Tage: **{euro(low30)}**"
        await interaction.followup.send(embed=e)

    # ---------------------------------------------------------------- /graph
    @app_commands.command(name="graph", description="Preisverlauf (verkauft, Trend, günstigstes Angebot) als Graph.")
    @app_commands.describe(produkt="Produktname", tage="Zeitraum in Tagen (Standard 90)")
    @app_commands.autocomplete(produkt=product_autocomplete)
    async def graph(self, interaction: discord.Interaction, produkt: str, tage: app_commands.Range[int, 7, 730] = 90):
        p = self.resolve_product(produkt)
        if p is None:
            return await interaction.response.send_message("Nichts gefunden.", ephemeral=True)
        hist = self.db.history(p["id"], tage)
        if len(hist) < 2:
            await interaction.response.send_message(
                f"Für **{p['name']}** sammle ich erst seit Kurzem Daten. Der Graph füllt sich jeden Tag "
                "(Cardmarket liefert keine alten Verläufe). Nutze solange `/preis`.", ephemeral=True)
            if not hist:
                await self.snapshot_now([p["id"]])
            return
        await interaction.response.defer()
        buf = await asyncio.to_thread(charts.price_chart, f"{p['name']} – letzte {tage} Tage", hist)
        await interaction.followup.send(file=discord.File(buf, filename="preis.png"))

    # ---------------------------------------------------------------- Watchlist
    @app_commands.command(name="watch", description="Produkt beobachten und bei günstigem Preis gepingt werden.")
    @app_commands.describe(produkt="Produktname", zielpreis="Alarm, sobald das günstigste Angebot ≤ diesem Preis (€) ist")
    @app_commands.autocomplete(produkt=product_autocomplete)
    @app_commands.guild_only()
    async def watch(self, interaction: discord.Interaction, produkt: str, zielpreis: float | None = None):
        p = self.resolve_product(produkt)
        if p is None:
            return await interaction.response.send_message("Nichts gefunden.", ephemeral=True)
        self.db.add_watch(interaction.guild_id, interaction.user.id, p["id"], zielpreis)
        rule = (f"sobald es **≤ {euro(zielpreis)}** gibt" if zielpreis is not None else
                f"wenn der Preis auf ein 30-Tage-Tief fällt und ≥ {CONFIG['price_drop_percent']} % unter dem Trend liegt")
        await interaction.response.send_message(f"👀 Ich beobachte **{p['name']}** und melde mich {rule}.", ephemeral=True)
        await self.snapshot_now([p["id"]])

    @app_commands.command(name="unwatch", description="Produkt nicht mehr beobachten.")
    @app_commands.autocomplete(produkt=product_autocomplete)
    @app_commands.guild_only()
    async def unwatch(self, interaction: discord.Interaction, produkt: str):
        p = self.resolve_product(produkt)
        ok = p is not None and self.db.remove_watch(interaction.guild_id, interaction.user.id, p["id"])
        await interaction.response.send_message("Entfernt." if ok else "Stand nicht auf deiner Watchlist.", ephemeral=True)

    @app_commands.command(name="watchlist", description="Deine beobachteten Produkte.")
    @app_commands.guild_only()
    async def watchlist(self, interaction: discord.Interaction):
        rows = self.db.watches(interaction.guild_id, interaction.user.id)
        if not rows:
            return await interaction.response.send_message("Leer. Füge etwas mit `/watch` hinzu.", ephemeral=True)
        lines = []
        for w in rows:
            price = self.db.latest_price(w["product_id"])
            lines.append(f"• **{w['name']}** – jetzt {euro(price['low'] if price else None)}"
                         + (f" · Ziel {euro(w['target'])}" if w["target"] is not None else ""))
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

    # ---------------------------------------------------------------- /sets
    @app_commands.command(name="sets", description="Die neuesten Pokémon-TCG-Sets.")
    async def sets_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            sets = await sources.fetch_latest_sets(self.bot.http_session, self.bot.api_key, count=8)
        except Exception:
            log.exception("Sets laden fehlgeschlagen")
            return await interaction.followup.send("pokemontcg.io ist gerade nicht erreichbar.")
        lines = [f"• **{s['name']}** ({s.get('series', '')}) – {self.fmt_date(s.get('releaseDate'))}" for s in sets]
        await interaction.followup.send("\n".join(lines) or "Keine Daten.")

    @staticmethod
    def fmt_date(d):
        try:
            return datetime.strptime(d, "%Y/%m/%d").strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            return d or "?"

    # ================================================================ Hintergrund-Jobs
    @tasks.loop(minutes=360)
    async def sets_loop(self):
        try:
            sets = await sources.fetch_latest_sets(self.bot.http_session, self.bot.api_key)
        except Exception:
            log.exception("Set-Check fehlgeschlagen")
            return
        first_run = not self.db.has_seen_any("set")
        for s in reversed(sets):
            if not self.db.mark_seen("set", s["id"]) or first_run:
                continue
            e = discord.Embed(title=f"🆕 Neues Set: {s['name']}", color=0xFFCB05,
                              description=f"Serie: **{s.get('series', '?')}**\n"
                                          f"Release: **{self.fmt_date(s.get('releaseDate'))}**\n"
                                          f"Karten: **{s.get('printedTotal') or s.get('total') or '?'}**")
            if logo := (s.get("images") or {}).get("logo"):
                e.set_image(url=logo)
            await self.send_to("sets", embed=e, ping_role="sets")

    @tasks.loop(minutes=30)
    async def news_loop(self):
        keywords = [k.lower() for k in CONFIG["news_keywords"]]
        for url in CONFIG["news_feeds"]:
            try:
                items = await sources.fetch_feed(self.bot.http_session, url)
            except Exception:
                log.warning("Feed %s nicht erreichbar", url)
                continue
            first_run = not self.db.has_seen_any(f"news:{url}")
            for key, title, link, summary, published in reversed(items):
                if not self.db.mark_seen(f"news:{url}", key) or first_run:
                    continue
                if not any(k in title.lower() for k in keywords):
                    continue
                e = news_embed(title, link, url.split("/")[2].removeprefix("www."), published, strip_html(summary))
                await self.send_to("news", embed=e)

    @tasks.loop(minutes=180)
    async def price_loop(self):
        session = self.bot.http_session
        today = date.today().isoformat()
        if self.db.get_meta("catalog_day") != today:
            rows = await sources.fetch_cardmarket_catalog(session)
            if rows:
                await asyncio.to_thread(self.db.upsert_products, rows)
                self.db.set_meta("catalog_day", today)
                log.info("Katalog aktualisiert: %d Produkte", len(rows))
        try:
            created, prices = await sources.fetch_cardmarket_prices(session)
        except Exception:
            log.exception("Cardmarket-Preise nicht ladbar")
            return
        if created and created == self.db.get_meta("price_created"):
            return  # Datei seit letztem Lauf nicht neu
        self._cache = prices
        keep = self.db.sealed_product_ids() | self.db.watched_product_ids()
        rows = [(pid, *v) for pid, v in prices.items() if pid in keep]
        await asyncio.to_thread(self.db.add_prices, today, rows)
        self.db.set_meta("price_created", created)
        await self.check_alerts()

    async def snapshot_now(self, product_ids):
        """Sofort einen Preis für einzelne (z.B. Einzelkarten) aus dem letzten Download speichern."""
        cache = getattr(self, "_cache", None)
        if cache is None:
            try:
                _, cache = await sources.fetch_cardmarket_prices(self.bot.http_session)
                self._cache = cache
            except Exception:
                return
        rows = [(pid, *cache[pid]) for pid in product_ids if pid in cache]
        if rows:
            self.db.add_prices(date.today().isoformat(), rows)

    async def check_alerts(self):
        pct = CONFIG["price_drop_percent"] / 100
        for w in self.db.watches():
            price = self.db.latest_price(w["product_id"])
            if price is None or price["low"] is None:
                continue
            low, last = price["low"], w["last_alert_price"]
            if w["target"] is not None:
                hit = low <= w["target"]
                reason = f"Zielpreis {euro(w['target'])} erreicht"
            else:
                low30 = self.db.lowest_since(w["product_id"], 30)
                hit = price["trend"] is not None and low <= price["trend"] * (1 - pct) and low <= (low30 or low)
                reason = f"30-Tage-Tief, {round((1 - low / price['trend']) * 100) if price['trend'] else '?'} % unter Trend"
            if not hit:
                if last is not None:
                    self.db.set_last_alert(w["id"], None)  # wieder scharf schalten
                continue
            if last is not None and low >= last * 0.98:
                continue  # schon gemeldet, nicht spürbar günstiger geworden
            self.db.set_last_alert(w["id"], low)
            e = discord.Embed(title=f"💸 {w['name']}", url=sources.CM_PRODUCT_URL.format(id=w["product_id"]),
                              description=f"**{euro(low)}** günstigstes Angebot · {reason}\n"
                                          f"Trend {euro(price['trend'])} · Ø verkauft 7 Tage {euro(price['avg7'])}",
                              color=0x3AA776)
            await self.send_to("alerts", guild_id=w["guild_id"], content=f"<@{w['user_id']}>", embed=e)

    @sets_loop.before_loop
    @news_loop.before_loop
    @price_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    token = os.getenv("DISCORD_TOKEN")
    if not token or token == "hier-dein-token":
        raise SystemExit("DISCORD_TOKEN fehlt – trage ihn in die Datei .env ein.")
    TCGBot().run(token, log_handler=None)


if __name__ == "__main__":
    main()

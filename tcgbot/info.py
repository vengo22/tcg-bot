"""#tcg-info: Release-Kalender mit Preisen und einheitlich formatierte News."""
import logging
import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger("tcgbot.info")

NEWS_TYPES = (
    # (Regex, Label, Farbe)
    (re.compile(r"vorbestell|pre[\s-]?order|vorverkauf", re.I), "🗓️ Vorbestellung", 0x9B59B6),
    (re.compile(r"angek[üu]ndigt|announce|enth[üu]llt|reveal|leak|erste karten|first look", re.I),
     "📣 Ankündigung", 0xE67E22),
    (re.compile(r"erscheint|release|ver[öo]ffentlich|ab heute|jetzt erh[äa]ltlich|out now|launch", re.I),
     "🚀 Release", 0x2ECC71),
    (re.compile(r"preis|price|teuer|g[üu]nstig|wert|value|sold|verkauft", re.I), "💶 Preise & Markt", 0xF1C40F),
    (re.compile(r"turnier|tournament|weltmeister|championship|regional", re.I), "🏆 Turniere", 0x3498DB),
)


def news_type(title: str):
    for rx, label, color in NEWS_TYPES:
        if rx.search(title):
            return label, color
    return "📰 News", 0x3B4CCA


def fmt_date(value) -> str | None:
    """RSS-Datum oder ISO-Datum -> 31.10.2026 (14:05)."""
    if not value:
        return None
    try:
        d = parsedate_to_datetime(value) if not re.match(r"\d{4}-\d{2}-\d{2}", str(value)) \
            else datetime.fromisoformat(str(value))
        return d.strftime("%d.%m.%Y %H:%M") if (d.hour or d.minute) else d.strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return None


def news_embed(title, url, source, published=None, summary=None) -> discord.Embed:
    """Einheitliches, gut lesbares News-Embed für #tcg-info."""
    label, color = news_type(title)
    # Google News hängt " - Quelle" an den Titel
    m = re.match(r"(.+?)\s+-\s+([^-]{2,60})$", title)
    if m and source == "Google News":
        title, source = m.group(1), m.group(2)
    e = discord.Embed(title=f"{label}  |  {title}"[:256], url=url, color=color)
    if summary:
        e.description = summary[:350] + ("…" if len(summary) > 350 else "")
    e.add_field(name="Quelle", value=source[:100])
    if when := fmt_date(published):
        e.add_field(name="Datum", value=when)
    return e


def countdown(d: date) -> str:
    days = (d - date.today()).days
    if days > 1:
        return f"in {days} Tagen"
    if days == 1:
        return "morgen"
    if days == 0:
        return "**heute!**"
    return "erschienen"


def euro(v) -> str:
    return "–" if v is None else f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


class InfoCog(commands.Cog):
    def __init__(self, bot, config: dict, send_to, latest_news):
        self.bot, self.db, self.cfg = bot, bot.db, config
        self.send_to, self.latest_news = send_to, latest_news
        self.refresh_loop.start()

    def cog_unload(self):
        self.refresh_loop.cancel()

    # -------------------------------------------------------------- Preise aus dem Cardmarket-Katalog
    def _cm_price(self, name_en: str, product: str):
        if not name_en:
            return None
        hits = [p for p in self.db.search_products(f"{name_en} {product}", limit=10) if p["sealed"]]
        for p in hits:
            if re.search(r"bundle|case|display case|sleeved|pokemon center|half", p["name"], re.I) \
                    and "bundle" not in product:
                continue
            price = self.db.latest_price(p["id"])
            if price and (price["trend"] or price["low"]):
                return p, price
        return None

    def calendar_embeds(self):
        sets = sorted(self.cfg.get("release_calendar", []), key=lambda s: s["release"])
        today = date.today()
        upcoming = [s for s in sets if date.fromisoformat(s["release"]) >= today]
        recent = [s for s in sets if date.fromisoformat(s["release"]) < today][-5:]
        embeds = []
        for title, group, color in (("📅 Kommende Sets & Produkte", upcoming, 0xFFCB05),
                                    ("📦 Zuletzt erschienen", list(reversed(recent)), 0x95A5A6)):
            if not group:
                continue
            e = discord.Embed(title=title, color=color)
            for s in group[:12]:
                d = date.fromisoformat(s["release"])
                lines = [f"🗓️ **{d.strftime('%d.%m.%Y')}** ({countdown(d)})"]
                if s.get("code") or s.get("name_en"):
                    lines.append(f"🏷️ {' · '.join(x for x in (s.get('code'), s.get('name_en')) if x)}")
                if s.get("products"):
                    lines.append(f"🧾 {s['products']}")
                if s.get("uvp"):
                    lines.append(f"💶 UVP: {s['uvp']}")
                for label, product in (("TTB", "elite trainer box"), ("Display", "booster box")):
                    hit = self._cm_price(s.get("name_en"), product)
                    if hit:
                        p, price = hit
                        lines.append(f"📈 {label} Cardmarket: ab **{euro(price['low'])}** · Trend {euro(price['trend'])}")
                e.add_field(name=f"{s.get('emoji', '✨')} {s['name_de']}", value="\n".join(lines)[:1024], inline=False)
            embeds.append(e)
        if embeds:
            embeds[-1].set_footer(text="Preise: Cardmarket (günstigstes Angebot, alle Sprachen) · "
                                       f"Stand {datetime.now().strftime('%d.%m.%Y %H:%M')}")
        return embeds

    async def post_overview(self, guild_id: int):
        """Kalender-Nachricht in #tcg-info anlegen bzw. aktualisieren (immer dieselbe Nachricht, angepinnt)."""
        ch_id = next((r["channel_id"] for r in self.db.channels("news") if r["guild_id"] == guild_id), None)
        channel = self.bot.get_channel(ch_id) if ch_id else None
        if channel is None:
            return False
        embeds = self.calendar_embeds()
        if not embeds:
            return False
        msg_id = self.db.get_meta(f"info_msg:{guild_id}")
        if msg_id:
            try:
                msg = await channel.fetch_message(int(msg_id))
                await msg.edit(embeds=embeds)
                return True
            except discord.HTTPException:
                pass
        msg = await channel.send(embeds=embeds)
        self.db.set_meta(f"info_msg:{guild_id}", msg.id)
        try:
            await msg.pin()
        except discord.HTTPException:
            pass  # ohne "Nachrichten verwalten" kein Anpinnen, nicht schlimm
        return True

    async def post_test_news(self, guild_id: int):
        item = await self.latest_news()
        if item:
            await self.send_to("news", guild_id=guild_id, embed=news_embed(*item))

    @tasks.loop(hours=6)
    async def refresh_loop(self):
        for row in self.db.channels("news"):
            try:
                await self.post_overview(row["guild_id"])
            except Exception:
                log.exception("Info-Übersicht aktualisieren fehlgeschlagen")

    @refresh_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="info", description="Release-Kalender und neueste Meldung in #tcg-info posten.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def info_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok = await self.post_overview(interaction.guild_id)
        await self.post_test_news(interaction.guild_id)
        await interaction.followup.send("✅ In #tcg-info gepostet." if ok else
                                        "#tcg-info fehlt noch – bitte erst `/setup` ausführen.", ephemeral=True)

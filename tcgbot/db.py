"""SQLite-Speicher: Produktkatalog, Preis-Verlauf, Watchlist, gemeldete Sets/News."""
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    expansion_id INTEGER,
    sealed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);

CREATE TABLE IF NOT EXISTS prices (
    product_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    low REAL, trend REAL, avg1 REAL, avg7 REAL, avg30 REAL,
    PRIMARY KEY (product_id, day)
);

CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    target REAL,
    last_alert_price REAL,
    created INTEGER NOT NULL,
    UNIQUE (guild_id, user_id, product_id)
);

CREATE TABLE IF NOT EXISTS guild_channels (
    guild_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, kind)
);

CREATE TABLE IF NOT EXISTS seen (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


# Gängige Abkürzungen -> Cardmarket-Namen (englisch)
ALIASES = {
    "etb": "elite trainer box",
    "upc": "ultra premium collection",
    "display": "booster box",
    "bb": "booster box",
    "spc": "super premium collection",
}


class DB:
    def __init__(self, path: str | Path = "tcgbot.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- Katalog & Preise ---------------------------------------------------
    def upsert_products(self, rows):
        self.conn.executemany(
            "INSERT INTO products (id, name, category, expansion_id, sealed) VALUES (?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, category=excluded.category, "
            "expansion_id=excluded.expansion_id, sealed=excluded.sealed",
            rows,
        )
        self.conn.commit()

    def search_products(self, query: str, limit: int = 25, sealed_first: bool = True):
        words = []
        for w in query.lower().split():
            words.extend(ALIASES.get(w, w).split())
        if not words:
            return []
        where = " AND ".join("lower(name) LIKE ?" for _ in words)
        order = "sealed DESC, " if sealed_first else ""
        return self.conn.execute(
            f"SELECT * FROM products WHERE {where} ORDER BY {order}length(name) LIMIT ?",
            [f"%{w}%" for w in words] + [limit],
        ).fetchall()

    def product(self, product_id: int):
        return self.conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()

    def add_prices(self, day: str, rows):
        self.conn.executemany(
            "INSERT OR REPLACE INTO prices (product_id, day, low, trend, avg1, avg7, avg30) "
            "VALUES (?,?,?,?,?,?,?)",
            [(pid, day, *vals) for pid, *vals in rows],
        )
        self.conn.commit()

    def history(self, product_id: int, days: int = 90):
        return self.conn.execute(
            "SELECT * FROM prices WHERE product_id=? AND day >= date('now', ?) ORDER BY day",
            (product_id, f"-{days} days"),
        ).fetchall()

    def latest_price(self, product_id: int):
        return self.conn.execute(
            "SELECT * FROM prices WHERE product_id=? ORDER BY day DESC LIMIT 1", (product_id,)
        ).fetchone()

    def lowest_since(self, product_id: int, days: int):
        row = self.conn.execute(
            "SELECT min(low) AS m FROM prices WHERE product_id=? AND day >= date('now', ?)",
            (product_id, f"-{days} days"),
        ).fetchone()
        return row["m"] if row else None

    # --- Watchlist ---------------------------------------------------------
    def add_watch(self, guild_id, user_id, product_id, target):
        self.conn.execute(
            "INSERT INTO watches (guild_id, user_id, product_id, target, created) VALUES (?,?,?,?,?) "
            "ON CONFLICT(guild_id, user_id, product_id) DO UPDATE SET target=excluded.target, "
            "last_alert_price=NULL",
            (guild_id, user_id, product_id, target, int(time.time())),
        )
        self.conn.commit()

    def remove_watch(self, guild_id, user_id, product_id) -> bool:
        cur = self.conn.execute(
            "DELETE FROM watches WHERE guild_id=? AND user_id=? AND product_id=?",
            (guild_id, user_id, product_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def watches(self, guild_id=None, user_id=None):
        sql = "SELECT w.*, p.name FROM watches w JOIN products p ON p.id=w.product_id WHERE 1=1"
        args = []
        if guild_id is not None:
            sql += " AND w.guild_id=?"
            args.append(guild_id)
        if user_id is not None:
            sql += " AND w.user_id=?"
            args.append(user_id)
        return self.conn.execute(sql, args).fetchall()

    def sealed_product_ids(self) -> set[int]:
        return {r[0] for r in self.conn.execute("SELECT id FROM products WHERE sealed=1")}

    def watched_product_ids(self) -> set[int]:
        return {r[0] for r in self.conn.execute("SELECT DISTINCT product_id FROM watches")}

    def set_last_alert(self, watch_id, price):
        self.conn.execute("UPDATE watches SET last_alert_price=? WHERE id=?", (price, watch_id))
        self.conn.commit()

    # --- Kanäle, gesehene Einträge, Meta -------------------------------------
    def set_channel(self, guild_id, kind, channel_id):
        self.conn.execute(
            "INSERT OR REPLACE INTO guild_channels VALUES (?,?,?)", (guild_id, kind, channel_id)
        )
        self.conn.commit()

    def channels(self, kind):
        return self.conn.execute(
            "SELECT guild_id, channel_id FROM guild_channels WHERE kind=?", (kind,)
        ).fetchall()

    def mark_seen(self, kind, key) -> bool:
        """True, wenn der Eintrag neu war."""
        cur = self.conn.execute("INSERT OR IGNORE INTO seen VALUES (?,?)", (kind, key))
        self.conn.commit()
        return cur.rowcount > 0

    def has_seen_any(self, kind) -> bool:
        return self.conn.execute("SELECT 1 FROM seen WHERE kind=? LIMIT 1", (kind,)).fetchone() is not None

    def get_meta(self, key):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))
        self.conn.commit()

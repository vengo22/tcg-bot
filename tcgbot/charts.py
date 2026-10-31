"""Preis-Graph als PNG für Discord."""
import io
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

SERIES = (
    ("avg1", "Ø Verkaufspreis (Tag)", "#2a78d6"),
    ("trend", "Trendpreis", "#e3893a"),
    ("low", "Günstigstes Angebot", "#3aa776"),
)


def price_chart(title: str, history) -> io.BytesIO:
    days = [date.fromisoformat(r["day"]) for r in history]
    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=120)
    fig.patch.set_facecolor("#2b2d31")  # Discord-Dunkelgrau
    ax.set_facecolor("#2b2d31")
    for key, label, color in SERIES:
        pts = [(d, r[key]) for d, r in zip(days, history) if r[key] is not None]
        if pts:
            ax.plot([p[0] for p in pts], [p[1] for p in pts], label=label, color=color,
                    linewidth=2, marker="o" if len(pts) < 15 else None, markersize=3)
    ax.set_title(title, color="#f2f3f5", fontsize=12, loc="left")
    ax.set_ylabel("€", color="#b5bac1")
    ax.tick_params(colors="#b5bac1")
    ax.grid(color="#3f4147", linewidth=0.6)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m."))
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", "."))
    leg = ax.legend(frameon=False, loc="upper left", fontsize=9)
    for t in leg.get_texts():
        t.set_color("#dbdee1")
    fig.autofmt_xdate()
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf

"""
PNG rendering of the Errors & Resolution ring for the batch summary email.

Mail clients will not run the frontend's chart library, and the mail can be
sent without a browser open at all, so the image is drawn server-side with
matplotlib on the Agg (headless) backend and embedded in the message as an
inline attachment.

The rings mirror AnalyticsPage exactly, including the colours, so the emailed
snapshot and the on-screen chart are recognisably the same picture:

  outer ring  what kind of error   Failed / Lo Progress / Pending
  inner ring  how much got solved  Solved / Unsolved

Excluded issues are absent from both, exactly as in Analytics -- ops set them
aside, so they are neither outstanding nor finished. See
analytics_service.build_analytics.
"""
from io import BytesIO

import matplotlib

# Must be selected before pyplot is imported: there is no display attached to
# the server process, and the default interactive backend would fail on import.
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

# Same hex values as AnalyticsPage's STATUS_COLORS / RESOLUTION_COLORS.
STATUS_COLORS = {"failed": "#dc2626", "pending": "#d97706", "lo_progress": "#2563eb"}
RESOLUTION_COLORS = {"solved": "#16a34a", "unsolved": "#a8a29e"}

_STATUS_ORDER = [
    ("failed", "Failed"),
    ("lo_progress", "Lo Progress"),
    ("pending", "Pending"),
]


# One neutral bar colour rather than a palette: the bars are one series, so
# colouring them differently would imply a distinction that isn't there.
BAR_COLOR = "#2f5597"


def render_entity_volume_png(entities: list, title: str = "", top_n: int = 12) -> bytes:
    """
    Horizontal bar of error volume per aggregator/bank, biggest first.

    `entities` is [(name, count), ...] in any order. Returns b"" when there is
    nothing to plot, which the caller treats as "send without this chart"
    rather than as an error.

    Horizontal because partner names are long and would overlap as x-axis
    labels; sorted ascending so matplotlib's bottom-up y-axis puts the largest
    at the top.
    """
    rows = [(str(n), int(c)) for n, c in entities if c]
    if not rows:
        return b""

    rows.sort(key=lambda r: r[1], reverse=True)
    rows = rows[:top_n]
    rows.reverse()

    names = [r[0] for r in rows]
    values = [r[1] for r in rows]

    # Grow with the number of bars so they never bunch up.
    height = max(2.2, 0.34 * len(rows) + 1.0)
    fig, ax = plt.subplots(figsize=(6.6, height), dpi=160)
    fig.patch.set_facecolor("white")

    bars = ax.barh(names, values, color=BAR_COLOR, height=0.62)
    ax.bar_label(bars, padding=4, fontsize=8.5, color="#374151",
                 fmt=lambda v: f"{int(v):,}")

    ax.set_xlabel("Error transactions", fontsize=9, color="#6b7280")
    ax.tick_params(axis="y", labelsize=9, length=0)
    ax.tick_params(axis="x", labelsize=8, colors="#6b7280")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#e5e7eb")
    ax.xaxis.grid(True, color="#f3f4f6", linewidth=0.8)
    ax.set_axisbelow(True)
    # Headroom so the value labels are not clipped by the axes edge.
    ax.set_xlim(0, max(values) * 1.16)

    if title:
        ax.set_title(title, fontsize=11, color="#1f2937", pad=10, loc="left")

    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def render_error_resolution_png(status_breakdown: dict, resolution: dict, title: str = "") -> bytes:
    """
    Two concentric rings as a PNG.

    `status_breakdown` is {failed, pending, lo_progress} and `resolution` is
    {solved, unsolved} -- both over the same population, so the rings line up.
    Returns b"" when there is nothing to draw, which the caller treats as "send
    the mail without a chart" rather than as an error: a batch with no
    outstanding errors is a good outcome, not a failure to render.
    """
    outer_vals, outer_colors, outer_labels = [], [], []
    for key, label in _STATUS_ORDER:
        value = int(status_breakdown.get(key) or 0)
        if value > 0:
            outer_vals.append(value)
            outer_colors.append(STATUS_COLORS[key])
            outer_labels.append(f"{label} ({value:,})")

    inner_vals, inner_colors, inner_labels = [], [], []
    for key, label in (("solved", "Solved"), ("unsolved", "Unsolved")):
        value = int(resolution.get(key) or 0)
        if value > 0:
            inner_vals.append(value)
            inner_colors.append(RESOLUTION_COLORS[key])
            inner_labels.append(f"{label} ({value:,})")

    if not outer_vals and not inner_vals:
        return b""

    fig, ax = plt.subplots(figsize=(6.2, 4.6), dpi=160)
    fig.patch.set_facecolor("white")

    # startangle=90 + counterclock=False makes the first wedge start at 12
    # o'clock and run clockwise, which is how the on-screen chart reads.
    common = {"startangle": 90, "counterclock": False}

    if outer_vals:
        ax.pie(
            outer_vals, radius=1.0, colors=outer_colors,
            wedgeprops={"width": 0.30, "edgecolor": "white", "linewidth": 2},
            **common,
        )
    if inner_vals:
        ax.pie(
            inner_vals, radius=0.66, colors=inner_colors,
            wedgeprops={"width": 0.30, "edgecolor": "white", "linewidth": 2},
            **common,
        )

    ax.set(aspect="equal")

    handles = [
        plt.Rectangle((0, 0), 1, 1, facecolor=c) for c in outer_colors + inner_colors
    ]
    ax.legend(
        handles, outer_labels + inner_labels,
        loc="center left", bbox_to_anchor=(1.0, 0.5),
        frameon=False, fontsize=9,
    )

    if title:
        ax.set_title(title, fontsize=11, color="#1f2937", pad=12)

    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)  # figures are global state in pyplot; leaking them leaks memory
    return buf.getvalue()

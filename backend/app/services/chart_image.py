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

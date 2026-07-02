"""Static-site build: render the whole program to flat HTML files.

Because the store is built from a single API call and every page is a pure
function of it, the site can be generated ahead of time and served as static
files (e.g. GitHub Pages). This mirrors the routes in ``web.py`` but writes each
page to disk instead of serving it.

Output layout (extensionless links resolve to these via host "pretty URLs"):

    index.html                 /
    premieres.html             /premieres
    calendar.html              /calendar
    day/<iso>.html             /day/<iso>
    movie/<id>.html            /movie/<id>
    static/…                   assets (fonts)
    .nojekyll                  tell GitHub Pages to serve files as-is

"Now" is frozen at build time, so schedule periodic rebuilds to keep the
upcoming/premiere filtering fresh.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from vega import queries, templating
from vega.api import VegaSceneClient
from vega.store import Store
from vega.sync import sync


def build(out: Path, *, base: str = "", on_progress: Callable[[str], None] | None = None) -> int:
    """Fetch the program and render every page under ``out``. Returns page count.

    ``base`` is a URL path prefix for internal links (e.g. ``"/vega"`` when the
    site is served from a GitHub Pages project subpath).
    """

    def report(message: str) -> None:
        if on_progress is not None:
            on_progress(message)

    env = templating.environment(base=base.rstrip("/"))
    # Build into an in-memory store so we can reuse the query layer verbatim.
    store = Store(":memory:")
    with VegaSceneClient() as client:
        sync(store, client, on_progress=on_progress)
    conn = store.conn

    out.mkdir(parents=True, exist_ok=True)
    pages = 0

    def render(template: str, ctx: dict[str, object], path: str) -> None:
        nonlocal pages
        html = env.get_template(template).render(**ctx)
        dest = out / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding="utf-8")
        pages += 1

    today = date.today()
    render("catalog.html", {"films": queries.catalog(conn), "today": today, "active": "catalog"}, "index.html")
    render(
        "premieres.html",
        {
            "films": queries.premieres(conn),
            "today": today,
            "next_week": today + timedelta(days=7),
            "active": "premieres",
        },
        "premieres.html",
    )
    render(
        "calendar.html",
        {"weeks": queries.calendar_grid(conn), "weekdays": templating.NO_DAYS, "active": "calendar"},
        "calendar.html",
    )

    days = [d["date"] for d in queries.day_index(conn)]
    for day in days:
        prev_day = max((d for d in days if d < day), default=None)
        next_day = min((d for d in days if d > day), default=None)
        render(
            "day.html",
            {
                "day": day,
                "shows": queries.shows_on(conn, day),
                "prev_day": prev_day,
                "next_day": next_day,
                "active": "calendar",
            },
            f"day/{day.isoformat()}.html",
        )

    movie_ids = [r["main_version_id"] for r in conn.execute("SELECT main_version_id FROM movies")]
    for main_id in movie_ids:
        render("movie.html", {"movie": queries.movie_detail(conn, main_id), "active": None}, f"movie/{main_id}.html")

    # Assets + GitHub Pages marker.
    shutil.copytree(templating.STATIC_DIR, out / "static", dirs_exist_ok=True)
    (out / ".nojekyll").write_text("")

    store.close()
    report(f"wrote {pages} pages to {out}/")
    return pages

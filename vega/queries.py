"""Read queries backing the web views.

Each function takes an open SQLite connection and returns plain dicts, already
enriched for display: JSON columns decoded, poster refs turned into CDN URLs,
and ``show_start`` parsed to a ``datetime``. Templates stay dumb.

"Upcoming" everywhere means ``show_start >= now`` (local time), so screenings
earlier today have already dropped off.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from vega.images import sanity_image_url

_JSON_COLUMNS = ("genres", "reviews", "trailers", "nationality", "original_language")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _decode_movie(row: sqlite3.Row) -> dict[str, Any]:
    movie = dict(row)
    for col in _JSON_COLUMNS:
        if col in movie:
            movie[col] = json.loads(movie[col]) if movie[col] else []
    ref = movie.get("poster_ref")
    # The cinema's poster is a 16:9 landscape promo image.
    movie["poster_url"] = sanity_image_url(ref, width=512, height=288)
    movie["poster_url_large"] = sanity_image_url(ref, width=960)
    return movie


def _classify_tags(raw: str | None) -> tuple[list[str], list[str]]:
    """Split stored version tags into (notable formats, subtitle languages).

    Notable formats are the annotations worth highlighting — ``35 mm``,
    ``Dolby Atmos`` (API type ``versionother`` / ``format``). Languages are the
    subtitle/version info (type ``version``), minus the ubiquitous ``2D``.
    """
    tags = json.loads(raw) if raw else []
    formats = [t["tag"] for t in tags if t.get("type") != "version"]
    languages = [t["tag"] for t in tags if t.get("type") == "version" and t["tag"] != "2D"]
    return formats, languages


def _decode_show(row: sqlite3.Row) -> dict[str, Any]:
    show = dict(row)
    show["formats"], show["languages"] = _classify_tags(show.get("version_tags"))
    show["start"] = datetime.fromisoformat(show["show_start"])
    return show


def _next_shows_by_movie(
    conn: sqlite3.Connection, now: str, limit: int = 3
) -> dict[str, list[dict[str, Any]]]:
    """The next ``limit`` upcoming screenings per film: ``{main_id: [{start, screen}]}``."""
    rows = conn.execute(
        """
        SELECT mid, show_start, screen_name, version_tags, show_type FROM (
            SELECT movie_main_version_id AS mid, show_start, screen_name,
                   version_tags, show_type,
                   ROW_NUMBER() OVER (
                       PARTITION BY movie_main_version_id ORDER BY show_start
                   ) AS rn
            FROM shows WHERE show_start >= :now
        ) WHERE rn <= :limit
        ORDER BY show_start
        """,
        {"now": now, "limit": limit},
    ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        formats, _ = _classify_tags(r["version_tags"])
        out.setdefault(r["mid"], []).append(
            {
                "start": datetime.fromisoformat(r["show_start"]),
                "screen": r["screen_name"],
                "formats": formats,
                "show_type": r["show_type"],
            }
        )
    return out


def catalog(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every film with an upcoming screening, soonest showing first.

    This is the authoritative "what's on" list the website never offered.
    """
    now = _now_iso()
    rows = conn.execute(
        """
        SELECT m.*,
               COUNT(s.id)        AS upcoming_count,
               MIN(s.show_start)  AS next_show,
               MAX(s.show_start)  AS last_show
        FROM movies m
        JOIN shows s ON s.movie_main_version_id = m.main_version_id
        WHERE s.show_start >= :now
        GROUP BY m.main_version_id
        ORDER BY next_show
        """,
        {"now": now},
    ).fetchall()
    next_shows = _next_shows_by_movie(conn, now)
    result = []
    for row in rows:
        movie = _decode_movie(row)
        movie["next_show"] = datetime.fromisoformat(movie["next_show"])
        movie["last_show"] = datetime.fromisoformat(movie["last_show"])
        movie["next_shows"] = next_shows.get(movie["main_version_id"], [])
        result.append(movie)
    return result


def premieres(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Films whose national premiere is today or later, soonest first.

    Answers "what's premiering next week": each row carries ``premiere_date``
    and the film's next screening at Vega.
    """
    now = _now_iso()
    today = date.today().isoformat()
    rows = conn.execute(
        """
        SELECT m.*,
               MIN(CASE WHEN s.show_start >= :now THEN s.show_start END) AS next_show,
               COUNT(CASE WHEN s.show_start >= :now THEN s.id END)       AS upcoming_count
        FROM movies m
        JOIN shows s ON s.movie_main_version_id = m.main_version_id
        WHERE m.premiere_date >= :today
        GROUP BY m.main_version_id
        ORDER BY m.premiere_date, m.title
        """,
        {"now": now, "today": today},
    ).fetchall()
    next_shows = _next_shows_by_movie(conn, now)
    result = []
    for row in rows:
        movie = _decode_movie(row)
        movie["premiere_date"] = date.fromisoformat(movie["premiere_date"])
        movie["next_show"] = (
            datetime.fromisoformat(movie["next_show"]) if movie["next_show"] else None
        )
        movie["next_shows"] = next_shows.get(movie["main_version_id"], [])
        result.append(movie)
    return result


def day_index(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Upcoming days that have screenings, with a film count each."""
    rows = conn.execute(
        """
        SELECT show_date,
               COUNT(*)                          AS show_count,
               COUNT(DISTINCT movie_main_version_id) AS film_count
        FROM shows
        WHERE show_date >= :today
        GROUP BY show_date
        ORDER BY show_date
        """,
        {"today": date.today().isoformat()},
    ).fetchall()
    return [
        {"date": date.fromisoformat(r["show_date"]), **{k: r[k] for k in ("show_count", "film_count")}}
        for r in rows
    ]


def calendar_grid(conn: sqlite3.Connection) -> list[list[dict[str, Any]]]:
    """A month-style calendar as rows of 7 day-cells, Monday first.

    The grid runs from the Monday of the week containing the first upcoming
    screening to the Sunday of the week containing the last one. Each cell holds
    the day's date, its films (distinct, ordered by first showtime), and whether
    it falls outside the show range (leading/trailing padding). Returns ``[]``
    when there are no upcoming screenings.
    """
    rows = conn.execute(
        """
        SELECT s.show_date, s.movie_main_version_id AS mid, m.title,
               COUNT(*) AS shows, MIN(s.show_start) AS first_start
        FROM shows s
        JOIN movies m ON m.main_version_id = s.movie_main_version_id
        WHERE s.show_date >= :today
        GROUP BY s.show_date, mid
        ORDER BY s.show_date, first_start
        """,
        {"today": date.today().isoformat()},
    ).fetchall()
    if not rows:
        return []

    films_by_day: dict[date, list[dict[str, Any]]] = {}
    for r in rows:
        day = date.fromisoformat(r["show_date"])
        films_by_day.setdefault(day, []).append(
            {"main_id": r["mid"], "title": r["title"], "shows": r["shows"]}
        )

    show_days = sorted(films_by_day)
    first, last = show_days[0], show_days[-1]
    start = first - timedelta(days=first.weekday())  # Monday of the first week
    end = last + timedelta(days=6 - last.weekday())  # Sunday of the last week

    weeks: list[list[dict[str, Any]]] = []
    day = start
    while day <= end:
        week = [
            {
                "date": day + timedelta(days=i),
                "films": films_by_day.get(day + timedelta(days=i), []),
                "muted": (day + timedelta(days=i)) < first or (day + timedelta(days=i)) > last,
            }
            for i in range(7)
        ]
        weeks.append(week)
        day += timedelta(days=7)
    return weeks


def shows_on(conn: sqlite3.Connection, day: date) -> list[dict[str, Any]]:
    """All screenings on a given day, with the essentials for a listing."""
    rows = conn.execute(
        """
        SELECT s.*, m.title, m.running_minutes, m.age_rating, m.genres, m.poster_ref
        FROM shows s
        JOIN movies m ON m.main_version_id = s.movie_main_version_id
        WHERE s.show_date = :day
        ORDER BY s.show_start, s.screen_name
        """,
        {"day": day.isoformat()},
    ).fetchall()
    result = []
    for row in rows:
        show = _decode_show(row)
        show["genres"] = json.loads(show["genres"]) if show.get("genres") else []
        show["poster_url"] = sanity_image_url(show.get("poster_ref"), width=320, height=180)
        result.append(show)
    return result


def movie_detail(conn: sqlite3.Connection, main_id: str) -> dict[str, Any] | None:
    """A single film plus all of its upcoming screenings, or ``None``."""
    row = conn.execute(
        "SELECT * FROM movies WHERE main_version_id = ?", (main_id,)
    ).fetchone()
    if row is None:
        return None
    movie = _decode_movie(row)
    show_rows = conn.execute(
        """
        SELECT * FROM shows
        WHERE movie_main_version_id = ? AND show_start >= ?
        ORDER BY show_start
        """,
        (main_id, _now_iso()),
    ).fetchall()
    movie["shows"] = [_decode_show(r) for r in show_rows]
    return movie

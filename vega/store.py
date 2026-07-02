"""Local SQLite store for the Vega Scene *film* program.

We mirror film screenings only. Vega Scene also hosts theatre/culture events,
but those are ignored at the ingestion boundary (see ``CINEMA_SCREENS``) so the
persistence layer never learns they exist.

Two tables:

* ``movies`` — one row per ``movieMainVersionId``, enriched from the Filmweb
  record and/or the cinema's CMS article. Metadata is upserted, never deleted.
* ``shows``  — one row per screening, keyed by the ebillett event id. Each sync
  is a full refresh of the forward window: shows no longer offered are pruned,
  so cancellations disappear on their own.

Everything a caller needs is expressed as plain SQL over these two tables; the
row models below just give us validated, self-documenting inserts.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from vega.models import FilmwebMovie, Program, Show, VegaArticle

DEFAULT_DB_PATH = Path("vega.db")

# Vega Scene has exactly three cinema screens. Every film screening runs on one
# of these; anything on another screen (Teatersal, Salongen, …) is a
# theatre/culture event and is filtered out at ingestion.
CINEMA_SCREENS = frozenset({"Vega Kino EN", "Vega Kino TO", "Vega Kino TRE"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS movies (
    main_version_id     TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    original_title      TEXT,
    year                INTEGER,
    running_time        TEXT,
    running_minutes     INTEGER,
    age_rating          TEXT,
    age_rating_reason   TEXT,
    recommended_age     TEXT,
    premiere_date       TEXT,                   -- ISO date; national premiere
    genres              TEXT,                   -- JSON array of names
    director            TEXT,
    cast                TEXT,
    writer              TEXT,
    nationality         TEXT,                   -- JSON array
    original_language   TEXT,                   -- JSON array
    production_company  TEXT,
    synopsis            TEXT,
    poster_ref          TEXT,                   -- Sanity image asset ref
    reviews             TEXT,                   -- JSON
    trailers            TEXT,                   -- JSON
    has_filmweb         INTEGER NOT NULL DEFAULT 0,
    has_vega_article    INTEGER NOT NULL DEFAULT 0,
    raw_json            TEXT,                   -- full filmweb + vega dump
    first_seen_at       TEXT NOT NULL,
    last_synced_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shows (
    id                    TEXT PRIMARY KEY,       -- ebillett event id (or composite fallback)
    movie_main_version_id TEXT NOT NULL,
    movie_version_id      TEXT NOT NULL,
    movie_title           TEXT NOT NULL,
    screen_name           TEXT NOT NULL,
    show_start            TEXT NOT NULL,          -- ISO datetime
    show_date             TEXT NOT NULL,          -- ISO date (indexed for day filtering)
    show_type             TEXT,
    ticket_sale_url       TEXT,
    version_tags          TEXT,                   -- JSON array of tag strings
    ticket_status         TEXT,                   -- e.g. 'UTSOLGT' (nullable)
    first_seen_at         TEXT NOT NULL,
    last_synced_at        TEXT NOT NULL,
    FOREIGN KEY (movie_main_version_id) REFERENCES movies(main_version_id)
);

CREATE INDEX IF NOT EXISTS idx_shows_date ON shows(show_date);
CREATE INDEX IF NOT EXISTS idx_shows_movie ON shows(movie_main_version_id);
CREATE INDEX IF NOT EXISTS idx_shows_start ON shows(show_start);
CREATE INDEX IF NOT EXISTS idx_movies_premiere ON movies(premiere_date);

CREATE TABLE IF NOT EXISTS sync_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    days          INTEGER,
    movies_seen   INTEGER,
    shows_seen    INTEGER,
    shows_pruned  INTEGER,
    ok            INTEGER NOT NULL DEFAULT 0,
    error         TEXT
);
"""


# --- row models --------------------------------------------------------------


class MovieRow(BaseModel):
    """A row for the ``movies`` table."""

    main_version_id: str
    title: str
    original_title: str | None = None
    year: int | None = None
    running_time: str | None = None
    running_minutes: int | None = None
    age_rating: str | None = None
    age_rating_reason: str | None = None
    recommended_age: str | None = None
    premiere_date: str | None = None
    genres: str | None = None
    director: str | None = None
    cast: str | None = None
    writer: str | None = None
    nationality: str | None = None
    original_language: str | None = None
    production_company: str | None = None
    synopsis: str | None = None
    poster_ref: str | None = None
    reviews: str | None = None
    trailers: str | None = None
    has_filmweb: int = 0
    has_vega_article: int = 0
    raw_json: str | None = None


class ShowRow(BaseModel):
    """A row for the ``shows`` table."""

    id: str
    movie_main_version_id: str
    movie_version_id: str
    movie_title: str
    screen_name: str
    show_start: str
    show_date: str
    show_type: str | None = None
    ticket_sale_url: str | None = None
    version_tags: str | None = None
    ticket_status: str | None = None


# --- Program -> rows transform -----------------------------------------------


def _json_or_none(value: object) -> str | None:
    return json.dumps(value, ensure_ascii=False) if value else None


def film_shows(program: Program) -> list[Show]:
    """Screenings that are films, i.e. run on one of the three cinema screens.

    This is the single ingestion filter that keeps theatre/culture events out of
    the store entirely.
    """
    return [s for s in program.shows if s.screen_name in CINEMA_SCREENS]


def movie_rows(program: Program) -> list[MovieRow]:
    """Build a movie row for every film referenced by the program's screenings.

    Keyed off the film screenings (not the raw document dicts), so theatre
    titles never produce a row, and a film whose show has no Filmweb/CMS document
    still gets a row (title taken from the show).
    """
    show_titles = {s.movie_main_version_id: s.movie_title for s in film_shows(program)}
    main_ids = set(show_titles)

    rows: list[MovieRow] = []
    for main_id in main_ids:
        fw: FilmwebMovie | None = program.filmweb_movies.get(main_id)
        va: VegaArticle | None = program.vega_articles.get(main_id)

        title = (
            (fw.title if fw else None)
            or (va.title if va else None)
            or show_titles.get(main_id)
            or main_id
        )
        raw = {
            "filmweb": fw.model_dump(by_alias=True, mode="json") if fw else None,
            "vega_article": va.model_dump(by_alias=True, mode="json") if va else None,
        }
        rows.append(
            MovieRow(
                main_version_id=main_id,
                title=title,
                original_title=fw.original_title if fw else None,
                year=fw.year if fw else None,
                running_time=fw.running_time if fw else None,
                running_minutes=fw.running_minutes if fw else None,
                age_rating=fw.age_rating.age if fw and fw.age_rating else None,
                age_rating_reason=fw.age_rating.age_reason if fw and fw.age_rating else None,
                recommended_age=fw.age_rating.recommended_age if fw and fw.age_rating else None,
                premiere_date=(
                    fw.premiere.premiere_date.isoformat()
                    if fw and fw.premiere and fw.premiere.premiere_date
                    else None
                ),
                genres=_json_or_none([g.name for g in fw.genres]) if fw else None,
                director=fw.director if fw else None,
                cast=fw.cast if fw else None,
                writer=fw.writer if fw else None,
                nationality=_json_or_none(fw.nationality) if fw else None,
                original_language=_json_or_none(fw.original_language) if fw else None,
                production_company=fw.production_company if fw else None,
                synopsis=fw.synopsis if fw else None,
                # Use the cinema CMS poster (a 16:9 landscape promo image). The
                # Filmweb postersV2 refs are portrait but live in Filmweb's own
                # Sanity project, so they 404 on the vegascene CDN — unusable.
                # Titles without a CMS article just get no poster.
                poster_ref=va.poster_ref if va else None,
                reviews=(
                    _json_or_none([r.model_dump(mode="json") for r in fw.reviews]) if fw else None
                ),
                trailers=(
                    _json_or_none([t.model_dump(mode="json") for t in fw.trailers]) if fw else None
                ),
                has_filmweb=int(fw is not None),
                has_vega_article=int(va is not None),
                raw_json=json.dumps(raw, ensure_ascii=False),
            )
        )
    return rows


def _ticket_status_index(program: Program) -> dict[tuple[str, str], str]:
    """Map ``(main_version_id, showtime_iso) -> ticket status`` from CMS showTags."""
    index: dict[tuple[str, str], str] = {}
    for main_id, article in program.vega_articles.items():
        for tag in article.show_tags:
            index[(main_id, tag.show_time.isoformat())] = tag.tags
    return index


def _show_id(show: Show) -> str:
    """Stable primary key for a screening.

    Prefer the ebillett event id (guaranteed unique per screening); fall back to
    a composite of the fields that make a screening unique if the URL is missing.
    """
    if show.event_id:
        return f"evt-{show.event_id}"
    return f"{show.movie_version_id}|{show.screen_name}|{show.show_start.isoformat()}"


def show_rows(program: Program) -> list[ShowRow]:
    status_index = _ticket_status_index(program)
    rows: list[ShowRow] = []
    for show in film_shows(program):
        start = show.show_start
        rows.append(
            ShowRow(
                id=_show_id(show),
                movie_main_version_id=show.movie_main_version_id,
                movie_version_id=show.movie_version_id,
                movie_title=show.movie_title,
                screen_name=show.screen_name,
                show_start=start.isoformat(),
                show_date=start.date().isoformat(),
                show_type=show.show_type or None,
                ticket_sale_url=show.ticket_sale_url,
                # Keep each tag's API type: 'version' is routine (2D / subtitle
                # language), while 'format'/'versionother' flag notable
                # presentation annotations like Dolby Atmos and 35 mm.
                version_tags=_json_or_none(
                    [{"tag": t.tag, "type": t.type} for t in show.version_tags]
                ),
                ticket_status=status_index.get(
                    (show.movie_main_version_id, start.isoformat())
                ),
            )
        )
    return rows


# --- upsert SQL (generated from the row models) ------------------------------


def _upsert_sql(table: str, columns: list[str], pk: str, timestamp_cols: list[str]) -> str:
    """Build an idempotent upsert that preserves ``first_seen_at`` across updates."""
    all_cols = columns + timestamp_cols
    placeholders = ", ".join(f":{c}" for c in all_cols)
    # On conflict, refresh every data column and last_synced_at, but leave
    # first_seen_at untouched so it records when we first saw the row.
    updates = ", ".join(
        f"{c}=excluded.{c}" for c in columns + ["last_synced_at"]
    )
    return (
        f"INSERT INTO {table} ({', '.join(all_cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({pk}) DO UPDATE SET {updates}"
    )


_MOVIE_COLS = list(MovieRow.model_fields)
_SHOW_COLS = list(ShowRow.model_fields)
_MOVIE_UPSERT = _upsert_sql("movies", _MOVIE_COLS, "main_version_id", ["first_seen_at", "last_synced_at"])
_SHOW_UPSERT = _upsert_sql("shows", _SHOW_COLS, "id", ["first_seen_at", "last_synced_at"])


# --- store -------------------------------------------------------------------


class Store:
    """Thin wrapper around the SQLite connection holding the program."""

    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Generator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def upsert_movies(self, rows: Iterable[MovieRow], synced_at: str) -> None:
        params = [{**r.model_dump(), "first_seen_at": synced_at, "last_synced_at": synced_at} for r in rows]
        if not params:
            return
        with self._tx() as conn:
            conn.executemany(_MOVIE_UPSERT, params)

    def upsert_shows(self, rows: Iterable[ShowRow], synced_at: str) -> None:
        params = [{**r.model_dump(), "first_seen_at": synced_at, "last_synced_at": synced_at} for r in rows]
        if not params:
            return
        with self._tx() as conn:
            conn.executemany(_SHOW_UPSERT, params)

    def prune_shows(self, keep_ids: set[str], from_date: date) -> int:
        """Delete shows on/after ``from_date`` whose id was not seen this run.

        This is what makes the store self-heal: a screening the API stops
        returning (cancelled, rescheduled) is dropped. Past shows are left as a
        historical record.
        """
        cutoff = from_date.isoformat()
        with self._tx() as conn:
            existing = {
                r["id"]
                for r in conn.execute("SELECT id FROM shows WHERE show_date >= ?", (cutoff,))
            }
            stale = existing - keep_ids
            if stale:
                conn.executemany("DELETE FROM shows WHERE id = ?", [(i,) for i in stale])
        return len(stale)

    # --- sync bookkeeping ----------------------------------------------------

    def start_run(self, started_at: str) -> int:
        with self._tx() as conn:
            cur = conn.execute("INSERT INTO sync_runs (started_at) VALUES (?)", (started_at,))
        return int(cur.lastrowid or 0)

    def finish_run(
        self,
        run_id: int,
        finished_at: str,
        *,
        days: int,
        movies_seen: int,
        shows_seen: int,
        shows_pruned: int,
        ok: bool,
        error: str | None = None,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE sync_runs SET finished_at=?, days=?, movies_seen=?, shows_seen=?, "
                "shows_pruned=?, ok=?, error=? WHERE id=?",
                (finished_at, days, movies_seen, shows_seen, shows_pruned, int(ok), error, run_id),
            )

    # --- convenience counts (used by the CLI status output) ------------------

    def counts(self) -> dict[str, int]:
        cur = self.conn.execute(
            "SELECT (SELECT COUNT(*) FROM movies), (SELECT COUNT(*) FROM shows), "
            "(SELECT COUNT(*) FROM shows WHERE show_date >= date('now'))"
        )
        movies, shows, upcoming = cur.fetchone()
        return {"movies": movies, "shows": shows, "upcoming_shows": upcoming}

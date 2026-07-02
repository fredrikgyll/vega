"""A small local website for browsing the mirrored film program.

Server-rendered with FastAPI + Jinja2, backed directly by the SQLite store
(read-only, one connection per request). Pages:

* ``/``              — everything on, soonest first (the authoritative list)
* ``/premieres``     — films premiering today or later
* ``/calendar``      — upcoming days with screenings
* ``/day/{date}``    — one day's screenings
* ``/movie/{id}``    — a film with synopsis, reviews and all its showtimes
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from vega import queries, templating
from vega.store import DEFAULT_DB_PATH


def create_app(db_path: str | Path = DEFAULT_DB_PATH) -> FastAPI:
    app = FastAPI(title="Vega Scene program")
    app.mount("/static", StaticFiles(directory=str(templating.STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(templating.TEMPLATES_DIR))
    templates.env.filters.update(templating.FILTERS)
    templates.env.globals["base"] = ""  # served from the domain root locally
    db_path = str(db_path)

    def connect() -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        conn = connect()
        try:
            films = queries.catalog(conn)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "catalog.html",
            {"films": films, "today": date.today(), "active": "catalog"},
        )

    @app.get("/premieres", response_class=HTMLResponse)
    def premieres(request: Request) -> HTMLResponse:
        conn = connect()
        try:
            films = queries.premieres(conn)
        finally:
            conn.close()
        next_week = date.today() + timedelta(days=7)
        return templates.TemplateResponse(
            request,
            "premieres.html",
            {"films": films, "today": date.today(), "next_week": next_week, "active": "premieres"},
        )

    @app.get("/calendar", response_class=HTMLResponse)
    def calendar(request: Request) -> HTMLResponse:
        conn = connect()
        try:
            weeks = queries.calendar_grid(conn)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "calendar.html",
            {"weeks": weeks, "weekdays": templating.NO_DAYS, "active": "calendar"},
        )

    @app.get("/day/{day}", response_class=HTMLResponse)
    def day_view(request: Request, day: str) -> HTMLResponse:
        try:
            the_day = date.fromisoformat(day)
        except ValueError:
            raise HTTPException(status_code=404, detail="bad date")
        conn = connect()
        try:
            shows = queries.shows_on(conn, the_day)
            days = [d["date"] for d in queries.day_index(conn)]
        finally:
            conn.close()
        prev_day = max((d for d in days if d < the_day), default=None)
        next_day = min((d for d in days if d > the_day), default=None)
        return templates.TemplateResponse(
            request,
            "day.html",
            {
                "day": the_day,
                "shows": shows,
                "prev_day": prev_day,
                "next_day": next_day,
                "active": "calendar",
            },
        )

    @app.get("/movie/{main_id}", response_class=HTMLResponse)
    def movie_view(request: Request, main_id: str) -> HTMLResponse:
        conn = connect()
        try:
            movie = queries.movie_detail(conn, main_id)
        finally:
            conn.close()
        if movie is None:
            raise HTTPException(status_code=404, detail="unknown film")
        return templates.TemplateResponse(
            request, "movie.html", {"movie": movie, "active": None}
        )

    return app

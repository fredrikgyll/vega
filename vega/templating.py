"""Jinja templates + filters, shared by the live server and the static build.

Kept free of any web-framework dependency so the static generator can render
the same templates with a plain Jinja environment.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import jinja2

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

# Norwegian short names, Monday-first (matches date.weekday()).
NO_DAYS = ["man", "tir", "ons", "tor", "fre", "lør", "søn"]
NO_MONTHS = ["jan", "feb", "mar", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "des"]


def no_date(value: date | datetime) -> str:
    return f"{NO_DAYS[value.weekday()]} {value.day}. {NO_MONTHS[value.month - 1]}"


def no_datetime(value: datetime) -> str:
    return f"{no_date(value)} {value:%H:%M}"


def no_month(value: date | datetime) -> str:
    return NO_MONTHS[value.month - 1]


FILTERS = {"no_date": no_date, "no_datetime": no_datetime, "no_month": no_month}


def environment(base: str = "") -> jinja2.Environment:
    """A standalone Jinja environment (used by the static build).

    ``base`` is a URL path prefix (e.g. ``"/vega"`` for a GitHub Pages project
    site) prepended to every internal link via the ``base`` template global; ""
    serves from the domain root.
    """
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=jinja2.select_autoescape(["html"]),
    )
    env.filters.update(FILTERS)
    env.globals["base"] = base
    return env

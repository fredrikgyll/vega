"""Pydantic models for the Vega Scene API payloads.

Everything comes from one endpoint, which returns the whole forward schedule:

* ``GET /api/program?includeDocuments=true`` -> Program

A ``Program`` bundles the bare ``shows`` with two dictionaries of movie
metadata, both keyed by ``movieMainVersionId`` (NOT ``movieVersionId``):

* ``filmwebMovies`` — the national film registry record (rich: synopsis, cast,
  genres, running time, age rating, premiere date, reviews, …). Present for
  every film.
* ``vegaArticles``  — the cinema's own CMS entry (poster, per-showtime ticket
  status, and a ``_type`` that tells films apart from culture/theatre events).
  Present for most titles; culture events (``KUL…`` ids) only have this.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, computed_field


def _list_or_empty(value: object) -> object:
    """Coerce an explicit ``null`` into an empty list.

    The API sends ``null`` (not just an absent key) for some list fields such as
    ``trailers`` and ``genres``; ``default_factory`` alone does not cover that.
    """
    return [] if value is None else value


def _dict_or_empty(value: object) -> object:
    return {} if value is None else value

# --- shared config -----------------------------------------------------------

# API payloads use camelCase and carry Sanity/Filmweb bookkeeping fields we do
# not model. ``extra="allow"`` keeps those around so nothing is silently lost
# (we persist the full dump as raw_json), and ``populate_by_name`` lets us build
# instances by field name in tests.
_ApiConfig = ConfigDict(populate_by_name=True, extra="allow")


# --- shows -------------------------------------------------------------------


class VersionTag(BaseModel):
    """A screening variant marker, e.g. ``{"tag": "Norsk tekst", "type": "version"}``."""

    model_config = _ApiConfig
    tag: str
    type: str


class Show(BaseModel):
    """One screening, as returned in ``program.shows``."""

    model_config = _ApiConfig

    screen_name: str = Field(alias="screenName")
    ticket_sale_url: str = Field(alias="ticketSaleUrl")
    show_type: str = Field(alias="showType", default="")
    show_start: datetime = Field(alias="showStart")
    movie_version_id: str = Field(alias="movieVersionId")
    movie_main_version_id: str = Field(alias="movieMainVersionId")
    movie_title: str = Field(alias="movieTitle")
    version_tags: Annotated[list[VersionTag], BeforeValidator(_list_or_empty)] = Field(
        alias="versionTags", default_factory=list
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def event_id(self) -> str | None:
        """The ebillett event id embedded in the ticket URL — unique per screening.

        e.g. ``…/events/59354/purchase`` -> ``"59354"``. Used as the stable
        primary key for a show row.
        """
        match = re.search(r"/events/(\d+)", self.ticket_sale_url)
        return match.group(1) if match else None


# --- vega CMS article (poster + ticket status) -------------------------------


class ShowTag(BaseModel):
    """Per-showtime availability marker, e.g. ``FÅ BILLETTER`` / ``UTSOLGT``."""

    model_config = _ApiConfig
    show_time: datetime = Field(alias="showTime")
    tags: str


class _AssetRef(BaseModel):
    model_config = _ApiConfig
    ref: str | None = Field(alias="_ref", default=None)


class _ImageField(BaseModel):
    model_config = _ApiConfig
    asset: _AssetRef | None = None


class _ImageDocument(BaseModel):
    model_config = _ApiConfig
    image: _ImageField | None = None


class VegaArticle(BaseModel):
    """The cinema's own CMS entry for a title (Sanity document)."""

    model_config = _ApiConfig

    type: str = Field(alias="_type")  # vegaSceneFilmomtale | vegaSceneKulturomtale
    title: str | None = None
    show_tags: Annotated[list[ShowTag], BeforeValidator(_list_or_empty)] = Field(
        alias="showTags", default_factory=list
    )
    horizontal_poster: _ImageDocument | None = Field(alias="horizontalPoster", default=None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def poster_ref(self) -> str | None:
        poster = self.horizontal_poster
        if poster and poster.image and poster.image.asset:
            return poster.image.asset.ref
        return None


# --- filmweb (national film registry) record ---------------------------------


class AgeRating(BaseModel):
    model_config = _ApiConfig
    age: str | None = None
    age_reason: str | None = Field(alias="ageReason", default=None)
    recommended_age: str | None = Field(alias="recommendedAge", default=None)


class Premiere(BaseModel):
    model_config = _ApiConfig
    premiere_date: date | None = Field(alias="premiereDate", default=None)
    no_cinema_release: bool = Field(alias="noCinemaRelease", default=False)


class Genre(BaseModel):
    model_config = _ApiConfig
    name: str


class Review(BaseModel):
    model_config = _ApiConfig
    who: str | None = None
    dice_value: str | None = Field(alias="diceValue", default=None)
    review_url: str | None = Field(alias="reviewUrl", default=None)


class Trailer(BaseModel):
    model_config = _ApiConfig
    title: str | None = None
    video_id: str | None = Field(alias="videoId", default=None)


class _PortableTextSpan(BaseModel):
    model_config = _ApiConfig
    text: str = ""


class _PortableTextBlock(BaseModel):
    model_config = _ApiConfig
    children: Annotated[list[_PortableTextSpan], BeforeValidator(_list_or_empty)] = Field(
        default_factory=list
    )


class FilmwebMovie(BaseModel):
    """The Filmweb registry record — the rich metadata source for a film."""

    model_config = _ApiConfig

    title: str | None = None
    original_title: str | None = Field(alias="originalTitle", default=None)
    year: int | None = None
    running_time: str | None = Field(alias="runningTime", default=None)
    age_rating: AgeRating | None = Field(alias="ageRating", default=None)
    premiere: Premiere | None = None
    genres: Annotated[list[Genre], BeforeValidator(_list_or_empty)] = Field(default_factory=list)
    director: str | None = Field(alias="directorV2", default=None)
    cast: str | None = Field(alias="castV2", default=None)
    writer: str | None = Field(alias="writerV2", default=None)
    nationality: Annotated[list[str], BeforeValidator(_list_or_empty)] = Field(default_factory=list)
    original_language: Annotated[list[str], BeforeValidator(_list_or_empty)] = Field(
        alias="originalLanguage", default_factory=list
    )
    production_company: str | None = Field(alias="productionCompany", default=None)
    reviews: Annotated[list[Review], BeforeValidator(_list_or_empty)] = Field(default_factory=list)
    trailers: Annotated[list[Trailer], BeforeValidator(_list_or_empty)] = Field(
        default_factory=list
    )
    body_text: Annotated[list[_PortableTextBlock], BeforeValidator(_list_or_empty)] = Field(
        alias="bodyText", default_factory=list
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def synopsis(self) -> str | None:
        """Flatten the Sanity portable-text ``bodyText`` into plain paragraphs."""
        blocks = [
            "".join(span.text for span in block.children).strip()
            for block in self.body_text
        ]
        text = "\n\n".join(b for b in blocks if b)
        return text or None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def running_minutes(self) -> int | None:
        """Parse ``"1 t. 50 min."`` (Norwegian) into total minutes."""
        if not self.running_time:
            return None
        hours = re.search(r"(\d+)\s*t", self.running_time)
        minutes = re.search(r"(\d+)\s*min", self.running_time)
        total = (int(hours.group(1)) * 60 if hours else 0) + (
            int(minutes.group(1)) if minutes else 0
        )
        return total or None


# --- program (endpoint response) ---------------------------------------------


class Program(BaseModel):
    """Response of ``GET /api/program`` (by date or by movieId)."""

    model_config = _ApiConfig

    shows: Annotated[list[Show], BeforeValidator(_list_or_empty)] = Field(default_factory=list)
    vega_articles: Annotated[dict[str, VegaArticle], BeforeValidator(_dict_or_empty)] = Field(
        alias="vegaArticles", default_factory=dict
    )
    filmweb_movies: Annotated[dict[str, FilmwebMovie], BeforeValidator(_dict_or_empty)] = Field(
        alias="filmwebMovies", default_factory=dict
    )

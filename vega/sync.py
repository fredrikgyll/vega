"""The periodic pull task: mirror the whole film program into the store.

The Vega Scene program endpoint returns the entire forward schedule in a single
call (``/api/program?includeDocuments=true``, with no ``date``), so a sync is:

1. fetch the full program (all shows + movie documents) in one request,
2. upsert every film movie and show (theatre screenings are filtered out),
3. prune shows on/after today that we did *not* see this run.

Step 3 keeps the mirror honest: a screening the API stops returning (cancelled
or rescheduled) is dropped, while movie metadata is only ever added or refreshed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from pydantic import BaseModel

from vega.api import VegaSceneClient
from vega.store import Store, movie_rows, show_rows


class SyncResult(BaseModel):
    days: int
    movies_seen: int
    shows_seen: int
    shows_pruned: int


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sync(
    store: Store,
    client: VegaSceneClient,
    *,
    on_progress: Callable[[str], None] | None = None,
) -> SyncResult:
    """Run one full sync. Returns counts; raises if the run fails."""

    def report(message: str) -> None:
        if on_progress is not None:
            on_progress(message)

    started_at = _now_iso()
    run_id = store.start_run(started_at)

    try:
        report("fetching program…")
        program = client.program()
        movies = movie_rows(program)
        shows = show_rows(program)
        store.upsert_movies(movies, started_at)
        store.upsert_shows(shows, started_at)

        seen_movie_ids = {m.main_version_id for m in movies}
        seen_show_ids = {s.id for s in shows}
        days = len({s.show_date for s in shows})
        if shows:
            span = f"{min(s.show_date for s in shows)} … {max(s.show_date for s in shows)}"
            report(f"{len(shows)} film shows / {len(seen_movie_ids)} movies over {days} days ({span})")

        # Prune everything from today forward that we didn't just see. The
        # response is the whole forward schedule, so anything still stored for
        # today onward but absent now has been dropped upstream.
        pruned = store.prune_shows(seen_show_ids, date.today())
        if pruned:
            report(f"pruned {pruned} stale shows")

        result = SyncResult(
            days=days,
            movies_seen=len(seen_movie_ids),
            shows_seen=len(seen_show_ids),
            shows_pruned=pruned,
        )
        store.finish_run(
            run_id,
            _now_iso(),
            days=result.days,
            movies_seen=result.movies_seen,
            shows_seen=result.shows_seen,
            shows_pruned=result.shows_pruned,
            ok=True,
        )
        return result
    except Exception as exc:
        store.finish_run(
            run_id,
            _now_iso(),
            days=0,
            movies_seen=0,
            shows_seen=0,
            shows_pruned=0,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise

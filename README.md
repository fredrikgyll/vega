# vega

A small service that mirrors the [Vega Scene](https://www.vegascene.no/) **film**
program into a local SQLite store and lets you browse the whole upcoming schedule
in one place — instead of paging through the website one day at a time.

Theatre/culture events are ignored on purpose: only screenings on the three
cinema screens (Vega Kino EN / TO / TRE) are stored.

## Usage

```sh
uv run vega sync             # pull the full forward program into vega.db
uv run vega status           # show store counts and the last sync
uv run vega serve            # browse at http://127.0.0.1:8000
uv run vega build --out dist # render the whole site to static HTML
```

Run `sync` on a timer (cron / launchd) to keep the local mirror fresh.

## Static site / deploy

`vega build` renders every page to flat HTML (no server or DB needed at
runtime) — one file per movie/day plus the index, premieres and calendar. Pass
`--base /<subpath>` when the site is served from a subpath (e.g. a GitHub Pages
project site at `…/vega/`); omit it for a domain root.

`.github/workflows/deploy.yml` builds and deploys to GitHub Pages on every push
to `main` and on a 6-hour cron (so the schedule stays current, since "upcoming"
is computed at build time).

## How it works

- `vega/models.py` — the Vega Scene API payloads as Pydantic models.
- `vega/api.py` — `VegaSceneClient`, an httpx2 (HTTP/2) wrapper over the
  `program` endpoint, which returns the entire forward schedule in one request.
- `vega/store.py` — the SQLite store: schema, the API→rows transform (this is
  where the three-screen film filter lives), idempotent upserts, and prune.
- `vega/sync.py` — the pull task: fetch the whole program in one call, upsert
  everything, then prune any forward screening we no longer see (so
  cancellations self-heal).
- `vega/queries.py` + `vega/web.py` + `vega/templates/` — the local web UI.

The store keeps two tables: `movies` (one row per film, enriched from Filmweb
and the cinema's CMS) and `shows` (one row per screening). `vega.db` is a
disposable local cache — delete it and re-run `vega sync` to rebuild.

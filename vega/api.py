"""HTTP client for the Vega Scene API, built on httpx2 (HTTP/2)."""

from __future__ import annotations

import httpx2

from vega.models import Program

BASE_URL = "https://www.vegascene.no"


class VegaSceneClient:
    """Typed wrapper over the Vega Scene program endpoint.

    Called with neither ``date`` nor ``movieId``, ``/api/program`` returns the
    entire forward schedule in one response, so a full sync needs a single
    request. We always pass ``includeDocuments=true`` so the response carries
    the movie metadata (Filmweb records + CMS articles) alongside the shows.
    """

    def __init__(self, *, timeout: float = 30.0, client: httpx2.Client | None = None) -> None:
        self._client = client or httpx2.Client(
            base_url=BASE_URL,
            http2=True,
            timeout=timeout,
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "vega-program-mirror/0.1 (personal use)",
            },
            # A couple of connect-level retries smooth over transient blips
            # during the periodic pull.
            transport=httpx2.HTTPTransport(retries=2),
        )

    def program(self) -> Program:
        """Return the entire forward program (all dates) in one request."""
        resp = self._client.get("/api/program", params={"includeDocuments": "true"})
        resp.raise_for_status()
        return Program.model_validate(resp.json())

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> VegaSceneClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

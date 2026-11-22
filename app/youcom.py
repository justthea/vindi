"""You.com API client: Search finds pages, Contents re-reads one, Research profiles a seller.

Endpoints and parameters follow https://you.com/docs/guides/search.md and
https://you.com/docs/guides/research.md as read on 2026-11-22. The Contents
request and response fields are the least certain; check them against the docs.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlsplit

from . import mock
from .config import Settings
from .models import QuerySpec, RawHit

SEARCH_URL = "https://ydc-index.io/v1/search"
CONTENTS_URL = "https://ydc-index.io/v1/contents"
RESEARCH_URL = "https://api.you.com/v1/research"


class YouError(RuntimeError):
    pass


def domain_of(url: str) -> str:
    return urlsplit(url.strip()).netloc.lower().removeprefix("www.")


def _as_text(value: Any) -> str:
    """Flatten whatever shape a text field arrives in into one string."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_as_text(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(t for t in (_as_text(v) for v in value) if t)
    return str(value)


def parse_search_response(data: dict, spec: QuerySpec) -> list[RawHit]:
    results = (data.get("results") or {}).get("web") or []
    hits = []
    for item in results:
        url = (item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        contents = item.get("contents") or {}
        extracted = _as_text(contents.get("markdown")) or _as_text(contents.get("highlights"))
        snippets = _as_text(item.get("snippets"))
        hits.append(
            RawHit(
                url=url,
                domain=domain_of(url),
                title=_as_text(item.get("title")),
                description=_as_text(item.get("description")),
                text="\n".join(t for t in (snippets, extracted) if t)[:6000],
                page_age=item.get("page_age"),
                query_label=spec.label,
                snippet_only=not extracted,
            )
        )
    return hits


class YouClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self._http = None

    def _client(self):
        if self._http is None:
            import httpx  # imported lazily so mock mode needs no network libraries

            self._http = httpx.AsyncClient(
                headers={"X-API-Key": self.s.ydc_api_key},
                timeout=httpx.Timeout(25.0, connect=10.0),
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()

    async def _post(self, url: str, body: dict, timeout: Optional[float] = None) -> Any:
        kwargs = {"timeout": timeout} if timeout else {}
        response = await self._client().post(url, json=body, **kwargs)
        if response.status_code >= 400:
            raise YouError(f"HTTP {response.status_code} from {url}: {response.text[:200]}")
        return response.json()

    # ---- Search -----------------------------------------------------------

    async def search(self, spec: QuerySpec, exclude_domains: Optional[list[str]] = None) -> list[RawHit]:
        if self.s.mock:
            return parse_search_response(mock.search_payload(spec), spec)

        body: dict[str, Any] = {"query": spec.query, "count": self.s.search_count}
        if spec.freshness:
            body["freshness"] = spec.freshness
        if spec.include_domains:
            body["include_domains"] = spec.include_domains
        elif exclude_domains:
            body["exclude_domains"] = exclude_domains

        if self.s.extraction_mode == "highlights":
            body["extraction"] = {"extraction_mode": "highlights"}
        elif self.s.extraction_mode == "full_page":
            body["extraction"] = {"extraction_mode": "full_page"}
            body["crawl_timeout"] = 8

        try:
            data = await self._post(SEARCH_URL, body)
        except YouError:
            if "extraction" not in body:
                raise
            # Extraction can fail on its own; plain snippets are still usable.
            body.pop("extraction", None)
            body.pop("crawl_timeout", None)
            data = await self._post(SEARCH_URL, body)
        return parse_search_response(data, spec)

    # ---- Contents ---------------------------------------------------------

    async def contents(self, url: str) -> Optional[str]:
        """Fresh text of one page, or None when it cannot be fetched."""
        if self.s.mock:
            return None
        try:
            data = await self._post(CONTENTS_URL, {"urls": [url], "formats": ["markdown"]}, timeout=30)
        except Exception:
            return None
        pages = data if isinstance(data, list) else (data.get("results") or data.get("data") or [])
        for page in pages:
            if isinstance(page, dict):
                text = _as_text(page.get("markdown")) or _as_text(page.get("html")) or _as_text(page.get("content"))
                if text:
                    return text
        return None

    # ---- Research ---------------------------------------------------------

    async def research(self, question: str, schema: Optional[dict] = None, domain: str = "") -> dict:
        """Returns {"content_type": "text" | "object", "content": ..., "sources": [...]}."""
        if self.s.mock:
            return mock.research(domain or "seller.example")

        body: dict[str, Any] = {"input": question, "research_effort": self.s.research_effort}
        use_schema = bool(schema) and self.s.research_effort != "lite"  # lite has no schema support
        if use_schema:
            body["output_schema"] = schema
        try:
            data = await self._post(RESEARCH_URL, body, timeout=120)
        except YouError:
            if not use_schema:
                raise
            body.pop("output_schema")  # schema output is beta; fall back to Markdown
            data = await self._post(RESEARCH_URL, body, timeout=120)

        output = data.get("output") or {}
        sources = [
            {"url": s.get("url", ""), "title": _as_text(s.get("title")) or s.get("url", "")}
            for s in output.get("sources") or []
            if isinstance(s, dict) and str(s.get("url", "")).startswith(("http://", "https://"))
        ]
        content = output.get("content", "")
        return {
            "content_type": "object" if isinstance(content, dict) else "text",
            "content": content,
            "sources": sources[:8],
        }

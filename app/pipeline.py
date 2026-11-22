"""Scan pipeline: search fan-out, filter, classify, verify, rank.

Design rule: the model classifies and points at evidence; this module checks
that evidence and decides what the creator is told to do.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import AsyncIterator, Optional
from urllib.parse import urlsplit

from .config import CACHE_DIR, Settings
from .llm import classify_batch
from .models import Hit, Identity, RawHit, Verdict
from .queries import SOCIAL_AND_REFERENCE, build_queries
from .youcom import YouClient

BATCH_SIZE = 6
PER_DOMAIN_CAP = 3
FLAGGED = {"unauthorized_commercial", "claimed_endorsement"}
LABEL_RANK = {
    "unauthorized_commercial": 0,
    "claimed_endorsement": 1,
    "unclear": 2,
    "editorial_or_fan": 3,
    "authorized_or_own": 4,
}
_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|ref$|ref_|mc_)")


# ---- text helpers -----------------------------------------------------------


def normalize_url(url: str) -> str:
    """Same page, same key: drops scheme, www, trailing slash and tracking params."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    params = sorted(
        kv for kv in parts.query.split("&") if kv and not _TRACKING.match(kv.split("=")[0].lower())
    )
    return host + parts.path.rstrip("/") + ("?" + "&".join(params) if params else "")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'}))
    return " ".join(text.lower().split())


def verify_quote(quote: str, text: str) -> bool:
    """True only when the quote appears word for word in the text we fetched."""
    q = _norm(quote)
    return len(q) >= 8 and q in _norm(text)


def find_identifier(text: str, identity: Identity) -> tuple[str, str]:
    """Which of the creator's identifiers is on the page: (matched, kind) or ("", "")."""
    haystack = _norm(text)
    for phrase in identity.phrases:
        if _norm(phrase) in haystack:
            return phrase, "phrase"
    for handle in identity.handles:
        if re.search(r"(?<![a-z0-9_])@?" + re.escape(handle.lower()) + r"(?![a-z0-9_])", haystack):
            return handle, "handle"
    if _norm(identity.name) in haystack:
        return identity.name, "name"
    return "", ""


def context_ok(text: str, identity: Identity, kind: str) -> bool:
    """For common names, require a handle or niche keyword on the same page."""
    if kind != "name" or not identity.strict_name_match:
        return True
    haystack = _norm(text)
    clues = [h.lower() for h in identity.handles] + [_norm(k) for k in identity.niche]
    return any(clue and clue in haystack for clue in clues)


def fallback_quote(text: str, identifier: str, width: int = 160) -> str:
    """A verbatim window around the identifier, used when the model's quote fails."""
    if not identifier:
        return ""
    match = re.search(re.escape(identifier), text, re.IGNORECASE)
    if not match:
        return ""
    start = max(0, match.start() - width // 2)
    end = min(len(text), match.end() + width // 2)
    return " ".join(text[start:end].split())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def is_own_or_partner(domain: str, identity: Identity) -> bool:
    if any(domain == d or domain.endswith("." + d) for d in identity.own_domains):
        return True
    domain_slug = _slug(domain.rsplit(".", 1)[0])
    return any(len(_slug(p)) >= 3 and _slug(p) in domain_slug for p in identity.partners)


# ---- candidate selection ----------------------------------------------------


def select_candidates(raw: list[RawHit], identity: Identity, limit: int) -> tuple[list[RawHit], dict]:
    stats = {"pages_checked": len(raw), "duplicates": 0, "own_or_partner": 0, "no_match": 0, "capped": 0}
    by_url: dict[str, RawHit] = {}
    for hit in raw:
        key = normalize_url(hit.url)
        kept = by_url.get(key)
        if kept is None:
            by_url[key] = hit
        else:
            stats["duplicates"] += 1
            if len(hit.text) > len(kept.text):
                by_url[key] = hit

    candidates: list[RawHit] = []
    per_domain: dict[str, int] = {}
    for hit in by_url.values():
        if is_own_or_partner(hit.domain, identity):
            stats["own_or_partner"] += 1
            continue
        matched, kind = find_identifier(hit.searchable, identity)
        if not matched:
            stats["no_match"] += 1  # search engines return near misses; we do not flag those
            continue
        if per_domain.get(hit.domain, 0) >= PER_DOMAIN_CAP or len(candidates) >= limit:
            stats["capped"] += 1
            continue
        per_domain[hit.domain] = per_domain.get(hit.domain, 0) + 1
        candidates.append(
            hit.model_copy(update={"id": f"h{len(candidates) + 1}", "matched_identifier": matched, "match_kind": kind})
        )
    return candidates, stats


# ---- verdict checking -------------------------------------------------------


def finalize(raw: RawHit, verdict: Optional[Verdict], identity: Identity) -> Hit:
    """Turn the model's verdict into what the creator sees, after checking it."""
    text = raw.searchable
    if verdict is None:
        label, reason, confidence, signal = "unclear", "The classifier returned no verdict for this page.", 0.0, ""
        quote, verified = fallback_quote(text, raw.matched_identifier), False
    else:
        label, reason, confidence, signal = verdict.label, verdict.reason, verdict.confidence, verdict.commercial_signal
        quote = " ".join(verdict.evidence_quote.split())
        verified = verify_quote(quote, text)
        if not verified:
            # The quote is not on the page as fetched, so we do not trust the label.
            quote = fallback_quote(text, raw.matched_identifier)
            if label in FLAGGED:
                label = "unclear"
                reason = "The quoted evidence could not be found on the page. " + reason
            confidence = min(confidence, 0.4)

    if label in FLAGGED and not context_ok(text, identity, raw.match_kind):
        label = "unclear"
        reason = "The name matches, but no handle or niche keyword is on the page. " + reason
        confidence = min(confidence, 0.4)

    if label in FLAGGED:
        action = "offer_deal_or_stop"
    elif label == "unclear":
        action = "review"
    else:
        action = "none"

    return Hit(
        id=raw.id,
        url=raw.url,
        domain=raw.domain,
        title=raw.title,
        description=raw.description,
        page_age=raw.page_age,
        query_label=raw.query_label,
        snippet_only=raw.snippet_only,
        matched_identifier=raw.matched_identifier,
        label=label,
        evidence_quote=quote,
        quote_verified=verified,
        commercial_signal=signal,
        confidence=round(confidence, 2),
        reason=reason.strip(),
        suggested_action=action,
        text=text[:4000],
    )


def rank_key(hit: Hit) -> tuple:
    return (LABEL_RANK[hit.label], -hit.confidence)


# ---- cache ------------------------------------------------------------------


def save_cache(identity: Identity, hits: list[Hit], summary: dict) -> None:
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "identity": identity.model_dump(),
            "hits": [h.model_dump() for h in hits],
            "summary": summary,
        }
        (CACHE_DIR / "last_scan.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    except OSError:
        pass  # the cache is a convenience; a scan never fails because of it


def load_cache() -> Optional[dict]:
    try:
        return json.loads((CACHE_DIR / "last_scan.json").read_text())
    except (OSError, ValueError):
        return None


# ---- the scan ---------------------------------------------------------------


def _event(name: str, **data) -> dict:
    return {"event": name, "data": data}


async def scan(identity: Identity, settings: Settings, you: Optional[YouClient] = None) -> AsyncIterator[dict]:
    """Yields status, hit and done events as work completes."""
    started = time.monotonic()
    you = you or YouClient(settings)
    specs = build_queries(identity, settings.max_queries)
    exclude = sorted(set(SOCIAL_AND_REFERENCE) | set(identity.own_domains))

    yield _event("status", stage="searching", queries_total=len(specs), queries_done=0, pages_checked=0)

    async def run_query(spec):
        try:
            return await you.search(spec, exclude_domains=exclude), None
        except Exception as exc:  # one failed query must not sink the scan
            return [], f"{spec.label}: {exc}"

    raw: list[RawHit] = []
    errors: list[str] = []
    done = 0
    for future in asyncio.as_completed([asyncio.ensure_future(run_query(s)) for s in specs]):
        hits, error = await future
        done += 1
        raw.extend(hits)
        if error:
            errors.append(error)
        yield _event(
            "status", stage="searching", queries_total=len(specs), queries_done=done, pages_checked=len(raw), error=error
        )

    candidates, stats = select_candidates(raw, identity, settings.max_candidates)
    yield _event("status", stage="classifying", candidates=len(candidates), **stats)

    async def run_batch(batch):
        try:
            return batch, await classify_batch(identity, batch, settings), None
        except Exception as exc:
            return batch, {}, f"classifier: {exc}"

    batches = [candidates[i : i + BATCH_SIZE] for i in range(0, len(candidates), BATCH_SIZE)]
    results: list[Hit] = []
    for future in asyncio.as_completed([asyncio.ensure_future(run_batch(b)) for b in batches]):
        batch, verdicts, error = await future
        if error:
            errors.append(error)
        for raw_hit in batch:
            hit = finalize(raw_hit, verdicts.get(raw_hit.id), identity)
            results.append(hit)
            yield _event("hit", **hit.model_dump())

    results.sort(key=rank_key)
    counts: dict[str, int] = {}
    for hit in results:
        counts[hit.label] = counts.get(hit.label, 0) + 1
    summary = {
        **stats,
        "queries": len(specs),
        "candidates": len(candidates),
        "flagged": sum(counts.get(label, 0) for label in FLAGGED),
        "counts": counts,
        "errors": errors,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "mock": settings.mock,
    }
    if results and not settings.mock:
        save_cache(identity, results, summary)
    yield _event("done", **summary)

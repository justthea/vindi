"""Development fixtures, used only when MOCK=1.

They let the UI and the pipeline run with no API keys. Every domain ends in
.example and the UI shows a MOCK banner. Never demo in this mode.
"""

from __future__ import annotations

import re

from .models import QuerySpec, RawHit


def _subject(spec: QuerySpec) -> str:
    match = re.search(r'"([^"]+)"', spec.query)
    return match.group(1) if match else "Sample Creator"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def search_payload(spec: QuerySpec) -> dict:
    """A response in the same shape as the You.com Search API."""
    s, slug = _subject(spec), _slug(_subject(spec))
    hoodie = {
        "url": "https://printhaus.example/listing/88211",
        "title": f"{s} Hoodie - Unisex Heavy Blend",
        "description": f"Show your love for {s} with this premium hoodie. $34.99. Add to cart.",
        "snippets": [f"{s} Hoodie. $34.99. Add to cart. Ships in 3 days. 214 sold."],
        "contents": {"highlights": [f"Fans of {s} will love this heavyweight hoodie. Printed on demand."]},
    }
    by_label = {
        "merch_marketplaces": [
            hoodie,
            {
                "url": "https://printhaus.example/listing/88212",
                "title": f"{s} Quote Mug 11oz",
                "description": f"Ceramic mug featuring a {s} quote. $16.50. Buy it now.",
                "snippets": [f"{s} Quote Mug. $16.50. Buy it now. Free shipping over $35."],
            },
        ],
        "merch_open_web": [
            dict(hoodie, url="https://www.printhaus.example/listing/88211/?utm_source=feed"),
        ],
        "endorsement_claims": [
            {
                "url": "https://glowlabs.example/products/whisk-pro",
                "title": f"Whisk Pro, recommended by {s}",
                "description": f"The mixer attachment recommended by {s}. Buy now for $59.",
                "snippets": [f"As seen on {s}'s channel. Whisk Pro is recommended by {s}. Buy now for $59."],
                "page_age": "2026-11-19T09:00:00",
            },
            {
                "url": f"https://dailycrumb.example/interviews/{slug}",
                "title": f"An interview with {s}",
                "description": f"We sat down with {s} to talk about building an audience.",
                "snippets": [f"In this interview, {s} explains how the channel started."],
            },
        ],
        "discount_codes": [
            {
                "url": f"https://dealcodes.example/{slug}",
                "title": f"{s} discount code: 20% off",
                "description": f"Use {s} promo code SAVE20 at checkout. Verified today.",
                "snippets": [f"{s} promo code SAVE20. 20% off sitewide. Shop now."],
            }
        ],
        "official_store": [
            {
                "url": f"https://{slug}-official-shop.example/",
                "title": f"Official {s} Shop",
                "description": f"The official store for {s} fans. Shop tees from $19.",
                "snippets": [f"Welcome to the Official {s} Shop. Shop tees from $19. Add to cart."],
            }
        ],
    }
    return {"results": {"web": by_label.get(spec.label, [])}, "metadata": {"query": spec.query}}


def classify(hits: list[RawHit]) -> dict:
    """Rule-based stand-in for the LLM classifier."""
    verdicts = []
    for hit in hits:
        text = hit.searchable
        lower = text.lower()
        sale = re.search(r"\$\d+[\d.]*|add to cart|buy (it )?now|shop now", lower)
        if "recommended by" in lower or "as seen on" in lower:
            label, reason = "claimed_endorsement", "The page says the creator recommends this product."
        elif sale:
            label, reason = "unauthorized_commercial", "The creator's name is in a listing with a price or buy button."
        else:
            label, reason = "editorial_or_fan", "The page talks about the creator and sells nothing under their name."
        sentence = next(
            (s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if hit.matched_identifier.lower() in s.lower()),
            "",
        )
        verdicts.append(
            {
                "id": hit.id,
                "label": label,
                "evidence_quote": sentence[:240],
                "commercial_signal": sale.group(0) if sale else "",
                "confidence": 0.8,
                "reason": reason,
            }
        )
    return {"verdicts": verdicts}


def research(domain: str) -> dict:
    return {
        "content_type": "object",
        "content": {
            "operator": f"Unknown operator of {domain} (mock)",
            "what_they_sell": "Print-on-demand apparel and mugs",
            "size": "Small online store",
            "location": "unknown",
            "contact": f"hello@{domain}",
            "creator_partnerships": "None found",
        },
        "sources": [{"url": f"https://{domain}/about", "title": f"About {domain}"}],
    }


def draft(name: str, url: str, quote: str, mode: str) -> dict:
    if mode == "deal":
        return {
            "subject": f"Your {name} products: let's make it official",
            "body": (
                f"Hi,\n\nI'm {name}. I noticed this page uses my name: {url}\n\n"
                f'It says: "{quote}"\n\n'
                "We don't have an agreement, but it tells me your customers respond to my name. "
                "I'd like to talk about a licensed collaboration at [rate]. Are you free for a call this week?\n\n"
                f"Best,\n{name}"
            ),
        }
    return {
        "subject": f"Please remove my name from your listing",
        "body": (
            f"Hi,\n\nI'm {name}. This page uses my name without my permission: {url}\n\n"
            f'It says: "{quote}"\n\n'
            "Please remove my name from this page within 7 days and reply to confirm.\n\n"
            f"Thank you,\n{name}"
        ),
    }

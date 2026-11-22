"""Turns a creator's identity into search queries, most valuable first."""

from __future__ import annotations

from .models import Identity, QuerySpec

MARKETPLACES = [
    "etsy.com",
    "amazon.com",
    "ebay.com",
    "redbubble.com",
    "teepublic.com",
    "zazzle.com",
    "society6.com",
    "aliexpress.com",
    "temu.com",
    "walmart.com",
]

# Excluded from every search: profiles, commentary and reference pages are almost
# never the sellers we are looking for, and they crowd out the results that are.
SOCIAL_AND_REFERENCE = [
    "instagram.com",
    "tiktok.com",
    "youtube.com",
    "x.com",
    "twitter.com",
    "facebook.com",
    "linkedin.com",
    "reddit.com",
    "threads.net",
    "wikipedia.org",
]


def _q(text: str) -> str:
    return '"' + text.replace('"', "") + '"'


def build_queries(identity: Identity, max_queries: int = 12) -> list[QuerySpec]:
    name = _q(identity.name)
    specs: list[QuerySpec] = []

    def add(label: str, query: str, **kwargs) -> None:
        specs.append(QuerySpec(label=label, query=query, **kwargs))

    add("merch_marketplaces", f"{name} merch OR shirt OR hoodie OR mug OR poster", include_domains=MARKETPLACES)
    for phrase in identity.phrases[:3]:
        add("phrase_marketplaces", f"{_q(phrase)} shirt OR sticker OR print OR mug", include_domains=MARKETPLACES)
    add("endorsement_claims", f'{name} "as seen on" OR "recommended by" OR "approved by" OR "favorite of"')
    add("merch_open_web", f"{name} merch OR shirt OR hoodie buy")
    add("official_store", f'{name} "official store" OR "official shop" OR "official merch"')
    add("discount_codes", f'{name} "discount code" OR "promo code" OR coupon')
    add("digital_products", f"{name} course OR ebook OR preset OR template")
    for handle in identity.handles[:1]:
        add("handle_shop", f"{_q(handle)} shop OR buy OR merch")
    add("partnership_claims", f'{name} "partnered with" OR sponsored OR "in collaboration with"', freshness="month")
    add("new_this_week", f"{name} buy OR shop OR sale", freshness="week")
    for handle in identity.handles[:2]:
        add("handle_inspired", f'{_q(handle)} "inspired by" OR dupe OR "as seen on"')

    return specs[:max_queries]

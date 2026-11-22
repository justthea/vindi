"""Logic tests. They run with no API keys:  python -m pytest -q"""

import asyncio
from dataclasses import replace

import pytest

from app.config import get_settings
from app.llm import extract_json
from app.models import Identity, QuerySpec, RawHit, Verdict
from app.pipeline import (
    context_ok,
    fallback_quote,
    finalize,
    find_identifier,
    is_own_or_partner,
    normalize_url,
    scan,
    select_candidates,
    verify_quote,
)
from app.queries import build_queries
from app.youcom import parse_search_response

DANA = Identity(
    name="Dana Rivera",
    handles=["@danabakes"],
    phrases=["crumb goals"],
    partners=["King Arthur"],
    own_domains=["https://www.danabakes.com/shop"],
    niche=["baking"],
)


def raw(url="https://shop.example/p/1", title="", description="", text="", **kw) -> RawHit:
    return RawHit(url=url, domain=url.split("/")[2].removeprefix("www."), title=title, description=description, text=text, **kw)


def test_identity_is_cleaned():
    assert DANA.handles == ["danabakes"]
    assert DANA.own_domains == ["danabakes.com"]


def test_queries_are_capped_and_quote_the_name():
    specs = build_queries(DANA, max_queries=12)
    assert 8 <= len(specs) <= 12
    assert specs[0].label == "merch_marketplaces" and specs[0].include_domains
    assert all('"' in s.query for s in specs)
    assert any(s.freshness == "week" for s in specs)


def test_normalize_url_merges_tracking_and_www():
    a = normalize_url("https://www.shop.example/p/1/?utm_source=x&color=red")
    b = normalize_url("http://shop.example/p/1?color=red&fbclid=abc")
    assert a == b == "shop.example/p/1?color=red"


def test_verify_quote_is_exact_but_whitespace_and_case_tolerant():
    page = "Fans of  Dana Rivera\nwill love this hoodie. $34.99"
    assert verify_quote("fans of dana rivera will love this hoodie", page)
    assert not verify_quote("Dana Rivera personally designed this hoodie", page)
    assert not verify_quote("Dana", page)  # too short to count as evidence


def test_find_identifier_prefers_specific_matches():
    assert find_identifier("Get your Crumb Goals tee", DANA) == ("crumb goals", "phrase")
    assert find_identifier("as seen on @danabakes today", DANA) == ("danabakes", "handle")
    assert find_identifier("Dana Rivera hoodie", DANA) == ("Dana Rivera", "name")
    assert find_identifier("danabakeshop clearance", DANA) == ("", "")


def test_common_name_needs_context():
    strict = DANA.model_copy(update={"strict_name_match": True})
    assert not context_ok("Dana Rivera for city council", strict, "name")
    assert context_ok("Dana Rivera baking mat", strict, "name")
    assert context_ok("Dana Rivera for city council", DANA, "name")


def test_own_and_partner_domains_are_dropped():
    assert is_own_or_partner("danabakes.com", DANA)
    assert is_own_or_partner("shop.danabakes.com", DANA)
    assert is_own_or_partner("kingarthurbaking.com", DANA)
    assert not is_own_or_partner("printhaus.example", DANA)


def test_select_candidates_dedupes_filters_and_caps():
    hits = [
        raw("https://shop.example/p/1", title="Dana Rivera hoodie"),
        raw("https://www.shop.example/p/1/?utm_source=a", title="Dana Rivera hoodie", text="longer text Dana Rivera"),
        raw("https://danabakes.com/store", title="Dana Rivera store"),
        raw("https://other.example/x", title="Unrelated Dana Riviera page"),
    ] + [raw(f"https://bulk.example/{i}", title="Dana Rivera mug") for i in range(5)]
    candidates, stats = select_candidates(hits, DANA, limit=48)
    assert stats["duplicates"] == 1 and stats["own_or_partner"] == 1 and stats["no_match"] == 1
    assert stats["capped"] == 2  # five from one domain, three kept
    assert [c.id for c in candidates] == ["h1", "h2", "h3", "h4"]
    assert candidates[0].text == "longer text Dana Rivera"


def test_finalize_downgrades_unverified_quotes():
    hit = raw(title="Dana Rivera Hoodie", text="Dana Rivera Hoodie. $34.99. Add to cart.", id="h1", matched_identifier="Dana Rivera", match_kind="name")
    good = finalize(hit, Verdict(id="h1", label="unauthorized_commercial", evidence_quote="Dana Rivera Hoodie. $34.99.", confidence=0.9), DANA)
    assert good.label == "unauthorized_commercial" and good.quote_verified and good.suggested_action == "offer_deal_or_stop"

    invented = finalize(hit, Verdict(id="h1", label="unauthorized_commercial", evidence_quote="Officially endorsed by Dana Rivera", confidence=0.9), DANA)
    assert invented.label == "unclear" and not invented.quote_verified and invented.confidence <= 0.4
    assert "Dana Rivera" in invented.evidence_quote  # replaced by a real window from the page

    missing = finalize(hit, None, DANA)
    assert missing.label == "unclear" and missing.suggested_action == "review"


def test_fallback_quote_is_verbatim():
    text = "Lots of words before. Buy the Dana Rivera mug today for $16. Lots of words after."
    quote = fallback_quote(text, "dana rivera", width=40)
    assert "Dana Rivera" in quote and verify_quote(quote, text)


def test_extract_json_tolerates_fences_and_chatter():
    assert extract_json('Here you go:\n```json\n{"verdicts": []}\n```') == {"verdicts": []}
    with pytest.raises(Exception):
        extract_json("no json here")


def test_parse_search_response_handles_shapes():
    spec = QuerySpec(label="t", query='"x"')
    data = {
        "results": {
            "web": [
                {"url": "https://a.example/1", "title": "T", "snippets": ["one", "two"], "contents": {"highlights": ["hi"]}},
                {"url": "https://b.example/2", "title": "T2", "contents": {"markdown": "# Page"}},
                {"url": "javascript:alert(1)", "title": "bad"},
                {"url": "https://c.example/3"},
            ]
        }
    }
    hits = parse_search_response(data, spec)
    assert [h.domain for h in hits] == ["a.example", "b.example", "c.example"]
    assert hits[0].text == "one\ntwo\nhi" and not hits[0].snippet_only
    assert hits[2].snippet_only
    assert parse_search_response({}, spec) == []


def test_full_scan_in_mock_mode():
    settings = replace(get_settings(), mock=True)

    async def run():
        return [event async for event in scan(DANA, settings)]

    events = asyncio.run(run())
    names = [e["event"] for e in events]
    assert names[0] == "status" and names[-1] == "done"
    hits = [e["data"] for e in events if e["event"] == "hit"]
    labels = {h["label"] for h in hits}
    assert {"unauthorized_commercial", "claimed_endorsement", "editorial_or_fan"} <= labels
    assert all(h["quote_verified"] for h in hits if h["label"] != "unclear")
    done = events[-1]["data"]
    assert done["duplicates"] >= 1 and done["flagged"] >= 3 and done["errors"] == []


def test_resale_marketplaces_are_recognized():
    from app.pipeline import is_resale_marketplace

    for d in ["ebay.com", "www.ebay.co.uk", "ebay.de", "poshmark.com", "gem.app", "depop.com", "mercari.com", "stockx.com"]:
        assert is_resale_marketplace(d), d
    for d in ["etsy.com", "redbubble.com", "amazon.com", "notebay.com", "gem.com", "kyliejennermerch.shop"]:
        assert not is_resale_marketplace(d), d


def test_finalize_never_flags_resale_as_unauthorized():
    hit = raw(
        url="https://www.ebay.com/itm/123",
        title="Dana Rivera Limited Edition Hoodie",
        text="Dana Rivera Limited Edition Hoodie size S. Pre-owned. $40",
        id="h1",
        matched_identifier="Dana Rivera",
        match_kind="name",
    )
    v = Verdict(id="h1", label="unauthorized_commercial", evidence_quote="Dana Rivera Limited Edition Hoodie", confidence=0.9)
    out = finalize(hit, v, DANA)
    assert out.label == "unclear" and out.confidence <= 0.5 and out.suggested_action == "review"
    assert "resale" in out.reason.lower()

    # A new-product listing on a print-on-demand site is still flagged.
    pod = raw(url="https://www.redbubble.com/i/1", title="Dana Rivera Hoodie", text="Dana Rivera Hoodie. $34.99", id="h2", matched_identifier="Dana Rivera", match_kind="name")
    out2 = finalize(pod, Verdict(id="h2", label="unauthorized_commercial", evidence_quote="Dana Rivera Hoodie", confidence=0.9), DANA)
    assert out2.label == "unauthorized_commercial"


def test_coupon_page_for_name_store_is_not_flagged_outright():
    coupon = raw(url="https://dealdrop.example/x", title="50% Off Dana Rivera Shop Coupons & Promo Codes", text="Dana Rivera Shop coupon codes", id="h1", matched_identifier="Dana Rivera", match_kind="name")
    v = Verdict(id="h1", label="unauthorized_commercial", evidence_quote="Dana Rivera Shop coupon codes", confidence=0.9)
    out = finalize(coupon, v, DANA)
    assert out.label == "unclear" and out.confidence <= 0.5

    # A fake store page with "Shop" in its title but no coupons is still flagged.
    store = raw(url="https://danariveramerch.shop/", title="Dana Rivera Shop - Official Merchandise Store", text="Dana Rivera Shop - Official Merchandise Store. Buy now", id="h2", matched_identifier="Dana Rivera", match_kind="name")
    out2 = finalize(store, Verdict(id="h2", label="unauthorized_commercial", evidence_quote="Dana Rivera Shop - Official Merchandise Store", confidence=0.9), DANA)
    assert out2.label == "unauthorized_commercial"

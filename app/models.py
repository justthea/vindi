"""Data shapes shared by the pipeline, the API and the UI."""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Label = Literal[
    "unauthorized_commercial",
    "claimed_endorsement",
    "editorial_or_fan",
    "authorized_or_own",
    "unclear",
]
Action = Literal["offer_deal_or_stop", "review", "none"]


def clean_domain(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"^[a-z]+://", "", value)
    value = value.split("/")[0]
    return value.removeprefix("www.")


def _clean_list(values: list[str]) -> list[str]:
    seen, out = set(), []
    for v in values or []:
        v = " ".join(str(v).split())
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


class Identity(BaseModel):
    """What the creator tells us about themselves."""

    name: str = Field(min_length=2, max_length=80)
    handles: list[str] = []
    phrases: list[str] = []
    partners: list[str] = []  # brand names or domains the creator already works with
    own_domains: list[str] = []
    niche: list[str] = []  # keywords that tell this person apart, e.g. "baking"
    strict_name_match: bool = False  # set when the name is common

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("handles")
    @classmethod
    def _handles(cls, v: list[str]) -> list[str]:
        return _clean_list([h.strip().lstrip("@") for h in v or []])

    @field_validator("phrases", "partners", "niche")
    @classmethod
    def _lists(cls, v: list[str]) -> list[str]:
        return _clean_list(v)

    @field_validator("own_domains")
    @classmethod
    def _domains(cls, v: list[str]) -> list[str]:
        return _clean_list([clean_domain(d) for d in v or []])


class QuerySpec(BaseModel):
    label: str
    query: str
    include_domains: Optional[list[str]] = None
    freshness: Optional[str] = None  # day, week, month, year


class RawHit(BaseModel):
    """One search result before classification."""

    id: str = ""
    url: str
    domain: str
    title: str = ""
    description: str = ""
    text: str = ""  # snippets plus extracted page text, when we got any
    page_age: Optional[str] = None
    query_label: str = ""
    snippet_only: bool = True  # False when You.com returned extracted page content
    matched_identifier: str = ""
    match_kind: str = ""  # name, handle or phrase

    @property
    def searchable(self) -> str:
        return "\n".join(part for part in (self.title, self.description, self.text) if part)


class Verdict(BaseModel):
    """What the classifier says about one hit. Treated as untrusted input."""

    id: str
    label: Label = "unclear"
    evidence_quote: str = ""
    commercial_signal: str = ""
    confidence: float = 0.0
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


class Hit(BaseModel):
    """A classified hit as shown to the creator."""

    id: str
    url: str
    domain: str
    title: str
    description: str
    page_age: Optional[str]
    query_label: str
    snippet_only: bool
    matched_identifier: str
    label: Label
    evidence_quote: str
    quote_verified: bool
    commercial_signal: str
    confidence: float
    reason: str
    suggested_action: Action
    text: str = ""  # kept so the draft step can fall back to it


class BriefRequest(BaseModel):
    url: str
    domain: str
    title: str = ""


class DraftRequest(BaseModel):
    identity: Identity
    hit: Hit
    mode: Literal["deal", "stop"] = "deal"
    brief: Optional[str] = None

# Tollbooth

Find who is selling with a creator's name, and turn them into paying partners.

Tollbooth scans the live web for sellers and brands using a creator's name, handle or catchphrase without a deal. It shows each page with the exact words found and a source link, then drafts a licensing offer or a removal request that the creator edits and sends themselves.

Built at Creator Economy Hack (localhost:nyc, Nov 22 2026). Track: Creator Business.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add YDC_API_KEY, LLM_API_KEY, LLM_MODEL
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000.

No keys yet? `MOCK=1 uvicorn app.main:app --port 8000` runs the whole flow on canned fixtures so the UI can be worked on. The page shows a MOCK banner. Do not demo in this mode.

Tests need no keys: `python -m pytest -q`

### Check your You.com key first

```bash
curl -s https://ydc-index.io/v1/search \
  -H "X-API-Key: $YDC_API_KEY" -H "Content-Type: application/json" \
  -d '{"query": "\"Dana Rivera\" hoodie", "count": 3, "extraction": {"extraction_mode": "highlights"}}'
```

You should get `results.web[]` with `url`, `title`, `description`, `snippets`. If the response shape differs, the only place to change is `parse_search_response` in `app/youcom.py`.

## How You.com powers it

| Step | You.com API | Where |
| --- | --- | --- |
| Find pages that use the creator's name: 8 to 12 queries in parallel, marketplace sweeps with `include_domains`, a "new this week" pass with `freshness` | Search, `POST https://ydc-index.io/v1/search` | `app/queries.py`, `YouClient.search` |
| Re-read the chosen page when a draft is written, so the evidence is fresh and timestamped | Contents, `POST https://ydc-index.io/v1/contents` | `YouClient.contents`, `/api/draft` |
| Profile the seller: operator, size, contact, existing creator deals, as JSON via `output_schema` | Research, `POST https://api.you.com/v1/research` | `YouClient.research`, `/api/brief` |

Without live search there is no product: listings that use a creator's name appear and disappear week to week.

## How it works

1. `queries.py` turns the identity into search queries, most valuable first.
2. `youcom.py` runs them concurrently against You.com Search.
3. `pipeline.select_candidates` dedupes by URL, drops the creator's own sites and known partners, and keeps only pages that contain the name, handle or a catchphrase.
4. `llm.classify_batch` labels each page and points at an evidence quote.
5. `pipeline.finalize` checks the model's work and decides the action.
6. The UI streams cards as they arrive (server-sent events from `/api/scan`).
7. Picking a card calls Research for the seller brief and Contents plus the LLM for the draft.

### Guardrails

- **The model classifies and points; the engine decides.** The suggested action comes from code, not from the model.
- **Evidence must be verbatim.** If the model's quote is not found word for word in the fetched text, the hit is downgraded to "Needs your review" and the quote is replaced with a real excerpt.
- **Common names.** With "My name is common" ticked, a page that matches only the name must also contain a handle or niche keyword to be flagged.
- **Nothing is sent automatically.** Drafts are first-person emails for the creator to edit and send.
- **Page text is untrusted.** It is passed to the model as data and rendered in the browser as text only.
- **Failures are visible.** A failed query or classifier batch is skipped, counted, and reported in the status line. The last successful live scan is saved to `.cache/` and can be reloaded, clearly marked as saved.

## Limits

- Text only. It finds names, handles and phrases, not faces in photos or video.
- A flag means "worth a look", not a legal conclusion. Tollbooth is not a law firm and its drafts are not legal advice.
- Marketplaces often block page fetches. Those cards are marked "Snippet only".

## Layout

```
app/config.py     settings from .env
app/models.py     Identity, RawHit, Verdict, Hit
app/queries.py    query templates
app/youcom.py     Search, Contents, Research client
app/llm.py        classifier and draft prompts (Anthropic or any OpenAI-compatible endpoint)
app/pipeline.py   filter, verify, rank, scan
app/main.py       HTTP API (Starlette) and static UI
app/mock.py       development fixtures
web/index.html    the whole UI
tests/            logic tests
```

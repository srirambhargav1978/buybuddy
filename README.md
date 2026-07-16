# BuyBuddy

A personalized AI shopping assistant built with LangGraph — gated behind
login, with real (seeded) purchase history for its registered demo
accounts, a browsable catalog of 79 products, a live AI chat assistant
that recommends items based on what you actually bought before, and a
stylized "Try On" preview for eyewear, shirts, and outerwear.

Live demo: https://api-buybuddy.sbm78.au

## Demo accounts

| Username | Password | Type | Notes |
|---|---|---|---|
| `user1` | `user@12345` | Registered | Ava Chen — fitness & minimalist tech |
| `user2` | `user@12345` | Registered | Priya Nair — outdoor & adventure |
| `user3` | `user@12345` | Registered | Marcus Lee — streetwear & gadgets |
| `guest1`/`guest2`/`guest3` | `guest@12345` | Guest | Clean slate, no history |

Registered accounts come pre-seeded with 10-15 past purchases and a set of
editable preference tags the AI already knows about. Guests start fresh,
same as a brand-new shopper — the assistant builds up preferences from the
conversation itself instead.

## How it works

**Auth** — a small fixed set of demo accounts (`users.py`), cookie-based
sessions (`auth.py`). Every page and API route is gated behind login;
checkout is intentionally not implemented (browsing + AI recommendations
only, cart included).

**Personalization** — `db.py` persists purchase history and preference
tags in SQLite per registered account. On the first chat message of a
session, those preferences seed the agent's working state, and a summary
of recent purchases is passed to the reply-generation step so recommendations
can reference real history ("since you're into trail running...") without
inventing anything for guests.

A 3-node LangGraph agent (`agent.py`) runs on every chat message:

1. **`extract_preferences`** — reads the conversation so far plus the new
   message, and asks the model to return an updated JSON snapshot of what
   the shopper wants (category, budget, style keywords).
2. **`search_catalog`** — plain Python filtering/scoring over the product
   catalog (`catalog.py`, 79 items across 10 categories) using those
   preferences. No LLM call here, and deliberately no vector DB.
3. **`generate_reply`** — asks the model to write a natural, helpful
   response recommending specific candidates, optionally personalized
   using the shopper's profile.

All model calls go through a shared LiteLLM proxy rather than calling
OpenAI/Anthropic directly — one place to swap models, track cost, and
enforce guardrails across every app that uses it, not just this one.
Traces are sent to Langfuse automatically for full observability.

**Try On** — a stylized (not photo-realistic) mockup: each registered
account has a simple SVG avatar, and eligible items (eyewear, shirts,
outerwear) render as an overlay on that avatar in a modal.

## The agentic layer (`/agents`)

A second, genuinely agentic LangGraph — separate from the simple
recommendation flow above — visible at `/agents` after login. Six real
agent nodes with a graph that actually branches at runtime (`agentic_agents.py`):

1. **Classification** — labels the incoming message's intent
2. **Routing** — a rule-based agent on top of the classifier that decides
   which specialist handles it — an actual conditional edge in the graph,
   not a simulated choice
3. **Pricing Fetch** — catalog search + pricing for shopping intents
4. **Policy** — answers returns/shipping/warranty questions
5. **Handoff** — proposes escalating to a human, does not execute it
6. **Human-in-the-Loop** — a real pause. Execution stops via LangGraph's
   `interrupt()` and waits for an explicit approve/reject decision before
   continuing — nothing simulated about the wait.

The `/agents` page streams each agent's decision live over SSE as the
graph runs, lighting up tiles laid out in the graph's actual shape. Every
turn's full decision trail (which agent, what it decided, why) is
persisted to SQLite and browsable afterward — full traceability, not just
a live view. Each node is also a properly named span in the shared
Langfuse trace for that turn, for the developer-grade view of the same run.

## Stack

- **FastAPI** — HTTP API + serves the login page and app shell
- **LangGraph** — the personalized recommendation agent described above
- **SQLite** — purchase history + preference tags (`db.py`)
- **LiteLLM** — model gateway (not called directly from this repo)
- **Langfuse** — tracing/observability (wired via LiteLLM's callback)

## Running locally

```bash
pip install -r requirements.txt
export LITELLM_BASE_URL=http://localhost:4000
export LITELLM_MASTER_KEY=your-key
uvicorn main:app --reload
```

Requires a LiteLLM proxy running separately (see the
[infrastructure repo](https://github.com/srirambhargav1978/sbm78-infrastructure)
for that setup) — this app doesn't call model providers directly.

## Testing

`tests/test_smoke.py` runs the app end-to-end against a mocked LLM — no
API keys or LiteLLM proxy required. Covers login for every demo account,
the auth gate, catalog/cart, preferences, try-on eligibility, and
personalized vs. guest chat behavior.

```bash
pip install -r requirements.txt
python -m pytest tests/test_smoke.py -v
```

Worth running before pushing any change you're not fully sure about.

## API

- `GET /login`, `POST /login`, `GET /logout` — auth
- `GET /` — app shell (catalog + chat), requires login
- `GET /api/catalog?category=&q=` — browse/search products
- `GET /api/product/{id}` — product detail
- `GET|POST /api/cart`, `DELETE /api/cart/{id}` — cart (no checkout)
- `GET|POST /api/preferences`, `DELETE /api/preferences/{tag}` — editable preference tags (registered only)
- `GET /api/tryon/{id}` — stylized try-on data (registered only, eligible categories only)
- `POST /chat` — `{"message": "..."}` → personalized reply + candidates
- `GET /health` — health check

# BuyBuddy

A personalized shopping assistant built with LangGraph. Understands what a
shopper is looking for, remembers preferences across turns in a
conversation, and recommends products from a catalog based on that.

Live demo: https://api-buybuddy.sbm78.au

## How it works

A 3-node LangGraph agent runs on every incoming message:

1. **`extract_preferences`** — reads the conversation so far plus the new
   message, and asks the model to return an updated JSON snapshot of what
   the shopper wants (category, budget, style keywords). This is what makes
   it personalized — preferences persist and get refined across turns
   within a session, rather than treating every message in isolation.
2. **`search_catalog`** — plain Python filtering/scoring over the product
   catalog using those preferences. No LLM call here, and deliberately no
   vector DB — simple and fast for this scale.
3. **`generate_reply`** — asks the model to write a natural, helpful
   response recommending specific candidates from step 2, and to ask one
   clarifying follow-up if useful.

All model calls go through a LiteLLM proxy rather than calling OpenAI/
Anthropic directly — one place to swap models, track cost, and enforce
guardrails across every app that uses it, not just this one. Traces are
sent to Langfuse automatically for full observability into what the agent
did on each turn.

## Stack

- **FastAPI** — HTTP API + serves its own chat UI at `/`
- **LangGraph** — the agent graph described above
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

## API

- `GET /` — chat UI
- `POST /chat` — `{"session_id": "optional", "message": "..."}` → reply + recommended products
- `GET /health` — health check

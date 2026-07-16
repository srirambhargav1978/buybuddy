"""
BuyBuddy's LangGraph agent.

Three nodes, run in sequence for every incoming chat message:

  1. extract_preferences — reads the conversation so far + the new message,
     asks the model to update a running JSON snapshot of what the shopper
     wants (category, budget, style keywords). For registered users this
     starts pre-seeded from their on-file preferences/purchase history
     (see main.py), so the very first reply is already personalized —
     guests start from a blank slate, same as a brand-new shopper.
  2. search_catalog       — plain Python filtering over catalog.py using
     those preferences. No LLM call, no vector DB — deliberately simple.
  3. generate_reply       — asks the model to write a natural, helpful
     response that recommends specific candidates from step 2, and may
     reference the shopper's known purchase history when relevant.

All LLM calls go through the LiteLLM proxy (not directly to OpenAI/Anthropic),
using an OpenAI-compatible client pointed at LITELLM_BASE_URL. Langfuse
tracing wraps every graph run via the callback handler in main.py.
"""

import json
import os
from typing import Any, Dict, List, Optional, TypedDict

from openai import OpenAI
from langgraph.graph import StateGraph, END

from catalog import CATALOG

MODEL_NAME = os.environ.get("BUYBUDDY_MODEL", "gpt-4o-mini")


def _client() -> OpenAI:
    return OpenAI(
        base_url=os.environ.get("LITELLM_BASE_URL", "http://litellm:4000"),
        api_key=os.environ.get("LITELLM_MASTER_KEY", "sk-not-set"),
    )


class BuyBuddyState(TypedDict):
    session_id: str
    message: str
    history: List[Dict[str, str]]
    preferences: Dict[str, Any]
    candidates: List[Dict[str, Any]]
    reply: str
    user_profile: Optional[Dict[str, Any]]


PREFERENCE_SYSTEM_PROMPT = """You are a preference-extraction module for a shopping assistant.
Given the running preferences (JSON) and the newest user message, return an UPDATED JSON object
with keys: category (string or null), budget_max (number or null), style (array of keyword strings).
Only change keys the new message gives evidence for — keep existing values otherwise.
Respond with ONLY the JSON object, no commentary, no markdown fences."""

REPLY_SYSTEM_PROMPT = """You are BuyBuddy, a friendly and concise personalized retail shopping assistant.
You will be given the shopper's known preferences, a short list of candidate products, and — if this is
a returning registered shopper — a summary of their past purchases (user_profile). Write a warm, helpful
reply (3-5 sentences) that recommends 1-3 of the candidates by name, briefly says why each fits what the
shopper is looking for, and asks one natural follow-up question to keep narrowing things down.

If user_profile is present, subtly personalize the reply using it — e.g. referencing a relevant past
purchase or known preference ("since you're into trail running...") — without being repetitive or
mentioning "database" or "purchase history" mechanically; talk like a shop assistant who remembers a
regular customer. If user_profile is absent (a guest, or a new/unknown shopper), stay friendly and
neutral — do not invent history that wasn't given to you.

If there are no good candidates, say so honestly and ask a clarifying question instead of forcing a
recommendation. Do not invent products that aren't in the candidate list."""


def extract_preferences(state: BuyBuddyState) -> BuyBuddyState:
    try:
        resp = _client().chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": PREFERENCE_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({
                    "current_preferences": state.get("preferences", {}),
                    "new_message": state["message"],
                })},
            ],
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        updated = json.loads(raw)
        merged = {**state.get("preferences", {}), **{k: v for k, v in updated.items() if v not in (None, [], "")}}
        state["preferences"] = merged
    except Exception:
        # If preference extraction fails for any reason, keep prior
        # preferences rather than breaking the whole turn.
        state.setdefault("preferences", {})
    return state


def search_catalog(state: BuyBuddyState) -> BuyBuddyState:
    prefs = state.get("preferences", {})
    category = (prefs.get("category") or "").lower()
    budget_max = prefs.get("budget_max")
    style = [s.lower() for s in (prefs.get("style") or [])]

    def score(item):
        s = 0
        if category and category in item["category"].lower():
            s += 3
        if budget_max:
            try:
                if item["price"] <= float(budget_max):
                    s += 2
            except (TypeError, ValueError):
                pass
        if style:
            s += len(set(style) & set(t.lower() for t in item["tags"]))
        return s

    ranked = sorted(CATALOG, key=score, reverse=True)
    # Only keep items that actually matched something if we have preferences yet;
    # otherwise show a small default spread so the first reply isn't empty.
    if category or budget_max or style:
        candidates = [c for c in ranked if score(c) > 0][:5]
    else:
        candidates = CATALOG[:5]

    state["candidates"] = candidates
    return state


def generate_reply(state: BuyBuddyState) -> BuyBuddyState:
    try:
        resp = _client().chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": REPLY_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({
                    "preferences": state.get("preferences", {}),
                    "candidates": state.get("candidates", []),
                    "conversation_so_far": state.get("history", []),
                    "latest_message": state["message"],
                    "user_profile": state.get("user_profile"),
                })},
            ],
            temperature=0.6,
        )
        state["reply"] = resp.choices[0].message.content.strip()
    except Exception as exc:
        state["reply"] = (
            "I'm having trouble reaching the model right now "
            f"({exc.__class__.__name__}). Please check that LITELLM_BASE_URL "
            "and LITELLM_MASTER_KEY are configured correctly."
        )
    return state


def build_graph():
    graph = StateGraph(BuyBuddyState)
    graph.add_node("extract_preferences", extract_preferences)
    graph.add_node("search_catalog", search_catalog)
    graph.add_node("generate_reply", generate_reply)

    graph.set_entry_point("extract_preferences")
    graph.add_edge("extract_preferences", "search_catalog")
    graph.add_edge("search_catalog", "generate_reply")
    graph.add_edge("generate_reply", END)

    return graph.compile()


# Compiled once at import time and reused across requests.
BUYBUDDY_GRAPH = build_graph()


def build_user_profile(user: dict, history: list, preferences: list) -> Optional[Dict[str, Any]]:
    """Summarizes a registered user's history/preferences for the reply prompt.
    Returns None for guests (or anyone with no on-file history) so the agent
    stays neutral instead of inventing personalization."""
    if not user or user.get("role") != "registered" or not history:
        return None
    recent = [h["product_name"] for h in history[:6]]
    return {
        "name": user.get("name"),
        "persona": user.get("persona"),
        "known_preferences": preferences,
        "recent_purchases": recent,
    }

"""
BuyBuddy's agentic layer — a real, dynamically-branching LangGraph, separate
from the simpler single-path assistant on the main page (agent.py).

Six named agents, each a real graph node, each logging exactly one
structured decision per turn:

  1. classify_intent  — labels the incoming message's intent
  2. route_decision    — picks which specialist handles it (drives an
     actual conditional edge — the graph's execution path genuinely
     changes at runtime, this isn't simulated)
  3. pricing_fetch     — catalog search + live pricing for shopping intents
  4. policy            — answers returns/shipping/warranty questions
  5. handoff           — proposes escalating to a human when warranted
  6. hitl              — a REAL pause: the graph stops and waits for a
     human decision before continuing (LangGraph's interrupt()), not a
     simulated delay

`generate_reply` is a 7th node that writes the actual response, informed
by whichever branch ran.

Every node appends one entry to state["decisions"] — this is what both
the live SSE stream (main.py) and the persisted trace/replay view
(db.py's agent_decisions table) are built from.
"""

import json
import os
from typing import Any, Dict, List, Optional, TypedDict

from openai import OpenAI
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

from catalog import CATALOG

MODEL_NAME = os.environ.get("BUYBUDDY_MODEL", "gpt-4o-mini")

POLICY_FACTS = {
    "returns": "Items can be returned within 30 days of delivery, unworn and in original packaging, for a full refund.",
    "shipping": "Standard shipping takes 3-5 business days. Express (2-day) shipping is available at checkout for an extra fee.",
    "warranty": "All electronics carry a 1-year manufacturer warranty against defects. Apparel and footwear are not covered by warranty.",
}


def _client() -> OpenAI:
    return OpenAI(
        base_url=os.environ.get("LITELLM_BASE_URL", "http://litellm:4000"),
        api_key=os.environ.get("LITELLM_MASTER_KEY", "sk-not-set"),
    )


class AgenticState(TypedDict):
    username: str
    display_name: str
    thread_id: str
    message: str
    history: List[Dict[str, str]]
    classification: Dict[str, Any]
    route: str
    search_results: List[Dict[str, Any]]
    policy_answer: str
    handoff_proposal: Dict[str, Any]
    hitl_decision: Optional[str]
    reply: str
    decisions: List[Dict[str, Any]]


def _log(state: AgenticState, agent: str, decision: str, detail: str) -> None:
    state.setdefault("decisions", []).append({
        "agent": agent,
        "decision": decision,
        "detail": detail,
    })


def _extract_json(raw: str) -> Dict[str, Any]:
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)


# ---------------------------------------------------------------------------
# 1. Classification
# ---------------------------------------------------------------------------

CLASSIFY_PROMPT = """You are an intent classifier for a retail shopping assistant.
Read the customer's message and classify it into EXACTLY ONE of these intents:
- "product_search": looking for, comparing, or asking about products to buy
- "policy_question": asking about returns, shipping, or warranty
- "escalation": frustrated, complaining, asking for a human, or reporting a problem
- "chitchat": greetings, thanks, small talk, anything not covered above

Respond with ONLY a JSON object: {"intent": "...", "confidence": 0.0-1.0, "reasoning": "one short sentence"}"""


def classify_intent(state: AgenticState) -> AgenticState:
    try:
        resp = _client().chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": CLASSIFY_PROMPT},
                {"role": "user", "content": state["message"]},
            ],
            temperature=0,
        )
        result = _extract_json(resp.choices[0].message.content)
        intent = result.get("intent", "chitchat")
        confidence = result.get("confidence", 0.5)
        reasoning = result.get("reasoning", "")
    except Exception as exc:
        intent, confidence, reasoning = "chitchat", 0.0, f"classification failed ({exc.__class__.__name__}), defaulting"

    state["classification"] = {"intent": intent, "confidence": confidence, "reasoning": reasoning}
    _log(state, "classification", intent, f"{reasoning} (confidence {confidence:.2f})")
    return state


# ---------------------------------------------------------------------------
# 2. Routing — a rule-based agent sitting on top of the classifier, exactly
#    the pattern most production routing agents use (LLM classifies, cheap
#    deterministic logic routes — no need to pay for another LLM call just
#    to pick a lane).
# ---------------------------------------------------------------------------

_ROUTE_MAP = {
    "product_search": "pricing_fetch",
    "policy_question": "policy",
    "escalation": "handoff",
    "chitchat": "reply_direct",
}


def route_decision(state: AgenticState) -> AgenticState:
    intent = state.get("classification", {}).get("intent", "chitchat")
    route = _ROUTE_MAP.get(intent, "pricing_fetch")
    state["route"] = route
    _log(state, "routing", route, f"intent '{intent}' routed to '{route}'")
    return state


def _route_edge(state: AgenticState) -> str:
    return state["route"]


# ---------------------------------------------------------------------------
# 3. Pricing Fetch — catalog search + live pricing for shopping intents
# ---------------------------------------------------------------------------

FILTER_PROMPT = """Extract shopping filters from this message as JSON:
{"category": "string or null", "budget_max": number or null, "keywords": ["..."]}
Respond with ONLY the JSON object."""


def pricing_fetch_node(state: AgenticState) -> AgenticState:
    try:
        resp = _client().chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": FILTER_PROMPT},
                {"role": "user", "content": state["message"]},
            ],
            temperature=0,
        )
        filters = _extract_json(resp.choices[0].message.content)
    except Exception:
        filters = {}

    category = (filters.get("category") or "").lower()
    budget_max = filters.get("budget_max")
    keywords = [k.lower() for k in (filters.get("keywords") or [])]

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
        if keywords:
            s += len(set(keywords) & set(t.lower() for t in item["tags"]))
        return s

    ranked = sorted(CATALOG, key=score, reverse=True)
    if category or budget_max or keywords:
        results = [c for c in ranked if score(c) > 0][:5]
    else:
        results = CATALOG[:5]

    state["search_results"] = results
    _log(state, "pricing_fetch", f"{len(results)} candidates", f"filters={filters}, priced from ${min((r['price'] for r in results), default=0)}-${max((r['price'] for r in results), default=0)}")
    return state


# ---------------------------------------------------------------------------
# 4. Policy — small fixed knowledge base, no vector DB needed at this scale
# ---------------------------------------------------------------------------

def policy_node(state: AgenticState) -> AgenticState:
    msg = state["message"].lower()
    if "warrant" in msg:
        topic = "warranty"
    elif "ship" in msg or "deliver" in msg:
        topic = "shipping"
    else:
        topic = "returns"
    answer = POLICY_FACTS[topic]
    state["policy_answer"] = answer
    _log(state, "policy", topic, answer)
    return state


# ---------------------------------------------------------------------------
# 5. Handoff — proposes escalation, does NOT execute it
# ---------------------------------------------------------------------------

def handoff_node(state: AgenticState) -> AgenticState:
    proposal = {
        "action": "escalate_to_human",
        "reason": f"Customer message classified as escalation: \"{state['message'][:120]}\"",
        "customer": state.get("display_name") or state.get("username"),
    }
    state["handoff_proposal"] = proposal
    _log(state, "handoff", "propose_escalation", proposal["reason"])
    return state


# ---------------------------------------------------------------------------
# 6. HITL — a genuine pause. Execution stops here until a human resumes it
#    with an explicit decision (see main.py's /agentic/chat/resume route).
# ---------------------------------------------------------------------------

def hitl_wait(state: AgenticState) -> AgenticState:
    decision = interrupt({
        "proposal": state.get("handoff_proposal", {}),
        "question": "Approve escalating this conversation to a human?",
    })
    state["hitl_decision"] = decision
    _log(state, "hitl", decision, "human reviewer decision on the handoff proposal")
    return state


def _hitl_edge(state: AgenticState) -> str:
    return "approved" if state.get("hitl_decision") == "approved" else "rejected"


def finalize_handoff(state: AgenticState) -> AgenticState:
    _log(state, "handoff", "escalation_confirmed", "Ticket created for human follow-up (demo — not wired to a real queue).")
    return state


# ---------------------------------------------------------------------------
# 7. Reply generation — informed by whichever branch actually ran
# ---------------------------------------------------------------------------

REPLY_PROMPT = """You are BuyBuddy's response writer. You'll be given which internal
agent handled this turn and its output. Write a short, warm reply (2-4 sentences)
appropriate to that outcome:
- pricing_fetch: recommend 1-3 of the search_results by name with prices, ask a follow-up
- policy: answer clearly using the policy_answer given, don't add unstated policy details
- handoff (approved): confirm a human will follow up soon, be reassuring
- handoff (rejected): apologize that you can't escalate right now, offer to keep helping directly
- reply_direct (chitchat): just respond naturally and briefly
Respond with plain text only, no markdown fences."""


def generate_reply(state: AgenticState) -> AgenticState:
    context = {
        "route": state.get("route"),
        "message": state["message"],
        "search_results": state.get("search_results", []),
        "policy_answer": state.get("policy_answer"),
        "handoff_proposal": state.get("handoff_proposal"),
        "hitl_decision": state.get("hitl_decision"),
        "display_name": state.get("display_name"),
    }
    try:
        resp = _client().chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": REPLY_PROMPT},
                {"role": "user", "content": json.dumps(context)},
            ],
            temperature=0.6,
        )
        state["reply"] = resp.choices[0].message.content.strip()
    except Exception as exc:
        state["reply"] = f"Sorry, I hit a snag generating a response ({exc.__class__.__name__})."
    _log(state, "reply", "generated", "Final response composed from the active branch's output.")
    return state


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_agentic_graph():
    g = StateGraph(AgenticState)
    g.add_node("classify_intent", classify_intent)
    g.add_node("route_decision", route_decision)
    g.add_node("pricing_fetch", pricing_fetch_node)
    g.add_node("policy", policy_node)
    g.add_node("handoff", handoff_node)
    g.add_node("hitl_wait", hitl_wait)
    g.add_node("finalize_handoff", finalize_handoff)
    g.add_node("generate_reply", generate_reply)

    g.set_entry_point("classify_intent")
    g.add_edge("classify_intent", "route_decision")
    g.add_conditional_edges("route_decision", _route_edge, {
        "pricing_fetch": "pricing_fetch",
        "policy": "policy",
        "handoff": "handoff",
        "reply_direct": "generate_reply",
    })
    g.add_edge("pricing_fetch", "generate_reply")
    g.add_edge("policy", "generate_reply")
    g.add_edge("handoff", "hitl_wait")
    g.add_conditional_edges("hitl_wait", _hitl_edge, {
        "approved": "finalize_handoff",
        "rejected": "generate_reply",
    })
    g.add_edge("finalize_handoff", "generate_reply")
    g.add_edge("generate_reply", END)

    checkpointer = MemorySaver()
    return g.compile(checkpointer=checkpointer)


AGENTIC_GRAPH = build_agentic_graph()

# Tile layout metadata for the frontend — the graph's actual shape, used to
# lay out /agents' tiles so the lit-up path visually matches real topology.
AGENT_TILES = [
    {"id": "classification", "label": "Classification", "col": 1},
    {"id": "routing", "label": "Routing", "col": 2},
    {"id": "pricing_fetch", "label": "Pricing Fetch", "col": 3, "branch": True},
    {"id": "policy", "label": "Policy", "col": 3, "branch": True},
    {"id": "handoff", "label": "Handoff", "col": 3, "branch": True},
    {"id": "hitl", "label": "Human-in-the-Loop", "col": 4},
    {"id": "reply", "label": "Reply", "col": 5},
]

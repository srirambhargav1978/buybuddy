import json
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Cookie, FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

import auth
import db
from agent import BUYBUDDY_GRAPH, build_user_profile
from agentic_agents import AGENTIC_GRAPH, AGENT_TILES
from catalog import CATALOG, CATALOG_BY_ID, CATEGORIES
from users import get_user, verify_login

LANGFUSE_ENABLED = bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="BuyBuddy", lifespan=lifespan)


def _get_langfuse_handler():
    if not LANGFUSE_ENABLED:
        return None
    try:
        from langfuse.callback import CallbackHandler
        return CallbackHandler()
    except Exception:
        return None


def _session_or_none(buybuddy_session: Optional[str]) -> Optional[dict]:
    session = auth.get_session(buybuddy_session)
    if not session:
        return None
    return {"token": buybuddy_session, **session}


# ---------------------------------------------------------------------------
# Auth pages
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "service": "buybuddy"}


@app.get("/login", response_class=HTMLResponse)
def login_page(buybuddy_session: Optional[str] = Cookie(default=None)):
    if _session_or_none(buybuddy_session):
        return RedirectResponse("/", status_code=303)
    return LOGIN_HTML.replace("__ERROR__", "")


@app.post("/login")
def login_submit(username: str = Form(...), password: str = Form(...)):
    user = verify_login(username.strip(), password)
    if not user:
        html = LOGIN_HTML.replace(
            "__ERROR__",
            '<div class="err">Incorrect username or password. Please try again.</div>',
        )
        return HTMLResponse(html, status_code=401)
    token = auth.create_session(user["username"])
    resp = RedirectResponse("/", status_code=303)
    auth.set_session_cookie(resp, token)
    return resp


@app.get("/logout")
def logout(buybuddy_session: Optional[str] = Cookie(default=None)):
    auth.destroy_session(buybuddy_session)
    resp = RedirectResponse("/login", status_code=303)
    auth.clear_session_cookie(resp)
    return resp


# ---------------------------------------------------------------------------
# App shell
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def app_shell(buybuddy_session: Optional[str] = Cookie(default=None)):
    session = _session_or_none(buybuddy_session)
    if not session:
        return RedirectResponse("/login", status_code=303)

    user = get_user(session["username"])
    preferences = db.get_preferences(user["username"]) if user["role"] == "registered" else []
    history = db.get_purchase_history(user["username"]) if user["role"] == "registered" else []
    cart_count = sum(i["qty"] for i in session.get("cart", []))

    bootstrap = {
        "username": user["username"],
        "name": user["name"],
        "role": user["role"],
        "persona": user.get("persona"),
        "avatar": user.get("avatar"),
        "preferences": preferences,
        "history": history[:15],
        "cartCount": cart_count,
        "categories": CATEGORIES,
    }
    return APP_HTML.replace("__BOOTSTRAP__", json.dumps(bootstrap))


# ---------------------------------------------------------------------------
# Catalog API
# ---------------------------------------------------------------------------

@app.get("/api/catalog")
def api_catalog(category: str = "", q: str = "", buybuddy_session: Optional[str] = Cookie(default=None)):
    auth.require_session(buybuddy_session)
    items = CATALOG
    if category and category != "all":
        items = [i for i in items if i["category"] == category]
    if q:
        ql = q.lower()
        items = [
            i for i in items
            if ql in i["name"].lower() or ql in i["description"].lower() or any(ql in t for t in i["tags"])
        ]
    return {"items": items, "count": len(items)}


@app.get("/api/product/{product_id}")
def api_product(product_id: int, buybuddy_session: Optional[str] = Cookie(default=None)):
    auth.require_session(buybuddy_session)
    item = CATALOG_BY_ID.get(product_id)
    if not item:
        raise HTTPException(404, "Product not found")
    return item


# ---------------------------------------------------------------------------
# Cart API (add/view/remove only — checkout intentionally not implemented)
# ---------------------------------------------------------------------------

class CartAddRequest(BaseModel):
    product_id: int
    qty: int = 1


def _expand_cart(cart_lines):
    expanded = []
    total = 0.0
    for line in cart_lines:
        product = CATALOG_BY_ID.get(line["product_id"])
        if not product:
            continue
        line_total = round(product["price"] * line["qty"], 2)
        total += line_total
        expanded.append({**product, "qty": line["qty"], "line_total": line_total})
    return expanded, round(total, 2)


@app.get("/api/cart")
def api_cart_get(buybuddy_session: Optional[str] = Cookie(default=None)):
    s = auth.require_session(buybuddy_session)
    items, total = _expand_cart(s["cart"])
    return {"items": items, "total": total, "count": sum(i["qty"] for i in s["cart"])}


@app.post("/api/cart")
def api_cart_add(req: CartAddRequest, buybuddy_session: Optional[str] = Cookie(default=None)):
    s = auth.require_session(buybuddy_session)
    if req.product_id not in CATALOG_BY_ID:
        raise HTTPException(404, "Product not found")
    cart = auth.SESSIONS[s["token"]]["cart"]
    for line in cart:
        if line["product_id"] == req.product_id:
            line["qty"] += max(1, req.qty)
            break
    else:
        cart.append({"product_id": req.product_id, "qty": max(1, req.qty)})
    items, total = _expand_cart(cart)
    return {"items": items, "total": total, "count": sum(i["qty"] for i in cart)}


@app.delete("/api/cart/{product_id}")
def api_cart_remove(product_id: int, buybuddy_session: Optional[str] = Cookie(default=None)):
    s = auth.require_session(buybuddy_session)
    cart = auth.SESSIONS[s["token"]]["cart"]
    auth.SESSIONS[s["token"]]["cart"] = [c for c in cart if c["product_id"] != product_id]
    items, total = _expand_cart(auth.SESSIONS[s["token"]]["cart"])
    return {"items": items, "total": total, "count": sum(i["qty"] for i in auth.SESSIONS[s["token"]]["cart"])}


# ---------------------------------------------------------------------------
# Preferences API (registered users only)
# ---------------------------------------------------------------------------

class PrefRequest(BaseModel):
    tag: str


@app.get("/api/preferences")
def api_prefs_get(buybuddy_session: Optional[str] = Cookie(default=None)):
    s = auth.require_session(buybuddy_session)
    user = get_user(s["username"])
    if user["role"] != "registered":
        return {"preferences": []}
    return {"preferences": db.get_preferences(user["username"])}


@app.post("/api/preferences")
def api_prefs_add(req: PrefRequest, buybuddy_session: Optional[str] = Cookie(default=None)):
    s = auth.require_session(buybuddy_session)
    user = get_user(s["username"])
    if user["role"] != "registered":
        raise HTTPException(403, "Guests don't have a saved preference profile")
    db.add_preference(user["username"], req.tag)
    return {"preferences": db.get_preferences(user["username"])}


@app.delete("/api/preferences/{tag}")
def api_prefs_remove(tag: str, buybuddy_session: Optional[str] = Cookie(default=None)):
    s = auth.require_session(buybuddy_session)
    user = get_user(s["username"])
    if user["role"] != "registered":
        raise HTTPException(403, "Guests don't have a saved preference profile")
    db.remove_preference(user["username"], tag)
    return {"preferences": db.get_preferences(user["username"])}


# ---------------------------------------------------------------------------
# Try On (stylized mockup — registered users only, eyewear/shirts/outerwear)
# ---------------------------------------------------------------------------

@app.get("/api/tryon/{product_id}")
def api_tryon(product_id: int, buybuddy_session: Optional[str] = Cookie(default=None)):
    s = auth.require_session(buybuddy_session)
    user = get_user(s["username"])
    product = CATALOG_BY_ID.get(product_id)
    if not product:
        raise HTTPException(404, "Product not found")
    if not product["try_on"]:
        raise HTTPException(400, "This item isn't eligible for Try On")
    if user["role"] != "registered" or not user.get("avatar"):
        raise HTTPException(403, "Try On is available for registered accounts")
    return {
        "avatar": user["avatar"],
        "name": user["name"],
        "product": {"id": product["id"], "name": product["name"], "icon": product["icon"], "category": product["category"]},
    }


# ---------------------------------------------------------------------------
# Chat (session-aware, personalized for registered users)
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str


@app.post("/chat")
def chat(req: ChatRequest, buybuddy_session: Optional[str] = Cookie(default=None)):
    s = auth.require_session(buybuddy_session)
    token = s["token"]
    session = auth.SESSIONS[token]
    user = get_user(session["username"])

    # First message of the session for a registered user: seed the agent's
    # working preferences from their on-file tags, so the very first reply
    # is already personalized instead of starting cold.
    if not session["chat_seeded"] and user["role"] == "registered":
        tags = db.get_preferences(user["username"])
        if tags:
            session["chat_preferences"] = {"style": tags}
        session["chat_seeded"] = True

    history_rows = db.get_purchase_history(user["username"]) if user["role"] == "registered" else []
    user_profile = build_user_profile(user, history_rows, db.get_preferences(user["username"]) if user["role"] == "registered" else [])

    state = {
        "session_id": token,
        "message": req.message,
        "history": session["chat_history"],
        "preferences": session["chat_preferences"],
        "candidates": [],
        "reply": "",
        "user_profile": user_profile,
    }

    config = {}
    handler = _get_langfuse_handler()
    if handler:
        config["callbacks"] = [handler]

    result = BUYBUDDY_GRAPH.invoke(state, config=config)

    session["chat_history"].append({"role": "user", "content": req.message})
    session["chat_history"].append({"role": "assistant", "content": result["reply"]})
    session["chat_preferences"] = result["preferences"]

    return {
        "reply": result["reply"],
        "preferences": result["preferences"],
        "candidates": result["candidates"],
    }


# ---------------------------------------------------------------------------
# Agentic layer — a separate, dynamically-branching LangGraph (see
# agentic_agents.py) with 6 named agents, streamed live over SSE so the
# /agents page can highlight each one the instant it makes a decision, plus
# a real human-in-the-loop pause on the escalation path.
# ---------------------------------------------------------------------------

@app.get("/agents", response_class=HTMLResponse)
def agents_page(buybuddy_session: Optional[str] = Cookie(default=None)):
    session = _session_or_none(buybuddy_session)
    if not session:
        return RedirectResponse("/login", status_code=303)
    user = get_user(session["username"])
    bootstrap = {
        "username": user["username"],
        "name": user["name"],
        "role": user["role"],
        "tiles": AGENT_TILES,
    }
    return AGENTS_HTML.replace("__BOOTSTRAP__", json.dumps(bootstrap))


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _stream_agentic(run_input, config, username: str, thread_id: str, turn_index: int, session: dict):
    def gen():
        for chunk in AGENTIC_GRAPH.stream(run_input, config, stream_mode="updates"):
            if "__interrupt__" in chunk:
                payload = chunk["__interrupt__"][0].value
                yield _sse({"type": "hitl_pending", "payload": payload})
            else:
                node_name = list(chunk.keys())[0]
                node_state = chunk[node_name]
                decisions = node_state.get("decisions") or []
                last = decisions[-1] if decisions else None
                if last:
                    yield _sse({"type": "agent_done", "node": node_name, "decision": last})

        # Only now — after the generator has actually been driven to
        # completion or a pause — does the graph's real state reflect what
        # just happened. Checking this any earlier (e.g. right after
        # constructing the StreamingResponse) would see stale/pre-run state,
        # since generators don't execute until iterated.
        snapshot = AGENTIC_GRAPH.get_state(config)
        if not snapshot.next:
            session["agentic_pending"] = False
            values = snapshot.values
            db.log_agent_decisions(username, thread_id, turn_index, values.get("message", ""), values.get("decisions", []))
            yield _sse({"type": "final", "reply": values.get("reply", "")})
        else:
            session["agentic_pending"] = True
            yield _sse({"type": "paused"})

    return StreamingResponse(gen(), media_type="text/event-stream")


class AgenticChatRequest(BaseModel):
    message: str


@app.post("/agentic/chat/stream")
def agentic_chat_stream(req: AgenticChatRequest, buybuddy_session: Optional[str] = Cookie(default=None)):
    s = auth.require_session(buybuddy_session)
    session = auth.SESSIONS[s["token"]]
    user = get_user(session["username"])

    turn_index = session["agentic_turn"] + 1
    session["agentic_turn"] = turn_index

    thread_id = session["agentic_thread_id"]
    config = {"configurable": {"thread_id": thread_id}}
    handler = _get_langfuse_handler()
    if handler:
        config["callbacks"] = [handler]
    state = {
        "username": user["username"],
        "display_name": user["name"],
        "thread_id": thread_id,
        "message": req.message,
        "history": [],
        "decisions": [],
    }
    return _stream_agentic(state, config, user["username"], thread_id, turn_index, session)


class AgenticResumeRequest(BaseModel):
    decision: str  # "approved" or "rejected"


@app.post("/agentic/chat/resume")
def agentic_chat_resume(req: AgenticResumeRequest, buybuddy_session: Optional[str] = Cookie(default=None)):
    from langgraph.types import Command
    s = auth.require_session(buybuddy_session)
    session = auth.SESSIONS[s["token"]]
    user = get_user(session["username"])

    if not session.get("agentic_pending"):
        raise HTTPException(400, "No pending human-in-the-loop decision for this session")

    thread_id = session["agentic_thread_id"]
    config = {"configurable": {"thread_id": thread_id}}
    handler = _get_langfuse_handler()
    if handler:
        config["callbacks"] = [handler]
    turn_index = session["agentic_turn"]

    decision = "approved" if req.decision == "approved" else "rejected"
    return _stream_agentic(Command(resume=decision), config, user["username"], thread_id, turn_index, session)


@app.get("/agentic/history")
def agentic_history(buybuddy_session: Optional[str] = Cookie(default=None)):
    s = auth.require_session(buybuddy_session)
    session = auth.SESSIONS[s["token"]]
    turns = db.get_agentic_turns(session["username"], session["agentic_thread_id"])
    return {"turns": turns}


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

LOGIN_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BuyBuddy — Sign In</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Inter',sans-serif;background:radial-gradient(circle at 20% 20%,#0D1B35,#060B14 60%);color:#E2EAF4;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
  .card{width:100%;max-width:400px;background:#0B1526;border:1px solid #1A2E4A;border-radius:18px;padding:40px 32px;box-shadow:0 20px 60px rgba(0,0,0,.4)}
  .mark{width:44px;height:44px;background:linear-gradient(135deg,#00D9B5,#4F8EF7);border-radius:11px;display:flex;align-items:center;justify-content:center;font-weight:800;color:#060B14;font-size:17px;margin-bottom:18px}
  h1{font-size:22px;font-weight:800;margin-bottom:4px}
  .tag{color:#00D9B5;font-size:12.5px;font-weight:600;letter-spacing:.03em;margin-bottom:22px}
  label{display:block;font-size:12.5px;color:#8FA3C0;margin:14px 0 6px;font-weight:600}
  input{width:100%;background:#0D1B35;border:1px solid #1A2E4A;border-radius:9px;padding:12px 14px;color:#fff;font-size:14px;font-family:inherit}
  input:focus{outline:none;border-color:#00D9B5}
  button{width:100%;margin-top:22px;background:linear-gradient(135deg,#00D9B5,#4F8EF7);color:#060B14;border:none;border-radius:9px;padding:13px;font-weight:800;font-size:14.5px;cursor:pointer}
  .err{background:#3A1420;border:1px solid #7A2436;color:#FF9DAE;font-size:12.5px;padding:10px 12px;border-radius:8px;margin-bottom:6px}
  .hint{margin-top:22px;border-top:1px solid #1A2E4A;padding-top:16px;font-size:11.5px;color:#5E7391;line-height:1.7}
  .hint b{color:#8FA3C0}
</style>
</head>
<body>
<div class="card">
  <div class="mark">SB</div>
  <h1>Welcome to BuyBuddy</h1>
  <div class="tag">PERSONALISED AI SHOPPING ASSISTANT</div>
  __ERROR__
  <form method="post" action="/login">
    <label>Username</label>
    <input name="username" autocomplete="username" required autofocus>
    <label>Password</label>
    <input name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Sign In</button>
  </form>
  <div class="hint">
    <b>Registered:</b> user1 / user2 / user3 &middot; password <b>user@12345</b><br>
    <b>Guest:</b> guest1 / guest2 / guest3 &middot; password <b>guest@12345</b>
  </div>
</div>
</body>
</html>"""


APP_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BuyBuddy — AI Shopping Assistant</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Inter',sans-serif;background:#060B14;color:#E2EAF4;min-height:100vh}
  a{color:inherit}

  header{padding:16px 4%;border-bottom:1px solid #1A2E4A;display:flex;align-items:center;gap:14px;position:sticky;top:0;background:#060B14;z-index:20}
  .mark{width:34px;height:34px;background:linear-gradient(135deg,#00D9B5,#4F8EF7);border-radius:8px;display:flex;align-items:center;justify-content:center;font-weight:800;color:#060B14;font-size:13px;flex-shrink:0}
  .brand{display:flex;flex-direction:column;line-height:1.25}
  .brand b{font-size:15.5px}
  .brand small{color:#00D9B5;font-size:10.5px;font-weight:700;letter-spacing:.04em}
  .spacer{flex:1}
  .cart-btn{position:relative;background:#0D1B35;border:1px solid #1A2E4A;color:#E2EAF4;border-radius:9px;padding:9px 14px;font-size:13px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px}
  .cart-badge{background:#00D9B5;color:#060B14;font-size:10.5px;font-weight:800;border-radius:20px;padding:1px 6px;min-width:16px;text-align:center}
  .user-chip{display:flex;align-items:center;gap:8px;background:#0D1B35;border:1px solid #1A2E4A;border-radius:9px;padding:6px 12px 6px 6px}
  .user-avatar{width:26px;height:26px;border-radius:50%;background:linear-gradient(135deg,#4F8EF7,#00D9B5);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:800;color:#060B14}
  .user-meta{display:flex;flex-direction:column;line-height:1.2}
  .user-meta b{font-size:12.5px}
  .user-meta small{font-size:10px;color:#8FA3C0;text-transform:capitalize}
  .logout{background:none;border:1px solid #1A2E4A;color:#8FA3C0;border-radius:9px;padding:8px 12px;font-size:12.5px;font-weight:600;cursor:pointer}
  .logout:hover{color:#FF9DAE;border-color:#7A2436}

  .hero{padding:26px 4% 20px;background:linear-gradient(135deg,rgba(0,217,181,.08),rgba(79,142,247,.05));border-bottom:1px solid #1A2E4A}
  .hero h1{font-size:26px;font-weight:800;margin-bottom:6px}
  .hero .accent{background:linear-gradient(135deg,#00D9B5,#4F8EF7);-webkit-background-clip:text;background-clip:text;color:transparent}
  .hero p{color:#9AB0CC;font-size:13.5px;max-width:720px;line-height:1.6}
  .hero p b{color:#E2EAF4}

  .layout{display:grid;grid-template-columns:1.6fr 1fr;gap:0;min-height:calc(100vh - 190px)}
  @media (max-width:920px){.layout{grid-template-columns:1fr}}

  .catalog-col{padding:22px 4%;border-right:1px solid #1A2E4A}
  @media (max-width:920px){.catalog-col{border-right:none;border-bottom:1px solid #1A2E4A}}
  .toolbar{display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap}
  #search{flex:1;min-width:180px;background:#0D1B35;border:1px solid #1A2E4A;border-radius:9px;padding:11px 14px;color:#fff;font-size:13.5px}
  #search:focus{outline:none;border-color:#00D9B5}
  .chips{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:18px}
  .chip{background:#0D1B35;border:1px solid #1A2E4A;color:#9AB0CC;border-radius:20px;padding:6px 13px;font-size:12px;font-weight:600;cursor:pointer;text-transform:capitalize;white-space:nowrap}
  .chip.active{background:#00D9B5;color:#060B14;border-color:#00D9B5}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:14px}
  .p-card{background:#0D1B35;border:1px solid #1A2E4A;border-radius:14px;padding:16px;cursor:pointer;transition:border-color .15s,transform .15s}
  .p-card:hover{border-color:#00D9B5;transform:translateY(-2px)}
  .p-icon{width:48px;height:48px;border-radius:12px;background:linear-gradient(135deg,rgba(0,217,181,.18),rgba(79,142,247,.14));display:flex;align-items:center;justify-content:center;font-size:22px;margin-bottom:10px}
  .p-cat{font-size:9.5px;color:#00D9B5;font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:5px}
  .p-name{font-size:13px;font-weight:700;line-height:1.35;margin-bottom:8px;min-height:35px}
  .p-price{font-size:14px;font-weight:800;color:#00D9B5}
  .p-tryon{display:inline-block;margin-top:8px;font-size:10px;background:rgba(79,142,247,.15);color:#8AB4FF;padding:3px 8px;border-radius:20px;font-weight:700}
  .empty{color:#5E7391;font-size:13px;padding:40px 0;text-align:center}

  .side-col{display:flex;flex-direction:column;height:calc(100vh - 190px);position:sticky;top:66px}
  .prefs-card{padding:18px 5%;border-bottom:1px solid #1A2E4A}
  .prefs-card h3{font-size:13px;margin-bottom:4px}
  .prefs-card .sub{color:#5E7391;font-size:11.5px;margin-bottom:12px;line-height:1.5}
  .pref-tags{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
  .pref-tag{background:rgba(0,217,181,.12);border:1px solid rgba(0,217,181,.35);color:#5FEBD1;font-size:11px;font-weight:600;padding:5px 6px 5px 10px;border-radius:20px;display:flex;align-items:center;gap:6px}
  .pref-tag button{background:none;border:none;color:#5FEBD1;cursor:pointer;font-size:13px;line-height:1;opacity:.7}
  .pref-tag button:hover{opacity:1}
  .pref-add{display:flex;gap:6px}
  .pref-add input{flex:1;background:#0D1B35;border:1px solid #1A2E4A;border-radius:7px;padding:8px 10px;color:#fff;font-size:12px}
  .pref-add button{background:#1A2E4A;color:#E2EAF4;border:none;border-radius:7px;padding:0 12px;font-size:12px;font-weight:700;cursor:pointer}

  .chat-col{flex:1;display:flex;flex-direction:column;min-height:0}
  .chat-head{padding:14px 5% 10px;font-size:12.5px;font-weight:700;color:#8FA3C0}
  #log{flex:1;overflow-y:auto;padding:6px 5% 16px;display:flex;flex-direction:column;gap:12px}
  .msg{max-width:88%;padding:10px 13px;border-radius:12px;font-size:13px;line-height:1.55}
  .user{align-self:flex-end;background:#00D9B5;color:#060B14;font-weight:500}
  .bot{align-self:flex-start;background:#0D1B35;border:1px solid #1A2E4A}
  .candidates{align-self:flex-start;display:flex;flex-wrap:wrap;gap:7px;max-width:100%}
  .mini-card{background:#0D1B35;border:1px solid #1A2E4A;border-radius:9px;padding:8px 10px;font-size:11px;width:128px;cursor:pointer}
  .mini-card:hover{border-color:#00D9B5}
  .mini-card b{display:block;color:#fff;font-size:11px;margin-bottom:3px}
  .mini-card .price{color:#00D9B5;font-weight:700;margin-top:4px}
  form.chat-form{border-top:1px solid #1A2E4A;padding:12px 5%;display:flex;gap:8px}
  form.chat-form input{flex:1;background:#0D1B35;border:1px solid #1A2E4A;border-radius:8px;padding:11px 13px;color:#fff;font-size:13px}
  form.chat-form input:focus{outline:none;border-color:#00D9B5}
  form.chat-form button{background:#00D9B5;color:#060B14;border:none;border-radius:8px;padding:0 18px;font-weight:700;cursor:pointer;font-size:13px}
  form.chat-form button:disabled{opacity:.5}

  .overlay{position:fixed;inset:0;background:rgba(3,7,15,.72);display:none;align-items:center;justify-content:center;z-index:50;padding:20px}
  .overlay.show{display:flex}
  .modal{background:#0B1526;border:1px solid #1A2E4A;border-radius:16px;max-width:420px;width:100%;padding:26px;max-height:88vh;overflow-y:auto}
  .modal-close{float:right;background:none;border:none;color:#8FA3C0;font-size:18px;cursor:pointer}
  .modal .p-icon{width:70px;height:70px;font-size:32px;border-radius:16px;margin-bottom:14px}
  .modal h2{font-size:18px;margin-bottom:6px}
  .modal .price{font-size:20px;font-weight:800;color:#00D9B5;margin:8px 0}
  .modal .desc{color:#9AB0CC;font-size:13px;line-height:1.6;margin-bottom:14px}
  .tag-row{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:18px}
  .tag-pill{background:#0D1B35;border:1px solid #1A2E4A;color:#8FA3C0;font-size:10.5px;padding:4px 9px;border-radius:20px}
  .btn-row{display:flex;gap:10px}
  .btn-primary{flex:1;background:#00D9B5;color:#060B14;border:none;border-radius:9px;padding:12px;font-weight:800;font-size:13px;cursor:pointer}
  .btn-secondary{flex:1;background:#0D1B35;border:1px solid #4F8EF7;color:#8AB4FF;border-radius:9px;padding:12px;font-weight:800;font-size:13px;cursor:pointer}
  .btn-secondary:disabled{opacity:.4;cursor:default;border-color:#1A2E4A;color:#5E7391}

  .drawer-overlay{position:fixed;inset:0;background:rgba(3,7,15,.6);display:none;z-index:50}
  .drawer-overlay.show{display:block}
  .drawer{position:fixed;top:0;right:0;bottom:0;width:340px;max-width:90vw;background:#0B1526;border-left:1px solid #1A2E4A;z-index:51;display:flex;flex-direction:column;transform:translateX(100%);transition:transform .2s}
  .drawer.show{transform:translateX(0)}
  .drawer-head{padding:18px 20px;border-bottom:1px solid #1A2E4A;display:flex;align-items:center}
  .drawer-head h3{font-size:15px;flex:1}
  .drawer-items{flex:1;overflow-y:auto;padding:14px 20px}
  .cart-line{display:flex;gap:10px;padding:12px 0;border-bottom:1px solid #14213A}
  .cart-line .p-icon{width:36px;height:36px;font-size:16px;margin:0}
  .cart-line-info{flex:1}
  .cart-line-info b{font-size:12.5px;display:block}
  .cart-line-info small{color:#5E7391;font-size:11px}
  .cart-line button{background:none;border:none;color:#5E7391;font-size:12px;cursor:pointer}
  .drawer-foot{padding:18px 20px;border-top:1px solid #1A2E4A}
  .drawer-foot .total{display:flex;justify-content:space-between;font-size:14px;font-weight:800;margin-bottom:12px}
  .checkout-note{font-size:11px;color:#5E7391;text-align:center;margin-top:8px}

  .tryon-avatar-wrap{position:relative;width:180px;height:260px;margin:0 auto 16px}
  .tryon-caption{text-align:center;font-size:12.5px;color:#9AB0CC;line-height:1.5}
  .tryon-caption b{color:#5FEBD1}
</style>
</head>
<body>

<header>
  <div class="mark">SB</div>
  <div class="brand"><b>BuyBuddy</b><small>PERSONALISED AI ASSISTANT</small></div>
  <div class="spacer"></div>
  <a class="cart-btn" href="/agents" style="text-decoration:none">🤖 Agent Activity</a>
  <button class="cart-btn" id="cart-btn">🛒 Cart <span class="cart-badge" id="cart-badge">0</span></button>
  <div class="user-chip">
    <div class="user-avatar" id="user-initial">?</div>
    <div class="user-meta"><b id="user-name">—</b><small id="user-role">—</small></div>
  </div>
  <a class="logout" href="/logout">Logout</a>
</header>

<div class="hero">
  <h1>Your <span class="accent">Personalised AI Assistant</span> for shopping</h1>
  <p id="hero-sub"><b>BuyBuddy</b> uses personalization techniques — your saved preferences and, for registered shoppers, real purchase history — to recommend items you're actually likely to want. Browse the catalogue, or just tell the assistant what you're after.</p>
</div>

<div class="layout">
  <div class="catalog-col">
    <div class="toolbar">
      <input id="search" placeholder="Search the catalogue...">
    </div>
    <div class="chips" id="chips"></div>
    <div class="grid" id="grid"></div>
  </div>

  <div class="side-col">
    <div class="prefs-card" id="prefs-card">
      <h3>Your Preferences</h3>
      <div class="sub" id="prefs-sub">Loading...</div>
      <div class="pref-tags" id="pref-tags"></div>
      <div class="pref-add" id="pref-add-wrap">
        <input id="pref-input" placeholder="Add a preference...">
        <button id="pref-add-btn">Add</button>
      </div>
    </div>
    <div class="chat-col">
      <div class="chat-head">Chat with BuyBuddy</div>
      <div id="log"></div>
      <form class="chat-form" id="chat-form">
        <input id="chat-input" placeholder="Ask for a recommendation..." autocomplete="off">
        <button type="submit" id="chat-send">Send</button>
      </form>
    </div>
  </div>
</div>

<!-- Product detail modal -->
<div class="overlay" id="product-overlay">
  <div class="modal">
    <button class="modal-close" id="product-close">✕</button>
    <div class="p-icon" id="pm-icon"></div>
    <div class="p-cat" id="pm-cat"></div>
    <h2 id="pm-name"></h2>
    <div class="price" id="pm-price"></div>
    <div class="desc" id="pm-desc"></div>
    <div class="tag-row" id="pm-tags"></div>
    <div class="btn-row">
      <button class="btn-primary" id="pm-add">Add to Cart</button>
      <button class="btn-secondary" id="pm-tryon">Try On</button>
    </div>
  </div>
</div>

<!-- Try-on modal -->
<div class="overlay" id="tryon-overlay">
  <div class="modal" style="max-width:340px;text-align:center">
    <button class="modal-close" id="tryon-close">✕</button>
    <div class="tryon-avatar-wrap" id="tryon-avatar"></div>
    <div class="tryon-caption" id="tryon-caption"></div>
  </div>
</div>

<!-- Cart drawer -->
<div class="drawer-overlay" id="drawer-overlay"></div>
<div class="drawer" id="cart-drawer">
  <div class="drawer-head"><h3>Your Cart</h3><button class="modal-close" id="drawer-close">✕</button></div>
  <div class="drawer-items" id="drawer-items"></div>
  <div class="drawer-foot">
    <div class="total"><span>Total</span><span id="drawer-total">$0</span></div>
    <button class="btn-secondary" style="width:100%" disabled>Checkout — Coming Soon</button>
    <div class="checkout-note">This demo showcases browsing + AI recommendations. Checkout isn't wired up.</div>
  </div>
</div>

<script>
const BB = __BOOTSTRAP__;

// ---- header / identity ----
document.getElementById('user-initial').textContent = BB.name.split(' ').map(w=>w[0]).join('').slice(0,2).toUpperCase();
document.getElementById('user-name').textContent = BB.name;
document.getElementById('user-role').textContent = BB.role + (BB.persona ? ' · ' + BB.persona : '');
document.getElementById('cart-badge').textContent = BB.cartCount;

if (BB.role !== 'registered') {
  document.getElementById('prefs-sub').textContent = "You're browsing as a guest — no saved history yet. Chat with BuyBuddy and it'll start learning what you like, just for this session.";
  document.getElementById('pref-add-wrap').style.display = 'none';
} else {
  document.getElementById('prefs-sub').textContent = 'Built from your order history. Edit anytime — BuyBuddy uses these to personalize recommendations.';
}

// ---- category chips ----
const chipsEl = document.getElementById('chips');
let activeCategory = 'all';
function renderChips() {
  chipsEl.innerHTML = '';
  ['all', ...BB.categories].forEach(c => {
    const el = document.createElement('div');
    el.className = 'chip' + (c === activeCategory ? ' active' : '');
    el.textContent = c === 'all' ? 'All' : c.replace('-', ' ');
    el.onclick = () => { activeCategory = c; renderChips(); loadCatalog(); };
    chipsEl.appendChild(el);
  });
}
renderChips();

// ---- catalog ----
const grid = document.getElementById('grid');
let CATALOG_CACHE = {};

async function loadCatalog() {
  const q = document.getElementById('search').value.trim();
  const params = new URLSearchParams();
  if (activeCategory !== 'all') params.set('category', activeCategory);
  if (q) params.set('q', q);
  const res = await fetch('/api/catalog?' + params.toString());
  const data = await res.json();
  grid.innerHTML = '';
  if (!data.items.length) {
    grid.innerHTML = '<div class="empty">No items match. Try a different search or category.</div>';
    return;
  }
  data.items.forEach(p => {
    CATALOG_CACHE[p.id] = p;
    const c = document.createElement('div');
    c.className = 'p-card';
    c.onclick = () => openProduct(p.id);
    c.innerHTML = `
      <div class="p-icon">${p.icon}</div>
      <div class="p-cat">${p.category.replace('-', ' ')}</div>
      <div class="p-name">${p.name}</div>
      <div class="p-price">$${p.price}</div>
      ${p.try_on ? '<div class="p-tryon">✨ Try On available</div>' : ''}
    `;
    grid.appendChild(c);
  });
}
document.getElementById('search').addEventListener('input', () => {
  clearTimeout(window.__searchT);
  window.__searchT = setTimeout(loadCatalog, 250);
});
loadCatalog();

// ---- product modal ----
let currentProductId = null;
function openProduct(id) {
  const p = CATALOG_CACHE[id];
  if (!p) return;
  currentProductId = id;
  document.getElementById('pm-icon').textContent = p.icon;
  document.getElementById('pm-cat').textContent = p.category.replace('-', ' ');
  document.getElementById('pm-name').textContent = p.name;
  document.getElementById('pm-price').textContent = '$' + p.price;
  document.getElementById('pm-desc').textContent = p.description;
  document.getElementById('pm-tags').innerHTML = p.tags.map(t => `<span class="tag-pill">${t}</span>`).join('');
  const tryonBtn = document.getElementById('pm-tryon');
  if (p.try_on && BB.role === 'registered') {
    tryonBtn.disabled = false;
    tryonBtn.textContent = 'Try On';
  } else if (p.try_on) {
    tryonBtn.disabled = true;
    tryonBtn.textContent = 'Try On (registered only)';
  } else {
    tryonBtn.disabled = true;
    tryonBtn.textContent = 'Try On (n/a)';
  }
  document.getElementById('product-overlay').classList.add('show');
}
document.getElementById('product-close').onclick = () => document.getElementById('product-overlay').classList.remove('show');
document.getElementById('product-overlay').addEventListener('click', e => { if (e.target.id === 'product-overlay') e.currentTarget.classList.remove('show'); });

document.getElementById('pm-add').onclick = async () => {
  await addToCart(currentProductId);
  document.getElementById('product-overlay').classList.remove('show');
};

// ---- avatars for try-on (simple flat SVG illustrations) ----
const AVATARS = {
  female_a: `<svg viewBox="0 0 180 260" xmlns="http://www.w3.org/2000/svg">
    <ellipse cx="90" cy="230" rx="58" ry="26" fill="#0D1B35"/>
    <path d="M35 260 Q40 150 90 150 Q140 150 145 260 Z" fill="#134E4A"/>
    <circle cx="90" cy="95" r="46" fill="#F2C9A0"/>
    <path d="M44 95 Q40 30 90 30 Q140 30 136 95 Q136 60 90 60 Q44 60 44 95Z" fill="#3A2618"/>
    <circle cx="72" cy="98" r="4" fill="#2A2A2A"/><circle cx="108" cy="98" r="4" fill="#2A2A2A"/>
    <path d="M75 118 Q90 128 105 118" stroke="#8A5A3A" stroke-width="3" fill="none" stroke-linecap="round"/>
  </svg>`,
  female_b: `<svg viewBox="0 0 180 260" xmlns="http://www.w3.org/2000/svg">
    <ellipse cx="90" cy="230" rx="58" ry="26" fill="#0D1B35"/>
    <path d="M35 260 Q40 150 90 150 Q140 150 145 260 Z" fill="#5B3A29"/>
    <circle cx="90" cy="95" r="46" fill="#D99A6C"/>
    <path d="M40 105 Q30 40 90 28 Q150 40 140 105 Q145 130 130 140 Q140 90 90 85 Q40 90 50 140 Q35 130 40 105Z" fill="#1A1210"/>
    <circle cx="72" cy="98" r="4" fill="#2A2A2A"/><circle cx="108" cy="98" r="4" fill="#2A2A2A"/>
    <path d="M75 118 Q90 128 105 118" stroke="#7A4325" stroke-width="3" fill="none" stroke-linecap="round"/>
  </svg>`,
  male_a: `<svg viewBox="0 0 180 260" xmlns="http://www.w3.org/2000/svg">
    <ellipse cx="90" cy="230" rx="58" ry="26" fill="#0D1B35"/>
    <path d="M32 260 Q38 145 90 145 Q142 145 148 260 Z" fill="#1E3A5F"/>
    <circle cx="90" cy="95" r="46" fill="#C88A5C"/>
    <path d="M46 80 Q50 32 90 32 Q130 32 134 80 Q130 65 90 65 Q50 65 46 80Z" fill="#20140C"/>
    <circle cx="72" cy="98" r="4" fill="#2A2A2A"/><circle cx="108" cy="98" r="4" fill="#2A2A2A"/>
    <path d="M75 120 Q90 126 105 120" stroke="#7A4A2A" stroke-width="3" fill="none" stroke-linecap="round"/>
  </svg>`
};

// ---- try-on modal ----
document.getElementById('pm-tryon').onclick = async () => {
  if (document.getElementById('pm-tryon').disabled) return;
  const res = await fetch('/api/tryon/' + currentProductId);
  if (!res.ok) return;
  const data = await res.json();
  const wrap = document.getElementById('tryon-avatar');
  wrap.innerHTML = AVATARS[data.avatar] || '';
  const overlay = document.createElement('div');
  const eyewearCats = ['eyewear'];
  const isEyewear = data.product.category === 'eyewear';
  overlay.style.position = 'absolute';
  overlay.style.left = '50%';
  overlay.style.transform = 'translateX(-50%)';
  overlay.style.fontSize = isEyewear ? '30px' : '40px';
  overlay.style.top = isEyewear ? '78px' : '150px';
  overlay.textContent = data.product.icon;
  wrap.appendChild(overlay);
  document.getElementById('tryon-caption').innerHTML = `<b>${data.name}</b> trying on<br>${data.product.name}`;
  document.getElementById('product-overlay').classList.remove('show');
  document.getElementById('tryon-overlay').classList.add('show');
};
document.getElementById('tryon-close').onclick = () => document.getElementById('tryon-overlay').classList.remove('show');
document.getElementById('tryon-overlay').addEventListener('click', e => { if (e.target.id === 'tryon-overlay') e.currentTarget.classList.remove('show'); });

// ---- cart ----
async function addToCart(id) {
  const res = await fetch('/api/cart', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({product_id:id, qty:1})});
  const data = await res.json();
  document.getElementById('cart-badge').textContent = data.count;
}
async function loadCart() {
  const res = await fetch('/api/cart');
  const data = await res.json();
  document.getElementById('cart-badge').textContent = data.count;
  document.getElementById('drawer-total').textContent = '$' + data.total.toFixed(2);
  const wrap = document.getElementById('drawer-items');
  wrap.innerHTML = '';
  if (!data.items.length) {
    wrap.innerHTML = '<div class="empty">Your cart is empty.</div>';
    return;
  }
  data.items.forEach(i => {
    const el = document.createElement('div');
    el.className = 'cart-line';
    el.innerHTML = `<div class="p-icon">${i.icon}</div><div class="cart-line-info"><b>${i.name}</b><small>Qty ${i.qty} · $${i.line_total.toFixed(2)}</small></div><button>Remove</button>`;
    el.querySelector('button').onclick = async () => { await fetch('/api/cart/' + i.id, {method:'DELETE'}); loadCart(); };
    wrap.appendChild(el);
  });
}
document.getElementById('cart-btn').onclick = () => { loadCart(); document.getElementById('drawer-overlay').classList.add('show'); document.getElementById('cart-drawer').classList.add('show'); };
document.getElementById('drawer-close').onclick = closeDrawer;
document.getElementById('drawer-overlay').onclick = closeDrawer;
function closeDrawer() { document.getElementById('drawer-overlay').classList.remove('show'); document.getElementById('cart-drawer').classList.remove('show'); }

// ---- preferences ----
async function loadPrefs() {
  if (BB.role !== 'registered') return;
  const res = await fetch('/api/preferences');
  const data = await res.json();
  const wrap = document.getElementById('pref-tags');
  wrap.innerHTML = '';
  data.preferences.forEach(tag => {
    const el = document.createElement('div');
    el.className = 'pref-tag';
    el.innerHTML = `${tag} <button>✕</button>`;
    el.querySelector('button').onclick = async () => { await fetch('/api/preferences/' + encodeURIComponent(tag), {method:'DELETE'}); loadPrefs(); };
    wrap.appendChild(el);
  });
}
document.getElementById('pref-add-btn').onclick = async () => {
  const input = document.getElementById('pref-input');
  const tag = input.value.trim();
  if (!tag) return;
  await fetch('/api/preferences', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({tag})});
  input.value = '';
  loadPrefs();
};
loadPrefs();

// ---- chat ----
const log = document.getElementById('log');
const chatForm = document.getElementById('chat-form');
const chatInput = document.getElementById('chat-input');
const chatSend = document.getElementById('chat-send');

function addMsg(text, cls) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}
function addCandidates(items) {
  if (!items || !items.length) return;
  const wrap = document.createElement('div');
  wrap.className = 'candidates';
  items.forEach(p => {
    const c = document.createElement('div');
    c.className = 'mini-card';
    c.innerHTML = `<b>${p.name}</b><div class="price">$${p.price}</div>`;
    c.onclick = () => { CATALOG_CACHE[p.id] = p; openProduct(p.id); };
    wrap.appendChild(c);
  });
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
}

const greeting = BB.role === 'registered'
  ? `Hey ${BB.name.split(' ')[0]}! Good to see you back. Tell me what you're after, or I can suggest something based on what you usually like.`
  : `Hi, I'm BuyBuddy. Tell me what you're shopping for — category, budget, anything you have in mind — and I'll find a few good options.`;
addMsg(greeting, 'bot');

chatForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;
  addMsg(message, 'user');
  chatInput.value = '';
  chatSend.disabled = true;
  try {
    const res = await fetch('/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message})});
    if (res.status === 401) { window.location.href = '/login'; return; }
    const data = await res.json();
    addMsg(data.reply, 'bot');
    addCandidates(data.candidates);
  } catch (err) {
    addMsg('Something went wrong reaching BuyBuddy. Please try again.', 'bot');
  } finally {
    chatSend.disabled = false;
    chatInput.focus();
  }
});
</script>
</body>
</html>"""


AGENTS_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BuyBuddy — Agent Activity</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Inter',sans-serif;background:#060B14;color:#E2EAF4;min-height:100vh}
  a{color:inherit}

  header{padding:16px 4%;border-bottom:1px solid #1A2E4A;display:flex;align-items:center;gap:14px;position:sticky;top:0;background:#060B14;z-index:20}
  .mark{width:34px;height:34px;background:linear-gradient(135deg,#00D9B5,#4F8EF7);border-radius:8px;display:flex;align-items:center;justify-content:center;font-weight:800;color:#060B14;font-size:13px;flex-shrink:0}
  .brand{display:flex;flex-direction:column;line-height:1.25}
  .brand b{font-size:15.5px}
  .brand small{color:#00D9B5;font-size:10.5px;font-weight:700;letter-spacing:.04em}
  .spacer{flex:1}
  .navlink{background:#0D1B35;border:1px solid #1A2E4A;color:#E2EAF4;border-radius:9px;padding:9px 14px;font-size:13px;font-weight:600;text-decoration:none}

  .hero{padding:22px 4% 16px;background:linear-gradient(135deg,rgba(0,217,181,.08),rgba(79,142,247,.05));border-bottom:1px solid #1A2E4A}
  .hero h1{font-size:22px;font-weight:800;margin-bottom:6px}
  .hero .accent{background:linear-gradient(135deg,#00D9B5,#4F8EF7);-webkit-background-clip:text;background-clip:text;color:transparent}
  .hero p{color:#9AB0CC;font-size:13px;max-width:760px;line-height:1.6}

  .layout{display:grid;grid-template-columns:1fr 380px;gap:0}
  @media (max-width:960px){.layout{grid-template-columns:1fr}}

  .main-col{padding:24px 4%}
  .graph{display:flex;gap:14px;align-items:flex-start;overflow-x:auto;padding-bottom:12px;margin-bottom:22px}
  .col{display:flex;flex-direction:column;gap:10px;min-width:150px}
  .arrow{align-self:center;color:#2A4468;font-size:20px;padding-top:34px}
  .tile{background:#0D1B35;border:1px solid #1A2E4A;border-radius:12px;padding:14px;transition:all .25s;position:relative}
  .tile .name{font-size:12.5px;font-weight:700;margin-bottom:4px}
  .tile .status{font-size:10.5px;color:#5E7391}
  .tile.active{border-color:#00D9B5;box-shadow:0 0 0 3px rgba(0,217,181,.15);transform:translateY(-2px)}
  .tile.active .status{color:#5FEBD1}
  .tile.waiting{border-color:#F5A623;box-shadow:0 0 0 3px rgba(245,166,35,.15)}
  .tile.waiting .status{color:#F5C767}

  .hitl-card{background:#171016;border:1px solid #F5A623;border-radius:12px;padding:16px;margin-bottom:20px;display:none}
  .hitl-card.show{display:block}
  .hitl-card h3{font-size:13.5px;margin-bottom:6px;color:#F5C767}
  .hitl-card p{font-size:12.5px;color:#D8C6A8;line-height:1.5;margin-bottom:12px}
  .hitl-btns{display:flex;gap:10px}
  .hitl-btns button{flex:1;border:none;border-radius:8px;padding:10px;font-weight:800;font-size:12.5px;cursor:pointer}
  .btn-approve{background:#00D9B5;color:#060B14}
  .btn-reject{background:#3A1420;color:#FF9DAE;border:1px solid #7A2436 !important}

  .chat-box{background:#0D1B35;border:1px solid #1A2E4A;border-radius:14px;padding:16px;margin-bottom:20px}
  .chat-box .hint{font-size:11.5px;color:#5E7391;margin-bottom:10px}
  #log{display:flex;flex-direction:column;gap:10px;max-height:280px;overflow-y:auto;margin-bottom:12px}
  .msg{max-width:90%;padding:9px 12px;border-radius:10px;font-size:12.5px;line-height:1.5}
  .user{align-self:flex-end;background:#00D9B5;color:#060B14;font-weight:500}
  .bot{align-self:flex-start;background:#132244;border:1px solid #1A2E4A}
  .agentic-form{display:flex;gap:8px}
  .agentic-form input{flex:1;background:#132244;border:1px solid #1A2E4A;border-radius:8px;padding:10px 12px;color:#fff;font-size:13px}
  .agentic-form button{background:#00D9B5;color:#060B14;border:none;border-radius:8px;padding:0 16px;font-weight:700;cursor:pointer;font-size:13px}
  .try-examples{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
  .try-ex{background:#132244;border:1px solid #1A2E4A;color:#8FA3C0;font-size:11px;padding:5px 10px;border-radius:20px;cursor:pointer}

  .feed-col{border-left:1px solid #1A2E4A;padding:24px 20px;max-height:100vh;overflow-y:auto;position:sticky;top:66px}
  .feed-col h3{font-size:13px;margin-bottom:4px}
  .feed-col .sub{font-size:11px;color:#5E7391;margin-bottom:14px}
  .feed-item{background:#0D1B35;border:1px solid #1A2E4A;border-radius:9px;padding:10px 12px;margin-bottom:8px}
  .feed-item .agent{font-size:10px;font-weight:800;color:#5FEBD1;text-transform:uppercase;letter-spacing:.03em}
  .feed-item .decision{font-size:12px;font-weight:700;margin:3px 0}
  .feed-item .detail{font-size:11px;color:#8FA3C0;line-height:1.4}
  .empty{color:#5E7391;font-size:12px;padding:20px 0;text-align:center}

  .history-section{margin-top:26px}
  .turn{background:#0D1B35;border:1px solid #1A2E4A;border-radius:10px;padding:12px 14px;margin-bottom:10px;cursor:pointer}
  .turn .msg-preview{font-size:12.5px;font-weight:600;margin-bottom:4px}
  .turn .turn-meta{font-size:10.5px;color:#5E7391}
  .turn-detail{display:none;margin-top:10px;padding-top:10px;border-top:1px solid #14213A}
  .turn-detail.show{display:block}
</style>
</head>
<body>

<header>
  <div class="mark">SB</div>
  <div class="brand"><b>BuyBuddy</b><small>AGENT ACTIVITY</small></div>
  <div class="spacer"></div>
  <a class="navlink" href="/">← Back to shop</a>
</header>

<div class="hero">
  <h1>Watch the <span class="accent">agentic layer</span> work, live</h1>
  <p>Every message here runs through six real, independently-deciding agents — not a fixed script. Classification and Routing determine which specialist handles it at runtime, and anything flagged for escalation genuinely pauses for a human decision before continuing.</p>
</div>

<div class="layout">
  <div class="main-col">
    <div class="graph" id="graph"></div>

    <div class="hitl-card" id="hitl-card">
      <h3>⏸ Human-in-the-Loop — waiting on you</h3>
      <p id="hitl-reason"></p>
      <div class="hitl-btns">
        <button class="btn-approve" id="hitl-approve">Approve escalation</button>
        <button class="btn-reject" id="hitl-reject">Reject</button>
      </div>
    </div>

    <div class="chat-box">
      <div class="hint">Try messages that hit different branches — the tiles above will light up as they're handled.</div>
      <div id="log"></div>
      <form class="agentic-form" id="agentic-form">
        <input id="agentic-input" placeholder="Ask BuyBuddy anything..." autocomplete="off">
        <button type="submit" id="agentic-send">Send</button>
      </form>
      <div class="try-examples">
        <div class="try-ex" data-msg="I need running shoes under $150">🏃 Product search</div>
        <div class="try-ex" data-msg="What's your return policy?">📦 Policy question</div>
        <div class="try-ex" data-msg="This is the third time my order hasn't arrived, I want to speak to someone">😠 Escalation</div>
        <div class="try-ex" data-msg="hey there!">👋 Chitchat</div>
      </div>
    </div>

    <div class="history-section">
      <h3 style="font-size:14px;margin-bottom:12px">Past turns — full decision trail</h3>
      <div id="history-list"><div class="empty">No turns yet — send a message above.</div></div>
    </div>
  </div>

  <div class="feed-col">
    <h3>Live Decision Feed</h3>
    <div class="sub">Streams as each agent completes, in order.</div>
    <div id="feed"><div class="empty">Send a message to see agents fire in real time.</div></div>
  </div>
</div>

<script>
const BB = __BOOTSTRAP__;
const graphEl = document.getElementById('graph');
const feedEl = document.getElementById('feed');
const log = document.getElementById('log');

// ---- build tile columns from server-provided graph shape ----
const cols = {};
BB.tiles.forEach(t => { (cols[t.col] = cols[t.col] || []).push(t); });
const colNums = Object.keys(cols).map(Number).sort((a,b)=>a-b);
colNums.forEach((c, i) => {
  const colDiv = document.createElement('div');
  colDiv.className = 'col';
  cols[c].forEach(t => {
    const tile = document.createElement('div');
    tile.className = 'tile';
    tile.id = 'tile-' + t.id;
    tile.innerHTML = `<div class="name">${t.label}</div><div class="status" id="status-${t.id}">idle</div>`;
    colDiv.appendChild(tile);
  });
  graphEl.appendChild(colDiv);
  if (i < colNums.length - 1) {
    const arrow = document.createElement('div');
    arrow.className = 'arrow';
    arrow.textContent = '→';
    graphEl.appendChild(arrow);
  }
});

function activateTile(id, statusText) {
  const tile = document.getElementById('tile-' + id);
  const status = document.getElementById('status-' + id);
  if (!tile) return;
  tile.classList.remove('waiting');
  tile.classList.add('active');
  if (status) status.textContent = statusText;
  setTimeout(() => tile.classList.remove('active'), 2200);
}
function waitingTile(id, statusText) {
  const tile = document.getElementById('tile-' + id);
  const status = document.getElementById('status-' + id);
  if (!tile) return;
  tile.classList.add('waiting');
  if (status) status.textContent = statusText;
}
function resetTiles() {
  document.querySelectorAll('.tile').forEach(t => t.classList.remove('active', 'waiting'));
  BB.tiles.forEach(t => { const s = document.getElementById('status-' + t.id); if (s) s.textContent = 'idle'; });
}

// map graph node names -> tile ids
const NODE_TO_TILE = {
  classify_intent: 'classification',
  route_decision: 'routing',
  pricing_fetch: 'pricing_fetch',
  policy: 'policy',
  handoff: 'handoff',
  hitl_wait: 'hitl',
  finalize_handoff: 'handoff',
  generate_reply: 'reply',
};

function addFeedItem(decision) {
  if (feedEl.querySelector('.empty')) feedEl.innerHTML = '';
  const item = document.createElement('div');
  item.className = 'feed-item';
  item.innerHTML = `<div class="agent">${decision.agent}</div><div class="decision">${decision.decision}</div><div class="detail">${decision.detail}</div>`;
  feedEl.insertBefore(item, feedEl.firstChild);
}

function addMsg(text, cls) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}

// ---- SSE-over-fetch stream reader ----
async function readStream(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split('\\n\\n');
    buf = parts.pop();
    for (const part of parts) {
      const line = part.trim();
      if (line.startsWith('data:')) {
        try { onEvent(JSON.parse(line.slice(5).trim())); } catch (e) {}
      }
    }
  }
}

function handleEvent(evt) {
  if (evt.type === 'agent_done') {
    const tileId = NODE_TO_TILE[evt.node];
    if (tileId) activateTile(tileId, evt.decision ? evt.decision.decision : 'done');
    if (evt.decision) addFeedItem(evt.decision);
  } else if (evt.type === 'hitl_pending') {
    waitingTile('hitl', 'waiting for you');
    document.getElementById('hitl-reason').textContent = evt.payload.proposal.reason || evt.payload.question;
    document.getElementById('hitl-card').classList.add('show');
  } else if (evt.type === 'final') {
    addMsg(evt.reply, 'bot');
    loadHistory();
  }
}

const form = document.getElementById('agentic-form');
const input = document.getElementById('agentic-input');
const sendBtn = document.getElementById('agentic-send');

async function sendMessage(message) {
  addMsg(message, 'user');
  resetTiles();
  document.getElementById('hitl-card').classList.remove('show');
  sendBtn.disabled = true;
  try {
    const res = await fetch('/agentic/chat/stream', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message})
    });
    await readStream(res, handleEvent);
  } catch (e) {
    addMsg('Something went wrong reaching the agent layer.', 'bot');
  } finally {
    sendBtn.disabled = false;
  }
}

form.addEventListener('submit', e => {
  e.preventDefault();
  const message = input.value.trim();
  if (!message) return;
  input.value = '';
  sendMessage(message);
});

document.querySelectorAll('.try-ex').forEach(el => {
  el.addEventListener('click', () => sendMessage(el.dataset.msg));
});

document.getElementById('hitl-approve').onclick = () => resolveHitl('approved');
document.getElementById('hitl-reject').onclick = () => resolveHitl('rejected');

async function resolveHitl(decision) {
  document.getElementById('hitl-card').classList.remove('show');
  try {
    const res = await fetch('/agentic/chat/resume', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({decision})
    });
    await readStream(res, handleEvent);
  } catch (e) {
    addMsg('Something went wrong resuming the agent layer.', 'bot');
  }
}

// ---- history / trace replay ----
async function loadHistory() {
  const res = await fetch('/agentic/history');
  const data = await res.json();
  const wrap = document.getElementById('history-list');
  wrap.innerHTML = '';
  if (!data.turns.length) {
    wrap.innerHTML = '<div class="empty">No turns yet — send a message above.</div>';
    return;
  }
  data.turns.forEach((t, idx) => {
    const el = document.createElement('div');
    el.className = 'turn';
    const detailId = 'turn-detail-' + idx;
    el.innerHTML = `
      <div class="msg-preview">"${t.message}"</div>
      <div class="turn-meta">Turn ${t.turn_index} · ${t.decisions.length} agent decisions · click to expand</div>
      <div class="turn-detail" id="${detailId}">
        ${t.decisions.map(d => `<div class="feed-item"><div class="agent">${d.agent}</div><div class="decision">${d.decision}</div><div class="detail">${d.detail}</div></div>`).join('')}
      </div>
    `;
    el.addEventListener('click', () => document.getElementById(detailId).classList.toggle('show'));
    wrap.appendChild(el);
  });
}
loadHistory();
</script>
</body>
</html>"""

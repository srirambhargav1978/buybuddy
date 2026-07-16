import json
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Cookie, FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

import auth
import db
from agent import BUYBUDDY_GRAPH, build_user_profile
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

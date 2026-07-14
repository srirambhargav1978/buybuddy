import os
import uuid
from typing import Dict

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agent import BUYBUDDY_GRAPH

app = FastAPI(title="BuyBuddy")

# In-memory session store — fine for a demo, resets on container restart.
# Swap for Redis/Postgres if this needs to survive restarts later.
SESSIONS: Dict[str, dict] = {}

LANGFUSE_ENABLED = bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))


def _get_langfuse_handler():
    if not LANGFUSE_ENABLED:
        return None
    try:
        from langfuse.callback import CallbackHandler
        return CallbackHandler()
    except Exception:
        return None


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    preferences: dict
    candidates: list


@app.get("/health")
def health():
    return {"status": "ok", "service": "buybuddy"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())
    session = SESSIONS.setdefault(session_id, {"history": [], "preferences": {}})

    state = {
        "session_id": session_id,
        "message": req.message,
        "history": session["history"],
        "preferences": session["preferences"],
        "candidates": [],
        "reply": "",
    }

    config = {}
    handler = _get_langfuse_handler()
    if handler:
        config["callbacks"] = [handler]

    result = BUYBUDDY_GRAPH.invoke(state, config=config)

    session["history"].append({"role": "user", "content": req.message})
    session["history"].append({"role": "assistant", "content": result["reply"]})
    session["preferences"] = result["preferences"]

    return ChatResponse(
        session_id=session_id,
        reply=result["reply"],
        preferences=result["preferences"],
        candidates=result["candidates"],
    )


@app.get("/", response_class=HTMLResponse)
def chat_ui():
    return CHAT_UI_HTML


CHAT_UI_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BuyBuddy — AI Shopping Assistant</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Inter',sans-serif;background:#060B14;color:#E2EAF4;height:100vh;display:flex;flex-direction:column}
  header{padding:20px 5%;border-bottom:1px solid #1A2E4A;display:flex;align-items:center;gap:10px}
  .mark{width:32px;height:32px;background:linear-gradient(135deg,#00D9B5,#4F8EF7);border-radius:7px;display:flex;align-items:center;justify-content:center;font-weight:800;color:#060B14;font-size:13px}
  header span{font-weight:700;font-size:15px}
  header small{color:#6B84A3;margin-left:8px}
  #log{flex:1;overflow-y:auto;padding:24px 5%;display:flex;flex-direction:column;gap:14px;max-width:760px;margin:0 auto;width:100%}
  .msg{max-width:75%;padding:12px 16px;border-radius:14px;font-size:14px;line-height:1.6}
  .user{align-self:flex-end;background:#00D9B5;color:#060B14;font-weight:500}
  .bot{align-self:flex-start;background:#0D1B35;border:1px solid #1A2E4A}
  .candidates{align-self:flex-start;display:flex;flex-wrap:wrap;gap:8px;max-width:85%}
  .card{background:#0D1B35;border:1px solid #1A2E4A;border-radius:10px;padding:10px 12px;font-size:12px;width:150px}
  .card b{display:block;color:#fff;font-size:12.5px;margin-bottom:4px}
  .card .price{color:#00D9B5;font-weight:700;margin-top:6px}
  form{border-top:1px solid #1A2E4A;padding:16px 5%;display:flex;gap:10px;max-width:760px;margin:0 auto;width:100%}
  input{flex:1;background:#0D1B35;border:1px solid #1A2E4A;border-radius:8px;padding:12px 14px;color:#fff;font-size:14px}
  input:focus{outline:none;border-color:#00D9B5}
  button{background:#00D9B5;color:#060B14;border:none;border-radius:8px;padding:0 22px;font-weight:700;cursor:pointer;font-size:14px}
  button:disabled{opacity:.5;cursor:default}
  .hint{color:#6B84A3;font-size:12px;text-align:center;padding:6px 0 14px}
</style>
</head>
<body>

<header>
  <div class="mark">SB</div>
  <span>BuyBuddy</span>
  <small>AI Shopping Assistant &middot; LangGraph + LiteLLM</small>
</header>

<div id="log">
  <div class="msg bot">Hi, I'm BuyBuddy. Tell me what you're shopping for &mdash; category, budget, anything you have in mind &mdash; and I'll find a few good options.</div>
</div>
<div class="hint">Try: "I need running shoes under $150" or "something for hiking, budget around $100"</div>

<form id="chat-form">
  <input id="input" type="text" placeholder="Ask BuyBuddy for a recommendation..." autocomplete="off">
  <button type="submit" id="send-btn">Send</button>
</form>

<script>
let sessionId = null;
const log = document.getElementById('log');
const form = document.getElementById('chat-form');
const input = document.getElementById('input');
const btn = document.getElementById('send-btn');

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
    c.className = 'card';
    c.innerHTML = '<b>' + p.name + '</b>' + p.description + '<div class="price">$' + p.price + '</div>';
    wrap.appendChild(c);
  });
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const message = input.value.trim();
  if (!message) return;
  addMsg(message, 'user');
  input.value = '';
  btn.disabled = true;

  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sessionId, message})
    });
    const data = await res.json();
    sessionId = data.session_id;
    addMsg(data.reply, 'bot');
    addCandidates(data.candidates);
  } catch (err) {
    addMsg('Something went wrong reaching BuyBuddy. Please try again.', 'bot');
  } finally {
    btn.disabled = false;
    input.focus();
  }
});
</script>

</body>
</html>"""

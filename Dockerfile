# BuyBuddy — FastAPI + LangGraph shopping assistant.
# Real, working app: main.py (API + chat UI), agent.py (3-node LangGraph:
# extract preferences -> filter catalog -> generate reply), catalog.py
# (demo product list). Session state is in-memory for now — if you later
# want it to survive container restarts, add Postgres/Redis as another
# service in docker-compose.yml and swap SESSIONS in main.py for it.

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

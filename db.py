"""
SQLite persistence for BuyBuddy: purchase history + editable preference tags
for the three registered accounts (user1/2/3).

Guests (guest1/2/3) intentionally have no rows here — they start every
session with a clean slate, same as a brand-new shopper.

On first run, seeds each registered user with 10-15 past orders and a
starting set of preference tags that match their persona, so the AI
assistant and the UI both have real history to draw on immediately.
"""

import datetime
import random
import sqlite3
from typing import Dict, List

from catalog import find_by_name

DB_PATH = "buybuddy.db"

# --- Seed data: persona-matched past purchases + starting preferences -----

_SEED_PURCHASES = {
    "user1": {  # Ava Chen — fitness & minimalist tech
        "items": [
            "AeroFit Running Shoes", "PulseBand Fitness Tracker", "FlexFit Yoga Mat",
            "NoiseGuard Pro Headphones", "SpeedRope Jump Rope", "MotionFit Performance Tee",
            "AirBuds Pro Earbuds", "RollEase Foam Roller", "PowerBand Resistance Set",
            "SprintLine Trail Runners", "TrainBag Gym Duffel", "SwiftCharge Power Bank",
        ],
        "preferences": ["running", "minimalist", "noise-cancelling", "budget-conscious", "fitness"],
    },
    "user2": {  # Priya Nair — outdoor & adventure
        "items": [
            "Trailblazer Hiking Boots", "SummitPack 40L Backpack", "StormShield Rain Jacket",
            "TrailLight Headlamp", "BasecampDome 2P Tent", "PureFlow Water Filter Bottle",
            "SteadyStride Trekking Poles", "DriftSleep 3-Season Sleeping Bag",
            "SummitVista Binoculars", "TrailShade Sport Sunglasses", "BaseCamp Softshell Jacket",
            "AllTerrain Camp Chair", "EmberStove Camp Stove",
        ],
        "preferences": ["hiking", "outdoor", "waterproof", "durable", "camping"],
    },
    "user3": {  # Marcus Lee — streetwear & gadgets
        "items": [
            "NoiseGuard Pro Headphones", "QuietType Mechanical Keyboard", "UrbanStep Sneakers",
            "UrbanTrek Denim Jacket", "Meridian Automatic Watch", "SkyLine Aviator Sunglasses",
            "ViewFinder Mirrorless Camera", "PrimeSound Bluetooth Speaker", "IndigoWash Denim Shirt",
            "StreamCast Portable Projector", "HeritageLeather Wallet", "PrecisionTrack Wireless Mouse",
            "HomeHub Smart Speaker",
        ],
        "preferences": ["streetwear", "tech-gadgets", "premium", "watches", "audio"],
    },
}


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL,
            purchased_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS preferences (
            username TEXT NOT NULL,
            tag TEXT NOT NULL,
            PRIMARY KEY (username, tag)
        )
    """)
    conn.commit()

    # Seed only if empty — keeps this idempotent across restarts.
    existing = conn.execute("SELECT COUNT(*) AS c FROM purchases").fetchone()["c"]
    if existing == 0:
        _seed(conn)
    conn.close()


def _seed(conn):
    today = datetime.date.today()
    for username, data in _SEED_PURCHASES.items():
        for i, item_name in enumerate(data["items"]):
            product = find_by_name(item_name)
            if not product:
                continue
            days_ago = random.randint(10, 240) + i * 3
            purchased_at = (today - datetime.timedelta(days=days_ago)).isoformat()
            conn.execute(
                "INSERT INTO purchases (username, product_id, product_name, category, price, purchased_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (username, product["id"], product["name"], product["category"], product["price"], purchased_at),
            )
        for tag in data["preferences"]:
            conn.execute(
                "INSERT OR IGNORE INTO preferences (username, tag) VALUES (?, ?)",
                (username, tag),
            )
    conn.commit()


def get_purchase_history(username: str) -> List[Dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT product_id, product_name, category, price, purchased_at "
        "FROM purchases WHERE username = ? ORDER BY purchased_at DESC",
        (username,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_preferences(username: str) -> List[str]:
    conn = _connect()
    rows = conn.execute(
        "SELECT tag FROM preferences WHERE username = ? ORDER BY tag", (username,)
    ).fetchall()
    conn.close()
    return [r["tag"] for r in rows]


def add_preference(username: str, tag: str) -> None:
    tag = tag.strip().lower()
    if not tag:
        return
    conn = _connect()
    conn.execute("INSERT OR IGNORE INTO preferences (username, tag) VALUES (?, ?)", (username, tag))
    conn.commit()
    conn.close()


def remove_preference(username: str, tag: str) -> None:
    conn = _connect()
    conn.execute("DELETE FROM preferences WHERE username = ? AND tag = ?", (username, tag))
    conn.commit()
    conn.close()

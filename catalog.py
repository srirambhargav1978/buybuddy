"""
Small in-memory product catalog for the BuyBuddy demo.

This is intentionally not a real database — it's enough variety for the
agent to have something meaningful to filter and recommend from. Swap this
for a real product DB / API call later without touching agent.py's logic,
as long as you keep the same dict shape.
"""

CATALOG = [
    {"id": 1, "name": "AeroFit Running Shoes", "category": "footwear", "price": 129, "tags": ["running", "sport", "lightweight"], "description": "Breathable mesh running shoes built for daily training."},
    {"id": 2, "name": "Trailblazer Hiking Boots", "category": "footwear", "price": 189, "tags": ["hiking", "outdoor", "durable"], "description": "Waterproof boots with reinforced ankle support for rough terrain."},
    {"id": 3, "name": "UrbanStep Sneakers", "category": "footwear", "price": 89, "tags": ["casual", "everyday", "budget"], "description": "Comfortable everyday sneakers at an affordable price point."},
    {"id": 4, "name": "NoiseGuard Pro Headphones", "category": "electronics", "price": 249, "tags": ["audio", "noise-cancelling", "travel"], "description": "Active noise-cancelling over-ear headphones with 30-hour battery."},
    {"id": 5, "name": "PulseBand Fitness Tracker", "category": "electronics", "price": 79, "tags": ["fitness", "wearable", "budget"], "description": "Tracks heart rate, sleep, and steps with a 10-day battery life."},
    {"id": 6, "name": "ClearView 4K Webcam", "category": "electronics", "price": 65, "tags": ["work-from-home", "video", "budget"], "description": "Sharp 4K webcam with auto-focus, built for video calls."},
    {"id": 7, "name": "SummitPack 40L Backpack", "category": "outdoor", "price": 139, "tags": ["hiking", "travel", "durable"], "description": "Weatherproof 40L pack designed for multi-day hikes."},
    {"id": 8, "name": "CozyLayer Fleece Jacket", "category": "apparel", "price": 69, "tags": ["outdoor", "warm", "casual"], "description": "Lightweight fleece jacket, great for cool-weather layering."},
    {"id": 9, "name": "MinimalDesk Standing Desk", "category": "home-office", "price": 349, "tags": ["work-from-home", "ergonomic"], "description": "Electric height-adjustable desk with memory presets."},
    {"id": 10, "name": "QuietType Mechanical Keyboard", "category": "electronics", "price": 99, "tags": ["work-from-home", "typing"], "description": "Low-noise mechanical keyboard suited for shared workspaces."},
    {"id": 11, "name": "EcoBrew Reusable Coffee Cup", "category": "home", "price": 19, "tags": ["budget", "everyday", "eco"], "description": "Insulated reusable cup that keeps drinks hot for 6 hours."},
    {"id": 12, "name": "FlexFit Yoga Mat", "category": "fitness", "price": 39, "tags": ["fitness", "home-workout", "budget"], "description": "Non-slip yoga mat with alignment lines, 6mm cushioning."},
    {"id": 13, "name": "StormShield Rain Jacket", "category": "apparel", "price": 119, "tags": ["hiking", "outdoor", "waterproof"], "description": "Fully waterproof, packable rain shell for unpredictable weather."},
    {"id": 14, "name": "TrailLight Headlamp", "category": "outdoor", "price": 34, "tags": ["hiking", "camping", "budget"], "description": "Rechargeable headlamp with 3 brightness modes."},
    {"id": 15, "name": "PrimeSound Bluetooth Speaker", "category": "electronics", "price": 59, "tags": ["audio", "travel", "budget"], "description": "Compact waterproof speaker with 12-hour battery life."},
]

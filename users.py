"""
Fixed demo accounts for BuyBuddy.

This is intentionally a hardcoded, tiny user store — the point is to gate
the demo behind a login, not to build real account infrastructure. Do not
reuse this scheme (plaintext-compared passwords, no signup, no password
reset) for anything beyond a personal portfolio demo.

Three "registered" shoppers with purchase history + preferences already on
file (seeded in db.py), and three "guest" accounts that start with a
completely clean slate every time — same as a brand-new visitor.
"""

USERS = {
    "user1": {
        "password": "user@12345",
        "role": "registered",
        "name": "Ava Chen",
        "gender": "female",
        "avatar": "female_a",
        "persona": "Fitness & minimalist tech",
    },
    "user2": {
        "password": "user@12345",
        "role": "registered",
        "name": "Priya Nair",
        "gender": "female",
        "avatar": "female_b",
        "persona": "Outdoor & adventure",
    },
    "user3": {
        "password": "user@12345",
        "role": "registered",
        "name": "Marcus Lee",
        "gender": "male",
        "avatar": "male_a",
        "persona": "Streetwear & gadgets",
    },
    "guest1": {
        "password": "guest@12345",
        "role": "guest",
        "name": "Guest One",
        "gender": None,
        "avatar": None,
        "persona": None,
    },
    "guest2": {
        "password": "guest@12345",
        "role": "guest",
        "name": "Guest Two",
        "gender": None,
        "avatar": None,
        "persona": None,
    },
    "guest3": {
        "password": "guest@12345",
        "role": "guest",
        "name": "Guest Three",
        "gender": None,
        "avatar": None,
        "persona": None,
    },
}


def verify_login(username: str, password: str):
    """Returns the user dict (with 'username' added) on success, else None."""
    user = USERS.get(username)
    if user and user["password"] == password:
        return {**user, "username": username}
    return None


def get_user(username: str):
    user = USERS.get(username)
    if not user:
        return None
    return {**user, "username": username}

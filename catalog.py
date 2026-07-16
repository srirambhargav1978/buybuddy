"""
Product catalog for the BuyBuddy demo.

Not a real database — a large, varied in-memory list is enough for the
agent to filter/recommend from and for the catalog UI to feel like a real
store. Swap this for a real product DB later without touching agent.py's
logic, as long as you keep the same dict shape.

Each item has:
  id, name, category, price, tags, description, icon (emoji used on the
  card since there's no product photography), try_on (bool — eligible for
  the stylized Try On feature; only eyewear / shirts / outerwear are).
"""

# category -> (card icon, tryOn eligible)
CATEGORY_META = {
    "footwear":     {"icon": "\U0001F45F", "try_on": False},
    "electronics":  {"icon": "\U0001F3A7", "try_on": False},
    "eyewear":      {"icon": "\U0001F576", "try_on": True},
    "shirts":       {"icon": "\U0001F455", "try_on": True},
    "outerwear":    {"icon": "\U0001F9E5", "try_on": True},
    "outdoor":      {"icon": "\U0001F3D5", "try_on": False},
    "fitness":      {"icon": "\U0001F3CB", "try_on": False},
    "home-office":  {"icon": "\U0001FA91", "try_on": False},
    "home":         {"icon": "\U0001F3E0", "try_on": False},
    "accessories":  {"icon": "\U0000231A", "try_on": False},
}

# (name, category, price, tags, description, icon override or None)
_RAW = [
    # --- footwear ---
    ("AeroFit Running Shoes", "footwear", 129, ["running", "sport", "lightweight"], "Breathable mesh running shoes built for daily training.", None),
    ("Trailblazer Hiking Boots", "footwear", 189, ["hiking", "outdoor", "durable"], "Waterproof boots with reinforced ankle support for rough terrain.", None),
    ("UrbanStep Sneakers", "footwear", 89, ["casual", "everyday", "budget"], "Comfortable everyday sneakers at an affordable price point.", None),
    ("SprintLine Trail Runners", "footwear", 145, ["running", "trail", "grip"], "Aggressive-tread trail runners for off-road distance training.", None),
    ("CloudStep Walking Shoes", "footwear", 99, ["casual", "comfort", "everyday"], "All-day cushioned walking shoes with a knit upper.", None),
    ("Oxford Classic Dress Shoes", "footwear", 159, ["formal", "leather", "office"], "Hand-finished leather oxfords for the office or events.", None),
    ("SunDeck Sport Sandals", "footwear", 49, ["summer", "casual", "budget"], "Quick-dry sport sandals with adjustable straps.", None),
    ("PowerLift Training Shoes", "footwear", 135, ["gym", "training", "stability"], "Flat, stable sole built for lifting and cross-training.", None),
    ("MetroCasual Loafers", "footwear", 109, ["casual", "leather", "everyday"], "Slip-on leather loafers that dress up or down easily.", None),
    ("IceGrip Winter Boots", "footwear", 175, ["winter", "warm", "outdoor"], "Insulated, salt-resistant boots rated to -30°C.", None),
    ("FeatherLite Racing Flats", "footwear", 119, ["running", "race", "lightweight"], "Ultra-light racing flats for competition-day speed.", None),
    ("HarborWalk Boat Shoes", "footwear", 89, ["casual", "summer", "leather"], "Classic leather boat shoes with a non-marking sole.", None),

    # --- electronics ---
    ("NoiseGuard Pro Headphones", "electronics", 249, ["audio", "noise-cancelling", "travel"], "Active noise-cancelling over-ear headphones with 30-hour battery.", None),
    ("PulseBand Fitness Tracker", "electronics", 79, ["fitness", "wearable", "budget"], "Tracks heart rate, sleep, and steps with a 10-day battery life.", None),
    ("ClearView 4K Webcam", "electronics", 65, ["work-from-home", "video", "budget"], "Sharp 4K webcam with auto-focus, built for video calls.", None),
    ("QuietType Mechanical Keyboard", "electronics", 99, ["work-from-home", "typing"], "Low-noise mechanical keyboard suited for shared workspaces.", None),
    ("PrimeSound Bluetooth Speaker", "electronics", 59, ["audio", "travel", "budget"], "Compact waterproof speaker with 12-hour battery life.", None),
    ("AirBuds Pro Earbuds", "electronics", 159, ["audio", "wireless", "travel"], "True wireless earbuds with adaptive noise cancellation.", None),
    ("SwiftCharge Power Bank", "electronics", 45, ["travel", "budget", "everyday"], "20,000mAh power bank with fast USB-C charging.", None),
    ("FocusView Monitor Light Bar", "electronics", 55, ["work-from-home", "ergonomic"], "Glare-free monitor light bar with auto brightness.", None),
    ("PrecisionTrack Wireless Mouse", "electronics", 39, ["work-from-home", "budget"], "Silent-click ergonomic mouse with a 3-month battery life.", None),
    ("ViewFinder Mirrorless Camera", "electronics", 699, ["photography", "premium", "travel"], "Compact mirrorless camera with 4K video and fast autofocus.", None),
    ("HomeHub Smart Speaker", "electronics", 89, ["smart-home", "audio", "budget"], "Voice-controlled smart speaker with rich bass.", None),
    ("StreamCast Portable Projector", "electronics", 229, ["entertainment", "travel"], "Pocket-sized projector for movie nights anywhere.", None),

    # --- eyewear (try-on eligible) ---
    ("SkyLine Aviator Sunglasses", "eyewear", 79, ["sunglasses", "classic", "uv-protection"], "Timeless aviator sunglasses with polarized UV400 lenses.", "\U0001F576"),
    ("RetroRound Sunglasses", "eyewear", 69, ["sunglasses", "retro", "casual"], "Round vintage-inspired frames in matte acetate.", "\U0001F576"),
    ("TrailShade Sport Sunglasses", "eyewear", 89, ["sunglasses", "sport", "outdoor"], "Wraparound polarized shades built for running and cycling.", "\U0001F3BD"),
    ("FocusLens Blue-Light Glasses", "eyewear", 45, ["glasses", "work-from-home", "budget"], "Blue-light filtering glasses for long screen sessions.", "\U0001F453"),
    ("Wayfare Classic Sunglasses", "eyewear", 75, ["sunglasses", "classic", "everyday"], "Bold square frames that suit most face shapes.", "\U0001F576"),
    ("CatEye Statement Sunglasses", "eyewear", 85, ["sunglasses", "fashion", "statement"], "Sharp cat-eye frames with gradient lenses.", "\U0001F576"),
    ("PolarMax Fishing Sunglasses", "eyewear", 99, ["sunglasses", "outdoor", "polarized"], "High-contrast polarized lenses that cut glare off water.", "\U0001F3BD"),
    ("ReadEasy Reading Glasses", "eyewear", 29, ["glasses", "budget", "everyday"], "Lightweight reading glasses with an anti-scratch coating.", "\U0001F453"),

    # --- shirts (try-on eligible) ---
    ("Oxford Button-Down Shirt", "shirts", 65, ["formal", "office", "cotton"], "Crisp cotton oxford shirt that works desk-to-dinner.", "\U0001F455"),
    ("Heritage Flannel Shirt", "shirts", 59, ["casual", "warm", "outdoor"], "Brushed flannel shirt with a soft, heavyweight feel.", "\U0001F455"),
    ("CoolLinen Summer Shirt", "shirts", 55, ["summer", "casual", "breathable"], "Airy linen-blend shirt built for hot weather.", "\U0001F455"),
    ("Everyday Graphic Tee", "shirts", 29, ["casual", "budget", "everyday"], "Soft cotton tee with a minimalist print.", "\U0001F455"),
    ("ClubFit Polo Shirt", "shirts", 49, ["casual", "sport", "everyday"], "Breathable pique polo with a tailored fit.", "\U0001F455"),
    ("WeekendHenley Shirt", "shirts", 45, ["casual", "layering", "everyday"], "Three-button henley in soft brushed cotton.", "\U0001F455"),
    ("IndigoWash Denim Shirt", "shirts", 69, ["casual", "denim", "layering"], "Lightweight denim shirt that layers well in any season.", "\U0001F455"),
    ("MotionFit Performance Tee", "shirts", 39, ["sport", "training", "moisture-wicking"], "Moisture-wicking training tee that moves with you.", "\U0001F455"),

    # --- outerwear (try-on eligible) ---
    ("CozyLayer Fleece Jacket", "outerwear", 69, ["outdoor", "warm", "casual"], "Lightweight fleece jacket, great for cool-weather layering.", "\U0001F9E5"),
    ("StormShield Rain Jacket", "outerwear", 119, ["hiking", "outdoor", "waterproof"], "Fully waterproof, packable rain shell for unpredictable weather.", "\U0001F9E5"),
    ("SummitDown Puffer Jacket", "outerwear", 189, ["winter", "warm", "outdoor"], "Down-insulated puffer rated for sub-zero temperatures.", "\U0001F9E5"),
    ("UrbanTrek Denim Jacket", "outerwear", 89, ["casual", "denim", "streetwear"], "Classic denim jacket with a modern tailored cut.", "\U0001F9E5"),
    ("Heritage Wool Overcoat", "outerwear", 249, ["formal", "warm", "premium"], "Tailored wool overcoat for smart, cold-weather dressing.", "\U0001F9E5"),
    ("GaleForce Windbreaker", "outerwear", 79, ["running", "outdoor", "lightweight"], "Packable windbreaker that cuts through cold gusts.", "\U0001F9E5"),
    ("LondonFog Trench Coat", "outerwear", 199, ["formal", "classic", "rain"], "Water-resistant trench coat with a timeless silhouette.", "\U0001F9E5"),
    ("BaseCamp Softshell Jacket", "outerwear", 139, ["hiking", "outdoor", "wind-resistant"], "Stretchy softshell jacket built for active days outdoors.", "\U0001F9E5"),

    # --- outdoor ---
    ("SummitPack 40L Backpack", "outdoor", 139, ["hiking", "travel", "durable"], "Weatherproof 40L pack designed for multi-day hikes.", None),
    ("TrailLight Headlamp", "outdoor", 34, ["hiking", "camping", "budget"], "Rechargeable headlamp with 3 brightness modes.", None),
    ("BasecampDome 2P Tent", "outdoor", 219, ["camping", "outdoor", "durable"], "Lightweight 2-person tent that pitches in under 5 minutes.", None),
    ("DriftSleep 3-Season Sleeping Bag", "outdoor", 129, ["camping", "outdoor", "warm"], "Compressible sleeping bag rated to -5°C.", None),
    ("EmberStove Camp Stove", "outdoor", 59, ["camping", "outdoor"], "Compact folding stove that boils water in under 3 minutes.", None),
    ("PureFlow Water Filter Bottle", "outdoor", 45, ["hiking", "camping", "budget"], "Filters 99.9% of bacteria straight from the bottle.", None),
    ("SteadyStride Trekking Poles", "outdoor", 69, ["hiking", "outdoor"], "Collapsible carbon trekking poles with shock absorption.", None),
    ("FrostBox 24-Can Cooler", "outdoor", 89, ["camping", "outdoor", "durable"], "Rotomolded cooler that holds ice for up to 3 days.", None),
    ("SummitVista Binoculars", "outdoor", 99, ["hiking", "outdoor", "travel"], "Compact 10x42 binoculars for trail and wildlife viewing.", None),
    ("AllTerrain Camp Chair", "outdoor", 55, ["camping", "outdoor", "comfort"], "Lightweight folding chair that packs into its own bag.", None),

    # --- fitness ---
    ("FlexFit Yoga Mat", "fitness", 39, ["fitness", "home-workout", "budget"], "Non-slip yoga mat with alignment lines, 6mm cushioning.", None),
    ("PowerBand Resistance Set", "fitness", 29, ["fitness", "home-workout", "budget"], "Five-band resistance set covering light to heavy tension.", None),
    ("IronCore Adjustable Dumbbells", "fitness", 179, ["fitness", "strength", "home-workout"], "Space-saving dumbbells that adjust from 5-50lbs.", None),
    ("RollEase Foam Roller", "fitness", 25, ["fitness", "recovery", "budget"], "High-density foam roller for post-workout recovery.", None),
    ("SpeedRope Jump Rope", "fitness", 19, ["fitness", "cardio", "budget"], "Ball-bearing speed rope for cardio and HIIT.", None),
    ("TrainBag Gym Duffel", "fitness", 49, ["fitness", "travel", "everyday"], "Water-resistant duffel with a dedicated shoe compartment.", None),

    # --- home-office ---
    ("MinimalDesk Standing Desk", "home-office", 349, ["work-from-home", "ergonomic"], "Electric height-adjustable desk with memory presets.", None),
    ("BrightArc Desk Lamp", "home-office", 45, ["work-from-home", "budget"], "Adjustable LED desk lamp with warm/cool modes.", None),
    ("FlexArm Monitor Mount", "home-office", 59, ["work-from-home", "ergonomic"], "Gas-spring monitor arm that frees up desk space.", None),
    ("ErgoSit Office Chair", "home-office", 279, ["work-from-home", "ergonomic", "premium"], "Breathable mesh chair with adjustable lumbar support.", None),
    ("TidyDesk Cable Organizer Set", "home-office", 15, ["work-from-home", "budget"], "Clip-and-sleeve set that keeps cables off the desk.", None),

    # --- home ---
    ("EcoBrew Reusable Coffee Cup", "home", 19, ["budget", "everyday", "eco"], "Insulated reusable cup that keeps drinks hot for 6 hours.", None),
    ("CloudNine Throw Blanket", "home", 39, ["comfort", "home", "budget"], "Ultra-soft knit throw blanket for the couch or bed.", None),
    ("HearthGlow Candle Set", "home", 29, ["home", "gift", "budget"], "Set of 3 soy candles in warm, cozy scents.", None),
    ("MorningRitual Ceramic Mug Set", "home", 25, ["home", "gift", "everyday"], "Set of 2 hand-glazed ceramic mugs.", None),

    # --- accessories ---
    ("Meridian Automatic Watch", "accessories", 229, ["watches", "premium", "classic"], "Automatic movement watch with a sapphire crystal face.", None),
    ("HeritageLeather Wallet", "accessories", 55, ["leather", "everyday", "gift"], "Slim bifold wallet in full-grain leather.", None),
    ("ClassicStitch Leather Belt", "accessories", 45, ["leather", "everyday", "formal"], "Reversible leather belt, black and brown in one.", None),
    ("TrailBrim Cap", "accessories", 25, ["casual", "outdoor", "budget"], "Breathable cap with UPF 50+ sun protection.", None),
    ("WoolKnit Scarf", "accessories", 35, ["winter", "warm", "gift"], "Chunky knit scarf in merino wool.", None),
    ("CarryAll Canvas Tote", "accessories", 39, ["casual", "everyday", "budget"], "Durable canvas tote that fits a 15\" laptop.", None),
]


def _build_catalog():
    catalog = []
    for i, (name, category, price, tags, description, icon_override) in enumerate(_RAW, start=1):
        meta = CATEGORY_META[category]
        catalog.append({
            "id": i,
            "name": name,
            "category": category,
            "price": price,
            "tags": tags,
            "description": description,
            "icon": icon_override or meta["icon"],
            "try_on": meta["try_on"],
        })
    return catalog


CATALOG = _build_catalog()
CATALOG_BY_ID = {item["id"]: item for item in CATALOG}
CATEGORIES = sorted(CATEGORY_META.keys())


def find_by_name(name: str):
    name = name.lower()
    for item in CATALOG:
        if item["name"].lower() == name:
            return item
    return None

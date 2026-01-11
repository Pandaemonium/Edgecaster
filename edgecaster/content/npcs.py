"""Lightweight NPC definitions and future-proofing for faction/disposition."""

NPC_DEFS = {
    "mentor": {
        "name": "Mentor",
        "factions": ["edgecasters"],
        "base_disposition": 10,
        "description": "Old, one-eyed and syphilitic, yet unerringly optimistic.",
        "dialogue": [
            "Ah, another step along the recursion.",
            "Choose a new pattern to weave into your repertoire.",
        ],
    },
    "caged_demon": {
        "name": "Caged Demon",
        "factions": ["neutral"],
        "base_disposition": 0,
        "description": "A hulking demon chained for training bouts. It regenerates quickly.",
        "dialogue": [
            "The demon growls, eager to be struck.",
            "You may practice your strikes here; it will regenerate.",
        ],
    },
    "hexmage": {
        "name": "The Hexmage",
        "factions": ["edgecasters"],
        "base_disposition": 5,
        "description": "This runecaster is swarming with bees.",
        "dialogue": [
            "Weave your sigils on a lattice of hexes.",
            "I'll open the hex drafting grid for you.",
        ],
    },
    "cartographer": {
        "name": "The Cartographer",
        "factions": ["edgecasters"],
        "base_disposition": 5,
        "description": "This chick is WAY too hot to be a cartographer.",
        "dialogue": [
            "Need more room to sketch? I can unroll a wide parchment.",
            "Let's draft on a spacious rectangular grid.",
        ],
    },
    "guide_npc": {
        "name": "Local Guide",
        "factions": ["neutral"],
        "base_disposition": 10,
        "description": "A friendly local who knows the area well.",
        "dialogue": [
            "Welcome, traveler! I know these lands like the back of my hand.",
            "There's an inventor nearby who studies the corruption patterns. You should visit them!",
            "I've marked their workshop on your map. Head northeast from here.",
        ],
        "quest_trigger": "find_the_inventor",  # Quest to give
        "quest_location": [55, 48],  # Inventor's location to mark on map
    },
    "inventor_npc": {
        "name": "The Inventor",
        "factions": ["neutral"],
        "base_disposition": 5,
        "description": "An eccentric inventor studying corruption patterns.",
        "dialogue": [
            "Fascinating! Another pattern-weaver arrives.",
            "I've been studying the corruption's mathematical properties. Such elegant chaos!",
            "If you learn anything about the seals, do return. I'd love to compare notes.",
        ],
        "quest_complete": "find_the_inventor",  # Quest to complete on dialogue
    },
    "lair_informant": {
        "name": "Lair Informant",
        "factions": ["neutral"],
        "base_disposition": 5,
        "description": "A well-connected rumor-monger with a keen eye for danger.",
        "dialogue": [
            "Legends leave footprints in the recursion.",
            "Ask, and I'll mark the nearest lairs on your map.",
        ],
    },
    "merchant": {
        "name": "Merchant",
        "factions": ["neutral"],
        "base_disposition": 5,
        "description": "A trader with a pack full of strange goods and fewer scruples than teeth.",
        "dialogue": [
            "I've all manner of wares.",
            "Care to trade?",
        ],
        # Which entry in content/merchants.yaml to use for stock/prices/restock.
        "merchant_id": "general_store",
    },

    # ---------------------------------------------------------------------------
    # Biome-Specific NPCs (for site spawning)
    # ---------------------------------------------------------------------------

    # --- Bird People (Aeries) ---
    "bird_elder": {
        "name": "Bird Elder",
        "factions": ["bird_people"],
        "base_disposition": 3,
        "glyph": "B",
        "color": (180, 200, 255),
        "description": "An ancient avian sage with feathers of silver and gold.",
        "dialogue": [
            "The winds carry many secrets, groundwalker.",
            "We watch the corruption spread from our peaks. It is... troubling.",
            "Perhaps our fates are intertwined.",
        ],
    },
    "bird_merchant": {
        "name": "Bird Merchant",
        "factions": ["bird_people"],
        "base_disposition": 5,
        "glyph": "B",
        "color": (160, 180, 220),
        "description": "A bird trader with exotic wares from the high reaches.",
        "dialogue": [
            "Feathers for bismuth, a fair trade.",
            "We have goods you won't find on the ground.",
        ],
        "merchant_id": "bird_merchant",
    },
    "bird_scout": {
        "name": "Bird Scout",
        "factions": ["bird_people"],
        "base_disposition": 4,
        "glyph": "b",
        "color": (140, 160, 200),
        "description": "A keen-eyed scout who patrols the mountain winds.",
        "dialogue": [
            "I've seen much from above. The corruption spreads faster than we thought.",
            "There are other settlements. I could mark them for you.",
        ],
    },

    # --- Fishing Village ---
    "fishmonger": {
        "name": "Fishmonger",
        "factions": ["neutral"],
        "base_disposition": 6,
        "glyph": "@",
        "color": (120, 180, 220),
        "description": "A weathered fisher with salt in their beard and tales to tell.",
        "dialogue": [
            "Fresh catch today! Well, as fresh as it gets.",
            "The waters have been strange lately. Fish swimming in circles.",
        ],
        "merchant_id": "fishmonger",
    },
    "boat_captain": {
        "name": "Boat Captain",
        "factions": ["neutral"],
        "base_disposition": 5,
        "glyph": "@",
        "color": (100, 140, 180),
        "description": "A grizzled captain with a thousand voyages behind them.",
        "dialogue": [
            "I've sailed these waters all my life.",
            "Something's wrong with the currents. The old patterns are breaking.",
        ],
    },
    "net_mender": {
        "name": "Net Mender",
        "factions": ["neutral"],
        "base_disposition": 7,
        "glyph": "@",
        "color": (140, 160, 180),
        "description": "A patient soul who repairs the village's fishing nets.",
        "dialogue": [
            "Every knot tells a story.",
            "Patterns within patterns, that's the secret to a good net.",
        ],
    },

    # --- Spriggan Grove ---
    "spriggan_elder": {
        "name": "Spriggan Elder",
        "factions": ["spriggan"],
        "base_disposition": 2,
        "glyph": "Y",
        "color": (60, 100, 50),
        "description": "An ancient tree spirit whose roots touch deep memories.",
        "dialogue": [
            "The forest... remembers...",
            "We have watched the recursion grow. It hungers.",
            "Protect the groves, and we shall aid you.",
        ],
    },
    "moss_keeper": {
        "name": "Moss Keeper",
        "factions": ["spriggan"],
        "base_disposition": 4,
        "glyph": "m",
        "color": (80, 120, 70),
        "description": "A gentle spirit who tends to the grove's mosses and fungi.",
        "dialogue": [
            "The moss speaks of distant corruption.",
            "I can offer remedies from the forest's bounty.",
        ],
        "merchant_id": "moss_keeper",
    },

    # --- Desert Nomad Camp ---
    "nomad_chief": {
        "name": "Nomad Chief",
        "factions": ["desert_nomads"],
        "base_disposition": 4,
        "glyph": "@",
        "color": (220, 200, 160),
        "description": "The weathered leader of a desert clan.",
        "dialogue": [
            "The sands shift, but we endure.",
            "You travel far from water, stranger. What do you seek?",
        ],
    },
    "caravan_merchant": {
        "name": "Caravan Merchant",
        "factions": ["desert_nomads"],
        "base_disposition": 5,
        "glyph": "@",
        "color": (200, 180, 140),
        "description": "A trader whose caravan crosses the harshest wastes.",
        "dialogue": [
            "Goods from the far oases, fresh as morning dew.",
            "Bismuth speaks louder than water out here.",
        ],
        "merchant_id": "caravan_merchant",
    },
    "water_diviner": {
        "name": "Water Diviner",
        "factions": ["desert_nomads"],
        "base_disposition": 6,
        "glyph": "@",
        "color": (180, 200, 220),
        "description": "A mystic who senses water hidden beneath the sands.",
        "dialogue": [
            "The earth whispers of hidden springs.",
            "I sense... something else beneath the sand. Something old.",
        ],
    },

    # --- Hunter Lodge ---
    "trapper": {
        "name": "Trapper",
        "factions": ["neutral"],
        "base_disposition": 5,
        "glyph": "@",
        "color": (140, 120, 100),
        "description": "A skilled hunter who knows every game trail.",
        "dialogue": [
            "The wolves are restless this season.",
            "I've set snares for game, not for travelers. You're safe here.",
        ],
    },
    "fur_trader": {
        "name": "Fur Trader",
        "factions": ["neutral"],
        "base_disposition": 6,
        "glyph": "@",
        "color": (160, 140, 120),
        "description": "A trader dealing in pelts and leather goods.",
        "dialogue": [
            "Finest furs from the taiga, keeps you warm in any cold.",
            "Looking to trade? I've got quality stock.",
        ],
        "merchant_id": "fur_trader",
    },
    "hunting_guide": {
        "name": "Hunting Guide",
        "factions": ["neutral"],
        "base_disposition": 5,
        "glyph": "@",
        "color": (130, 110, 90),
        "description": "An experienced guide who knows where the big game roams.",
        "dialogue": [
            "I can lead you to prime hunting grounds.",
            "Careful out there. The bears are fierce this time of year.",
        ],
    },

    # --- Tropical Shrine ---
    "shaman": {
        "name": "Shaman",
        "factions": ["jungle_spirits"],
        "base_disposition": 3,
        "glyph": "@",
        "color": (180, 140, 100),
        "description": "A mystic who communes with the jungle spirits.",
        "dialogue": [
            "The spirits speak of great change coming.",
            "This shrine protects against the spreading corruption.",
        ],
    },
    "spirit_speaker": {
        "name": "Spirit Speaker",
        "factions": ["jungle_spirits"],
        "base_disposition": 4,
        "glyph": "@",
        "color": (160, 120, 80),
        "description": "An interpreter between mortals and the spirit world.",
        "dialogue": [
            "The ancestors have messages for those who listen.",
            "Leave an offering, and perhaps they will guide you.",
        ],
    },
    "offering_keeper": {
        "name": "Offering Keeper",
        "factions": ["jungle_spirits"],
        "base_disposition": 5,
        "glyph": "@",
        "color": (140, 110, 70),
        "description": "A guardian who maintains the shrine's sacred offerings.",
        "dialogue": [
            "The spirits require sustenance.",
            "I can exchange your offerings for blessings.",
        ],
        "merchant_id": "offering_keeper",
    },

    # --- Corruption Outpost ---
    "cult_leader": {
        "name": "Cult Leader",
        "factions": ["corruption_cult"],
        "base_disposition": -5,
        "glyph": "@",
        "color": (150, 80, 160),
        "description": "A fanatic who embraces the spreading corruption.",
        "dialogue": [
            "You resist what cannot be resisted.",
            "Join us, and the recursion shall spare you.",
            "The old order crumbles. A new pattern emerges.",
        ],
    },
    "corrupted_merchant": {
        "name": "Corrupted Merchant",
        "factions": ["corruption_cult"],
        "base_disposition": 0,
        "glyph": "M",
        "color": (130, 70, 140),
        "description": "A trader dealing in cursed and corrupted goods.",
        "dialogue": [
            "I have... special wares. Not for the faint of heart.",
            "These items carry a price beyond bismuth.",
        ],
        "merchant_id": "corrupted_merchant",
    },
}

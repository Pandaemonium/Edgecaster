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
            "Coins are dead. Bismuth sings.",
            "Want to trade?",
        ],
        # Which entry in content/merchants.yaml to use for stock/prices/restock.
        "merchant_id": "general_store",
    },
}

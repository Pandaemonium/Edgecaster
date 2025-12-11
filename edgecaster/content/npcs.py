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
}

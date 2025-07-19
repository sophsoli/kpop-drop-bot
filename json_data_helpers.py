import json
import os
import uuid

COLLECTIONS_FILE = "collections.json"

def card_collection():
    # Load cards from the JSON file at startup
    try:
        with open("cards.json", encoding="utf-8") as data_file:
            cards = json.load(data_file)
            print("Cards loaded", cards)
            return cards
    except FileNotFoundError:
        print("cards.json file not found. Please check if it exists.")
        return []
    
def load_collections():
    if os.path.exists(COLLECTIONS_FILE):
        with open(COLLECTIONS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_collections(user_collections):
    with open(COLLECTIONS_FILE, 'w') as f:
        json.dump(user_collections, f, indent=4)

def ensure_card_ids(collections):
    changed = False
    for user_id, cards in collections.items():
        for card in cards:
            if "id" not in card:
                card["id"] = str(uuid.uuid4())
                changed = True
    if changed:
        save_collections(collections)
    return collections
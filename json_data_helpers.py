import json

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
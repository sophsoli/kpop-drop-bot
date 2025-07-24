import json
import os

def read_entries(file):
    if os.path.exists(file):
        with open(file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def add_entry(file, entry):
    data = read_entries(file)
    data.insert(0, entry)
    with open(file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)
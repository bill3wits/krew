"""krew-kanban-lean: thinning keeps every name, type, enum and `required`; only prose shortens, marked."""
from __future__ import annotations

from conftest import load_plugin

SCHEMA = {"name": "kanban_block", "description": "Block the card. " + "More words. " * 40,
          "parameters": {"type": "object", "required": ["reason"],
                         "properties": {"reason": {"type": "string", "description": "Why. " + "Long. " * 50},
                                        "kind": {"type": "string", "enum": ["dependency", "needs_input"],
                                                 "description": "Which kind."}}}}


def test_thin_keeps_structure_and_shortens_prose():
    m = load_plugin("krew-kanban-lean")
    t = m.thin(SCHEMA)
    assert t["name"] == "kanban_block" and t["parameters"]["required"] == ["reason"]
    assert t["parameters"]["properties"]["kind"]["enum"] == ["dependency", "needs_input"]
    assert t["description"] == "Block the card."
    assert SCHEMA["description"].startswith("Block the card. More")       # the original is not mutated


def test_a_cut_is_marked():
    m = load_plugin("krew-kanban-lean")
    long_sentence = "word " * 100
    assert m._first_sentence(long_sentence, 50).endswith("…")

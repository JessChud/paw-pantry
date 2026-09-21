import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_generated_inventory_is_current_unique_and_broad():
    spec = importlib.util.spec_from_file_location(
        "build_shopping_inventory", ROOT / "scripts" / "build_shopping_inventory.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    generated = module.build()
    committed = json.loads((ROOT / "data" / "shopping_intents.json").read_text())
    assert committed == generated
    assert len(committed) == 2771
    assert len({row["id"] for row in committed}) == len(committed)
    assert len({row["species"] for row in committed}) == 20
    assert len({row["category"] for row in committed}) == 23
    assert all(row["base_intent_id"] for row in committed)

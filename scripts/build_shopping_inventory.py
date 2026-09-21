"""Build the broad, non-SKU shopping-intent library deterministically.

The source seeds are reviewed product types.  Variant records represent useful
shopping constraints (size, format, cleaning, travel, and similar qualifiers),
not products that Paw Pantry claims to stock, test, or medically recommend.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEEDS_PATH = ROOT / "data" / "shopping_intent_seeds.json"
OUTPUT_PATH = ROOT / "data" / "shopping_intents.json"


SAFE_DEFAULT = (
    "Compare the exact size, materials, cleaning instructions, compatibility, "
    "and current retailer details before choosing."
)

CATEGORY_GUIDANCE = {
    "food": "Check the species and life-stage label, ingredients, feeding directions, and package size. Ask a veterinarian about diet changes, allergies, or medical needs.",
    "treats": "Check the species and life-stage label, ingredients, calories, serving guidance, and package size. Ask a veterinarian about allergies or medical needs.",
    "supplements": "Use only a species-appropriate product after checking the label with a veterinarian; product listings do not establish need, dose, or suitability.",
    "flea-tick": "Confirm the exact species, age, weight range, active ingredients, and directions with a veterinarian before use. Never substitute a dog product for a cat product or another species.",
    "toys": "Choose an appropriate size and material, supervise use, and replace damaged items.",
    "habitat": "Confirm species requirements, usable dimensions, ventilation, temperature or humidity needs, and cleaning instructions before choosing.",
    "water-care": "Check compatibility with the animal, habitat volume, equipment, and current manufacturer directions before use.",
    "heating-lighting": "Confirm species-specific temperature, UV, distance, fixture, thermostat, and replacement requirements with a qualified veterinarian or experienced keeper.",
    "feeding": "Compare capacity, materials, cleaning instructions, stability, and compatibility with the animal and feeding routine.",
    "grooming": "Check species, coat or skin type, size, instructions, and cleaning requirements. Stop if the animal shows discomfort and ask a veterinarian about skin or nail concerns.",
    "carriers": "Measure the animal and check ventilation, usable dimensions, closures, weight limits, cleaning instructions, and current travel rules.",
    "leashes": "Check fit, adjustment range, hardware, intended use, and inspection instructions before every use.",
    "beds": "Compare usable sleeping dimensions, entry height, materials, cleaning instructions, and the animal's normal resting position.",
    "litter": "Check species compatibility, materials, dust, cleaning routine, package amount, and disposal instructions.",
    "bedding": "Check species compatibility, materials, dust, absorbency, package amount, and replacement guidance.",
    "cleaning": "Check surface compatibility, ingredients, dilution or use directions, ventilation, drying, storage, and whether animals must be kept away during use.",
    "travel": "Check the animal's measurements, trip type, current carrier rules, capacity, cleaning, and compatibility before travel.",
    "safety": "Check the product's intended species, fit or capacity, instructions, replacement schedule, and emergency limitations.",
}

# Variants expand natural-language coverage without inventing exact products,
# prices, ratings, availability, or hands-on testing.
CATEGORY_VARIANTS = {
    "food": [
        ("small-package option", ["small", "package", "trial", "size"]),
        ("bulk-package option", ["bulk", "large", "package", "stock up"]),
        ("resealable-package option", ["resealable", "freshness", "storage"]),
        ("travel-size option", ["travel", "portable", "portion"]),
        ("alternative-protein option", ["alternative", "protein", "recipe"]),
        ("limited-ingredient option", ["limited", "ingredient", "simple", "recipe"]),
        ("age-specific option", ["life stage", "young", "adult", "senior"]),
        ("sensitive-digestion option", ["sensitive", "digestion", "stomach"]),
    ],
    "treats": [
        ("small-piece option", ["small", "pieces", "training", "bite"]),
        ("soft-texture option", ["soft", "chewy", "texture"]),
        ("crunchy-texture option", ["crunchy", "crisp", "texture"]),
        ("single-ingredient option", ["single", "ingredient", "simple"]),
        ("lower-calorie option", ["low", "calorie", "portion"]),
        ("resealable-pouch option", ["resealable", "pouch", "freshness"]),
        ("bulk-pack option", ["bulk", "multipack", "stock up"]),
    ],
    "toys": [
        ("small-size option", ["small", "compact", "size"]),
        ("large-size option", ["large", "big", "size"]),
        ("durable option", ["durable", "tough", "heavy duty"]),
        ("washable option", ["washable", "easy clean"]),
        ("interactive option", ["interactive", "enrichment", "play"]),
        ("quiet indoor option", ["quiet", "indoor", "apartment"]),
        ("outdoor option", ["outdoor", "yard", "weather"]),
        ("multipack option", ["multipack", "variety", "set"]),
    ],
    "habitat": [
        ("compact-space option", ["compact", "small space"]),
        ("larger-size option", ["large", "roomy", "size"]),
        ("easy-clean option", ["easy clean", "washable", "maintenance"]),
        ("modular option", ["modular", "expandable", "connect"]),
        ("portable option", ["portable", "temporary", "travel"]),
        ("replacement-part option", ["replacement", "part", "compatible"]),
    ],
    "heating-lighting": [
        ("thermostat-compatible option", ["thermostat", "control", "compatible"]),
        ("dimmable option", ["dimmable", "control", "fixture"]),
        ("replacement option", ["replacement", "bulb", "element"]),
        ("compact-fixture option", ["compact", "fixture", "small"]),
        ("larger-habitat option", ["large", "habitat", "coverage"]),
    ],
    "carriers": [
        ("small-size option", ["small", "compact", "size"]),
        ("large-size option", ["large", "roomy", "size"]),
        ("soft-sided option", ["soft sided", "fabric", "lightweight"]),
        ("hard-sided option", ["hard sided", "rigid", "shell"]),
        ("collapsible option", ["collapsible", "folding", "storage"]),
        ("car-travel option", ["car", "vehicle", "travel"]),
        ("air-travel research option", ["airline", "flight", "travel"]),
        ("easy-clean option", ["easy clean", "washable", "liner"]),
    ],
    "grooming": [
        ("small-size option", ["small", "compact", "size"]),
        ("large-size option", ["large", "wide", "size"]),
        ("sensitive-skin option", ["sensitive", "skin", "gentle"]),
        ("long-coat option", ["long", "coat", "fur"]),
        ("short-coat option", ["short", "coat", "fur"]),
        ("easy-clean option", ["easy clean", "washable"]),
        ("travel option", ["travel", "portable", "compact"]),
    ],
    "feeding": [
        ("small-capacity option", ["small", "capacity", "portion"]),
        ("large-capacity option", ["large", "capacity", "multi pet"]),
        ("non-slip option", ["non slip", "stable", "base"]),
        ("dishwasher-safe option", ["dishwasher", "easy clean"]),
        ("travel option", ["travel", "portable", "collapsible"]),
        ("automatic option", ["automatic", "timer", "scheduled"]),
        ("replacement-part option", ["replacement", "filter", "part"]),
    ],
    "bedding": [
        ("small-package option", ["small", "package", "trial"]),
        ("bulk-package option", ["bulk", "large", "package"]),
        ("low-dust option", ["low dust", "dust free"]),
        ("high-absorbency option", ["absorbent", "moisture", "odor"]),
        ("unscented option", ["unscented", "fragrance free"]),
        ("easy-clean option", ["easy clean", "replace", "liner"]),
    ],
    "litter": [
        ("small-package option", ["small", "package", "trial"]),
        ("bulk-package option", ["bulk", "large", "package"]),
        ("low-dust option", ["low dust", "dust free"]),
        ("unscented option", ["unscented", "fragrance free"]),
        ("odor-control option", ["odor", "control", "multi pet"]),
        ("easy-clean option", ["easy clean", "maintenance"]),
    ],
    "beds": [
        ("small-size option", ["small", "compact", "size"]),
        ("large-size option", ["large", "roomy", "size"]),
        ("washable-cover option", ["washable", "removable", "cover"]),
        ("travel option", ["travel", "portable", "folding"]),
        ("cooling option", ["cooling", "summer", "warm weather"]),
        ("warming option", ["warming", "heated", "cold weather"]),
        ("raised option", ["raised", "elevated", "airflow"]),
        ("non-slip option", ["non slip", "stable", "base"]),
    ],
    "cleaning": [
        ("fragrance-free option", ["fragrance free", "unscented"]),
        ("concentrated option", ["concentrated", "dilute", "refill"]),
        ("ready-to-use option", ["ready to use", "spray"]),
        ("carpet-use option", ["carpet", "rug", "fabric"]),
        ("hard-floor option", ["hard floor", "tile", "sealed"]),
        ("upholstery-use option", ["upholstery", "couch", "fabric"]),
        ("travel-size option", ["travel", "portable", "small"]),
    ],
    "travel": [
        ("compact option", ["compact", "packable", "small"]),
        ("large-capacity option", ["large", "capacity", "long trip"]),
        ("car-travel option", ["car", "vehicle", "road trip"]),
        ("air-travel research option", ["airline", "flight", "travel"]),
        ("easy-clean option", ["washable", "easy clean"]),
        ("emergency-kit option", ["emergency", "evacuation", "kit"]),
    ],
    "leashes": [
        ("small-size option", ["small", "lightweight", "size"]),
        ("large-size option", ["large", "heavy duty", "size"]),
        ("reflective option", ["reflective", "night", "visibility"]),
        ("hands-free option", ["hands free", "waist", "running"]),
        ("padded option", ["padded", "comfort", "handle"]),
        ("water-resistant option", ["water resistant", "rain", "outdoor"]),
    ],
    "water-care": [
        ("small-habitat option", ["small", "tank", "volume"]),
        ("large-habitat option", ["large", "tank", "volume"]),
        ("refill option", ["refill", "bulk", "concentrated"]),
        ("freshwater option", ["freshwater", "water"]),
        ("saltwater option", ["saltwater", "marine", "water"]),
    ],
    "safety": [
        ("compact option", ["compact", "portable", "small"]),
        ("large-capacity option", ["large", "capacity"]),
        ("travel option", ["travel", "car", "portable"]),
        ("replacement option", ["replacement", "refill", "part"]),
        ("high-visibility option", ["visible", "reflective", "bright"]),
    ],
}

DEFAULT_VARIANTS = [
    ("compact option", ["compact", "small", "space"]),
    ("larger-size option", ["large", "capacity", "size"]),
    ("easy-clean option", ["easy clean", "washable", "maintenance"]),
    ("travel option", ["travel", "portable"]),
    ("replacement or refill option", ["replacement", "refill", "part"]),
]


# Additional companion-animal product types that were missing from the first
# dog/cat-centered library.  These are search concepts, not live stock records.
ADDITIONAL_CONCEPTS = {
    "ferret": [
        ("Ferret food", "food", "ferret kibble complete diet"),
        ("Ferret treats", "treats", "ferret reward snack"),
        ("Ferret cage", "habitat", "ferret enclosure multi level cage"),
        ("Ferret playpen", "habitat", "ferret exercise pen play yard"),
        ("Ferret hammock", "beds", "ferret hammock hanging bed"),
        ("Ferret sleep sack", "beds", "ferret sleep sack cave bed"),
        ("Ferret tunnel", "toys", "ferret tunnel play tube"),
        ("Ferret ball toy", "toys", "ferret ball enrichment toy"),
        ("Ferret litter", "litter", "ferret litter low dust"),
        ("Ferret litter pan", "litter", "ferret corner litter box pan"),
        ("Ferret bedding", "bedding", "ferret washable bedding liner"),
        ("Ferret carrier", "carriers", "ferret travel carrier"),
        ("Ferret harness", "leashes", "ferret harness leash"),
        ("Ferret food bowl", "feeding", "ferret food bowl lock cage"),
        ("Ferret water bottle", "feeding", "ferret water bottle cage"),
        ("Ferret grooming brush", "grooming", "ferret brush grooming"),
        ("Ferret nail clippers", "grooming", "ferret nail clipper"),
        ("Ferret shampoo", "grooming", "ferret shampoo bath"),
        ("Ferret enzyme cleaner", "cleaning", "ferret stain odor cleaner"),
        ("Ferret cage liner", "bedding", "ferret cage liner washable"),
    ],
    "gerbil": [
        ("Gerbil food", "food", "gerbil pellets seed food"),
        ("Gerbil treats", "treats", "gerbil treat snack"),
        ("Gerbil tank enclosure", "habitat", "gerbil glass tank enclosure"),
        ("Gerbil enclosure topper", "habitat", "gerbil tank topper cage"),
        ("Gerbil paper bedding", "bedding", "gerbil paper bedding burrow"),
        ("Gerbil nesting material", "bedding", "gerbil nesting burrow material"),
        ("Gerbil exercise wheel", "toys", "gerbil solid wheel exercise"),
        ("Gerbil sand bath", "grooming", "gerbil sand bath container"),
        ("Gerbil chew toy", "toys", "gerbil chew wood toy"),
        ("Gerbil tunnel", "toys", "gerbil tunnel burrow tube"),
        ("Gerbil hideout", "habitat", "gerbil hide house"),
        ("Gerbil water bottle", "feeding", "gerbil water bottle"),
        ("Gerbil food dish", "feeding", "gerbil ceramic food bowl"),
        ("Gerbil carrier", "carriers", "gerbil travel carrier"),
        ("Gerbil cleaning scoop", "cleaning", "gerbil bedding litter scoop"),
    ],
    "rat": [
        ("Rat food", "food", "rat pellets blocks complete food"),
        ("Rat treats", "treats", "rat training treat snack"),
        ("Rat cage", "habitat", "rat enclosure multi level cage"),
        ("Rat playpen", "habitat", "rat playpen exercise pen"),
        ("Rat bedding", "bedding", "rat paper bedding low dust"),
        ("Rat cage liner", "bedding", "rat washable cage liner"),
        ("Rat hammock", "beds", "rat hammock hanging bed"),
        ("Rat hideout", "habitat", "rat hide house shelter"),
        ("Rat tunnel", "toys", "rat tunnel tube toy"),
        ("Rat foraging toy", "toys", "rat foraging puzzle enrichment"),
        ("Rat chew toy", "toys", "rat chew toy wood"),
        ("Rat exercise wheel", "toys", "rat large solid wheel"),
        ("Rat water bottle", "feeding", "rat water bottle cage"),
        ("Rat food bowl", "feeding", "rat food bowl ceramic"),
        ("Rat carrier", "carriers", "rat travel carrier"),
        ("Rat harness", "leashes", "rat harness leash"),
        ("Rat grooming brush", "grooming", "rat brush grooming"),
        ("Rat nail trimmer", "grooming", "rat nail trimmer"),
        ("Rat cage cleaner", "cleaning", "rat cage cleaner fragrance free"),
        ("Rat litter pan", "litter", "rat litter box corner pan"),
    ],
    "mouse": [
        ("Mouse food", "food", "mouse pellets blocks complete food"),
        ("Mouse treats", "treats", "mouse treat snack"),
        ("Mouse enclosure", "habitat", "mouse cage tank enclosure"),
        ("Mouse bedding", "bedding", "mouse paper bedding burrow"),
        ("Mouse nesting material", "bedding", "mouse nesting material"),
        ("Mouse exercise wheel", "toys", "mouse solid wheel exercise"),
        ("Mouse hideout", "habitat", "mouse hide house"),
        ("Mouse tunnel", "toys", "mouse tunnel tube"),
        ("Mouse chew toy", "toys", "mouse chew toy wood"),
        ("Mouse foraging toy", "toys", "mouse foraging puzzle enrichment"),
        ("Mouse water bottle", "feeding", "mouse water bottle"),
        ("Mouse food dish", "feeding", "mouse ceramic food bowl"),
        ("Mouse carrier", "carriers", "mouse travel carrier"),
        ("Mouse cleaning scoop", "cleaning", "mouse bedding litter scoop"),
        ("Mouse playpen", "habitat", "mouse playpen exercise enclosure"),
    ],
    "chinchilla": [
        ("Chinchilla hay", "food", "chinchilla timothy hay"),
        ("Chinchilla pellets", "food", "chinchilla pellets food"),
        ("Chinchilla treats", "treats", "chinchilla treat snack"),
        ("Chinchilla cage", "habitat", "chinchilla enclosure multi level cage"),
        ("Chinchilla ledge", "habitat", "chinchilla cage ledge shelf"),
        ("Chinchilla hideout", "habitat", "chinchilla hide house"),
        ("Chinchilla bedding", "bedding", "chinchilla bedding low dust"),
        ("Chinchilla fleece liner", "bedding", "chinchilla fleece cage liner"),
        ("Chinchilla dust bath", "grooming", "chinchilla dust bath container"),
        ("Chinchilla cooling stone", "habitat", "chinchilla cooling stone slab"),
        ("Chinchilla chew toy", "toys", "chinchilla chew toy wood"),
        ("Chinchilla exercise wheel", "toys", "chinchilla large solid wheel"),
        ("Chinchilla hay feeder", "feeding", "chinchilla hay rack feeder"),
        ("Chinchilla water bottle", "feeding", "chinchilla water bottle"),
        ("Chinchilla food bowl", "feeding", "chinchilla ceramic bowl"),
        ("Chinchilla carrier", "carriers", "chinchilla travel carrier"),
        ("Chinchilla grooming comb", "grooming", "chinchilla grooming comb"),
        ("Chinchilla cage cleaner", "cleaning", "chinchilla cage cleaner"),
    ],
    "hedgehog": [
        ("Hedgehog food", "food", "hedgehog food kibble"),
        ("Hedgehog treats", "treats", "hedgehog treat snack"),
        ("Hedgehog enclosure", "habitat", "hedgehog cage enclosure"),
        ("Hedgehog bedding", "bedding", "hedgehog paper bedding"),
        ("Hedgehog fleece liner", "bedding", "hedgehog fleece cage liner"),
        ("Hedgehog exercise wheel", "toys", "hedgehog solid wheel exercise"),
        ("Hedgehog hideout", "habitat", "hedgehog hide house"),
        ("Hedgehog sleep sack", "beds", "hedgehog sleep sack cuddle pouch"),
        ("Hedgehog tunnel", "toys", "hedgehog tunnel tube"),
        ("Hedgehog food bowl", "feeding", "hedgehog food bowl"),
        ("Hedgehog water bowl", "feeding", "hedgehog water bowl"),
        ("Hedgehog carrier", "carriers", "hedgehog travel carrier"),
        ("Hedgehog bath brush", "grooming", "hedgehog soft bath brush"),
        ("Hedgehog nail clippers", "grooming", "hedgehog nail clipper"),
        ("Hedgehog thermometer", "habitat", "hedgehog enclosure thermometer"),
        ("Hedgehog thermostat", "heating-lighting", "hedgehog thermostat heat control"),
    ],
    "turtle": [
        ("Aquatic turtle pellets", "food", "aquatic turtle pellets food"),
        ("Tortoise pellets", "food", "tortoise pellets food"),
        ("Turtle treat", "treats", "turtle treat food"),
        ("Aquatic turtle tank", "habitat", "aquatic turtle aquarium tank"),
        ("Tortoise enclosure", "habitat", "tortoise table enclosure"),
        ("Turtle tank filter", "water-care", "turtle aquarium filter"),
        ("Turtle basking platform", "habitat", "turtle basking dock platform"),
        ("Turtle UVB light", "heating-lighting", "turtle uvb lamp light"),
        ("Turtle basking heat lamp", "heating-lighting", "turtle basking heat lamp"),
        ("Turtle water heater", "heating-lighting", "turtle aquarium heater"),
        ("Turtle thermometer", "habitat", "turtle tank thermometer"),
        ("Turtle water conditioner", "water-care", "turtle aquarium water conditioner"),
        ("Turtle water test kit", "water-care", "turtle aquarium test kit"),
        ("Turtle feeding tongs", "feeding", "turtle feeding tongs"),
        ("Turtle tank cleaning tool", "cleaning", "turtle aquarium cleaning tool"),
        ("Turtle carrier", "carriers", "turtle transport carrier"),
        ("Turtle hide", "habitat", "turtle tank hide shelter"),
        ("Tortoise water dish", "feeding", "tortoise shallow water dish"),
    ],
    "amphibian": [
        ("Amphibian food", "food", "frog newt salamander food"),
        ("Amphibian terrarium", "habitat", "frog newt salamander terrarium"),
        ("Amphibian substrate", "habitat", "frog terrarium substrate"),
        ("Amphibian moss", "habitat", "terrarium moss humidity"),
        ("Amphibian hide", "habitat", "frog hide shelter"),
        ("Amphibian water dish", "feeding", "frog water dish"),
        ("Terrarium mister", "habitat", "terrarium mister spray humidity"),
        ("Automatic misting system", "habitat", "automatic terrarium misting system"),
        ("Terrarium hygrometer", "habitat", "terrarium humidity gauge hygrometer"),
        ("Terrarium thermometer", "habitat", "terrarium digital thermometer"),
        ("Amphibian-safe water conditioner", "water-care", "amphibian water conditioner"),
        ("Amphibian feeding tongs", "feeding", "frog feeding tongs"),
        ("Terrarium drainage layer", "habitat", "terrarium drainage layer"),
        ("Terrarium plant light", "heating-lighting", "terrarium plant light fixture"),
        ("Amphibian carrier", "carriers", "frog salamander transport carrier"),
        ("Terrarium cleaning tool", "cleaning", "terrarium cleaning tool"),
    ],
    "hermit-crab": [
        ("Hermit crab food", "food", "hermit crab food pellets"),
        ("Hermit crab treat", "treats", "hermit crab treat food"),
        ("Hermit crab tank", "habitat", "hermit crab glass tank enclosure"),
        ("Hermit crab substrate", "habitat", "hermit crab sand fiber substrate"),
        ("Hermit crab saltwater mix", "water-care", "hermit crab marine salt water mix"),
        ("Hermit crab water conditioner", "water-care", "hermit crab water conditioner"),
        ("Hermit crab pool", "feeding", "hermit crab water pool dish"),
        ("Hermit crab food dish", "feeding", "hermit crab food bowl"),
        ("Hermit crab spare shells", "habitat", "hermit crab natural spare shells"),
        ("Hermit crab hygrometer", "habitat", "hermit crab humidity gauge"),
        ("Hermit crab thermometer", "habitat", "hermit crab thermometer"),
        ("Hermit crab thermostat", "heating-lighting", "hermit crab thermostat heat control"),
        ("Hermit crab hideout", "habitat", "hermit crab hide cave"),
        ("Hermit crab climbing toy", "toys", "hermit crab climbing enrichment"),
        ("Hermit crab tank cleaner", "cleaning", "hermit crab tank cleaning tool"),
        ("Hermit crab carrier", "carriers", "hermit crab transport container"),
    ],
    "snake": [
        ("Species-appropriate snake food", "food", "snake food frozen feeder species appropriate"),
        ("Snake enclosure", "habitat", "snake terrarium enclosure"),
        ("Snake substrate", "habitat", "snake bedding substrate"),
        ("Snake hide", "habitat", "snake hide cave shelter"),
        ("Snake water bowl", "feeding", "snake water dish bowl"),
        ("Snake climbing branch", "habitat", "snake climbing branch decor"),
        ("Snake heat lamp", "heating-lighting", "snake heat lamp fixture"),
        ("Snake heat mat", "heating-lighting", "snake under tank heat mat"),
        ("Snake thermostat", "heating-lighting", "snake thermostat temperature controller"),
        ("Snake thermometer-hygrometer", "habitat", "snake thermometer hygrometer"),
        ("Snake feeding tongs", "feeding", "snake feeding tongs forceps"),
        ("Snake handling hook", "safety", "snake handling hook"),
        ("Snake enclosure lock", "safety", "snake terrarium lock clip"),
        ("Snake carrier", "carriers", "snake transport carrier"),
        ("Snake enclosure cleaner", "cleaning", "snake terrarium cleaner"),
    ],
    "lizard": [
        ("Species-appropriate lizard food", "food", "lizard food species appropriate"),
        ("Lizard enclosure", "habitat", "lizard terrarium enclosure"),
        ("Lizard substrate", "habitat", "lizard bedding substrate"),
        ("Lizard hide", "habitat", "lizard hide cave shelter"),
        ("Lizard water dish", "feeding", "lizard water bowl dish"),
        ("Lizard food dish", "feeding", "lizard food bowl"),
        ("Lizard climbing branch", "habitat", "lizard climbing branch decor"),
        ("Lizard basking platform", "habitat", "lizard basking rock platform"),
        ("Lizard UVB light", "heating-lighting", "lizard uvb lamp light"),
        ("Lizard heat lamp", "heating-lighting", "lizard basking heat lamp"),
        ("Lizard thermostat", "heating-lighting", "lizard thermostat controller"),
        ("Lizard thermometer-hygrometer", "habitat", "lizard thermometer hygrometer"),
        ("Lizard mister", "habitat", "lizard terrarium mister"),
        ("Lizard feeding tongs", "feeding", "lizard feeding tongs"),
        ("Lizard carrier", "carriers", "lizard travel carrier"),
        ("Lizard terrarium cleaner", "cleaning", "lizard terrarium cleaning tool"),
    ],
}


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def additional_seeds() -> list[dict]:
    rows = []
    for species, concepts in ADDITIONAL_CONCEPTS.items():
        for title, category, keyword_text in concepts:
            rows.append({
                "id": f"{species}-{slug(title)}",
                "title": title,
                "species": species,
                "category": category,
                "keywords": keyword_text.split(),
                "guidance": CATEGORY_GUIDANCE.get(category, SAFE_DEFAULT),
            })
    return rows


def build() -> list[dict]:
    seeds = json.loads(SEEDS_PATH.read_text()) + additional_seeds()
    output = []
    seen = set()
    for seed in seeds:
        base_id = seed["id"]
        base = {**seed, "base_intent_id": base_id, "variant_label": None}
        if base_id in seen:
            raise ValueError(f"Duplicate seed ID: {base_id}")
        output.append(base)
        seen.add(base_id)
        variants = CATEGORY_VARIANTS.get(seed["category"], DEFAULT_VARIANTS)
        for label, extra_keywords in variants:
            label_terms = set(re.findall(r"[a-z0-9]+", label.lower())) - {
                "option", "use", "size", "package", "capacity"}
            title_terms = set(re.findall(r"[a-z0-9]+", seed["title"].lower()))
            # Do not create awkward duplicates such as "Durable chew toy —
            # durable option" or "Hands-free leash — hands-free option".
            if label_terms and label_terms <= title_terms:
                continue
            row_id = f"{base_id}--{slug(label)}"
            if row_id in seen:
                raise ValueError(f"Duplicate generated ID: {row_id}")
            output.append({
                **seed,
                "id": row_id,
                "title": f"{seed['title']} — {label}",
                "keywords": list(dict.fromkeys(seed["keywords"] + extra_keywords)),
                "base_intent_id": base_id,
                "variant_label": label,
            })
            seen.add(row_id)
    return output


if __name__ == "__main__":
    rows = build()
    OUTPUT_PATH.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    species = len({row["species"] for row in rows})
    categories = len({row["category"] for row in rows})
    print(f"Wrote {len(rows)} searchable shopping intents across {species} pet types and {categories} categories.")

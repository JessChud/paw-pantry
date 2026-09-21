"""Paw Pantry API — pet replenishment backend (zero-ops: affiliate model).

Serves pet profiles, a curated product catalog mapped to affiliate links,
run-out predictions, and the hosted privacy policy + terms pages.
"""
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import date, timedelta
from functools import lru_cache
from hmac import compare_digest
from html import escape
from math import ceil
from urllib.parse import parse_qs, urlencode, urlparse
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.openapi.utils import get_openapi
from fastapi.security import APIKeyHeader
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker
from affiliate_links import valid_chewy_link
from semantic_search import SemanticRanker

BASE_DIR = Path(__file__).parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/pawpantry.db")
API_KEY = os.getenv("PAW_PANTRY_API_KEY", "")
MUSE_CONNECTOR_API_KEY = os.getenv("MUSE_CONNECTOR_API_KEY", "")
if not API_KEY or API_KEY == "dev-key-change-me":
    raise RuntimeError("Set a private PAW_PANTRY_API_KEY before starting Paw Pantry.")
if MUSE_CONNECTOR_API_KEY and compare_digest(
        MUSE_CONNECTOR_API_KEY.encode(), API_KEY.encode()):
    raise RuntimeError(
        "MUSE_CONNECTOR_API_KEY must differ from PAW_PANTRY_API_KEY so the "
        "connector cannot access private pet records."
    )

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()
SEMANTIC_RANKER = SemanticRanker()


# ---------------- models ----------------
class Pet(Base):
    __tablename__ = "pets"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    species = Column(String, nullable=False)  # dog, cat, fish, rabbit, hamster...
    breed = Column(String, default="")
    age_years = Column(Float, default=0)
    weight_lb = Column(Float, default=0)
    allergies = Column(String, default="")  # comma-separated
    notes = Column(String, default="")
    supplies = relationship("PetSupply", back_populates="pet", cascade="all, delete-orphan")


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    brand = Column(String, default="")
    species = Column(String, default="dog")
    category = Column(String, default="food")  # food, treats, litter, toys, supplies
    package_size = Column(String, default="")
    chewy_url = Column(String, default="")
    amazon_url = Column(String, default="")
    notes = Column(String, default="")


class PetSupply(Base):
    __tablename__ = "pet_supplies"
    id = Column(Integer, primary_key=True)
    pet_id = Column(Integer, ForeignKey("pets.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    last_purchased = Column(Date, default=date.today)
    package_amount = Column(Float, default=0)  # e.g. 80 (oz in the bag)
    daily_use = Column(Float, default=1)       # e.g. 2.5 oz/day
    unit = Column(String, default="oz")
    pet = relationship("Pet", back_populates="supplies")
    product = relationship("Product")


# ---------------- schemas ----------------
class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class PetCreate(InputModel):
    name: str = Field(min_length=1, max_length=120)
    species: str = Field(min_length=1, max_length=60)
    breed: str = Field(default="", max_length=200)
    age_years: float = Field(default=0, ge=0, le=300)
    weight_lb: float = Field(default=0, ge=0, le=100000)
    allergies: str = Field(default="", max_length=1000)
    notes: str = Field(default="", max_length=3000)

    @field_validator("species")
    @classmethod
    def normalize_species(cls, value):
        return value.lower()


class PetUpdate(InputModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    species: Optional[str] = Field(default=None, min_length=1, max_length=60)
    breed: Optional[str] = Field(default=None, max_length=200)
    age_years: Optional[float] = Field(default=None, ge=0, le=300)
    weight_lb: Optional[float] = Field(default=None, ge=0, le=100000)
    allergies: Optional[str] = Field(default=None, max_length=1000)
    notes: Optional[str] = Field(default=None, max_length=3000)

    @field_validator("*")
    @classmethod
    def reject_null(cls, value, info):
        if value is None:
            raise ValueError("Omit an unchanged field instead of sending null.")
        return value.lower() if info.field_name == "species" else value


class SupplyCreate(InputModel):
    product_id: int = Field(gt=0)
    last_purchased: date = Field(default_factory=date.today)
    package_amount: float = Field(gt=0, le=1000000)
    daily_use: float = Field(gt=0, le=1000000)
    unit: str = Field(default="oz", min_length=1, max_length=24)

    @field_validator("last_purchased")
    @classmethod
    def valid_date(cls, value):
        if not date(1900, 1, 1) <= value <= date.today():
            raise ValueError("Use a purchase date from 1900 through today.")
        return value

    @model_validator(mode="after")
    def reasonable_duration(self):
        if self.package_amount / self.daily_use > 36500:
            raise ValueError("Check the amounts and units: supply cannot exceed 100 years.")
        return self


class RetailerOption(BaseModel):
    """A user-initiated external retailer action that a connector may render."""

    retailer: Literal["amazon", "chewy"]
    destination: str
    button_label: str
    url: str
    disclosure: str
    kind: Literal["product", "search"] = "product"
    affiliate: bool = True
    opens_after_user_click: bool = True
    rel: str = "sponsored nofollow noopener"
    source_page_url: Optional[str] = None


class CatalogProduct(BaseModel):
    id: int
    name: str
    brand: str
    species: str
    category: str
    package_size: str
    notes: str
    website_url: Optional[str]
    catalog_status: Literal["active", "retired"]
    replacement_product_id: Optional[int]
    amazon_link_available: bool
    verified_amazon_product_link: bool
    affiliate_search_available: bool
    chewy_link_available: bool
    retailer_options: list[RetailerOption]


class ProductLinkResult(BaseModel):
    product: CatalogProduct
    retailer: Literal["amazon", "chewy"]
    destination: str
    button_label: str
    url: str
    disclosure: str
    kind: Literal["product", "search"] = "product"
    affiliate: bool = True
    opens_after_user_click: bool = True
    rel: str = "sponsored nofollow noopener"
    source_page_url: Optional[str] = None


class InventoryConcept(BaseModel):
    id: str
    title: str
    species: str
    category: str
    keywords: list[str]
    guidance: str
    base_intent_id: str
    variant_label: Optional[str] = None


class InventoryListing(InventoryConcept):
    website_url: str
    retailer_options: list[RetailerOption]


class ShoppingOptionsResult(BaseModel):
    query: str
    curated_products: list[CatalogProduct]
    matched_inventory: list[InventoryListing]
    broader_amazon_search: RetailerOption
    matching_method: Literal["keyword", "hybrid-semantic"]
    semantic_model: Optional[str]
    guidance: str


class CatalogStats(BaseModel):
    active_curated_products: int
    retired_products: int
    shopping_intents: int
    verified_amazon_products: int
    affiliate_enabled_active_products: int
    affiliate_enabled_intents: int
    verified_chewy_products: int
    species_counts: dict[str, int]
    category_counts: dict[str, int]
    shopping_species_counts: dict[str, int]
    shopping_category_counts: dict[str, int]
    broader_amazon_search_enabled: bool
    first_party_recommendation_pages: int
    semantic_search_enabled: bool
    semantic_model: Optional[str]


class RefillEstimateRequest(InputModel):
    purchase_date: date
    package_amount: float = Field(gt=0, le=1000000)
    daily_use: float = Field(gt=0, le=1000000)
    unit: str = Field(min_length=1, max_length=24)
    reorder_lead_days: int = Field(default=5, ge=0, le=365)

    @field_validator("purchase_date")
    @classmethod
    def valid_purchase_date(cls, value):
        if not date(1900, 1, 1) <= value <= date.today():
            raise ValueError("Use a purchase date from 1900 through today.")
        return value

    @model_validator(mode="after")
    def reasonable_duration(self):
        if self.package_amount / self.daily_use > 36500:
            raise ValueError("Check the amounts and units: supply cannot exceed 100 years.")
        return self


class RefillEstimateResult(BaseModel):
    days_total: float
    days_left: float
    runs_out: date
    reorder_by: date
    overdue: bool
    unit: str
    guidance: str


@asynccontextmanager
async def lifespan(application):
    seed()
    yield
    engine.dispose()


app = FastAPI(
    title="Paw Pantry Connector API", version="0.12.0", lifespan=lifespan,
    description="Stateless pet-supply search and refill estimates for Muse. "
                "The connector cannot read or write Paw Pantry's private pet workspace. "
                "Retailer actions open only after the user chooses them, and the supplied "
                "affiliate disclosure must be displayed. No purchase is made by this API."
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_key(x_api_key: Optional[str] = Depends(api_key_header)):
    if not x_api_key or not compare_digest(x_api_key.encode(), API_KEY.encode()):
        raise HTTPException(status_code=401, detail="invalid api key")


def require_connector_key(x_api_key: Optional[str] = Depends(api_key_header)):
    """Accept the connector credential without granting access to private pet records."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="invalid api key")
    supplied = x_api_key.encode()
    valid_owner = compare_digest(supplied, API_KEY.encode())
    valid_connector = bool(MUSE_CONNECTOR_API_KEY) and compare_digest(
        supplied, MUSE_CONNECTOR_API_KEY.encode())
    if not (valid_owner or valid_connector):
        raise HTTPException(status_code=401, detail="invalid api key")


def pet_to_dict(pet: Pet) -> dict:
    return {
        "id": pet.id, "name": pet.name, "species": pet.species,
        "breed": pet.breed, "age_years": pet.age_years,
        "weight_lb": pet.weight_lb, "allergies": pet.allergies,
        "notes": pet.notes,
    }


def catalog_metadata():
    path = BASE_DIR / "data" / "catalog_sources.json"
    return json.loads(path.read_text()) if path.exists() else {}


def valid_amazon_link(url):
    parsed = urlparse(url or "")
    return (parsed.scheme == "https" and parsed.hostname in {"www.amazon.com", "amazon.com"}
            and not parsed.username and not parsed.password
            and parse_qs(parsed.query).get("tag") == ["pawpantry-20"])


DISCLOSURE = ("Paw Pantry may earn a commission if you buy through this link, "
              "at no extra cost to you.")
AMAZON_DISCLOSURE = DISCLOSURE + " As an Amazon Associate I earn from qualifying purchases."
PUBLIC_BASE_URL = "https://paw-pantry.onrender.com"


def amazon_search_option(query_text: str, species: Optional[str] = None,
                         category: Optional[str] = None,
                         source_page_url: Optional[str] = None,
                         button_label: str = "See options on Amazon") -> dict:
    """Create a clearly labeled, tagged Amazon search action.

    Search actions are used when Paw Pantry has relevant original guidance but no
    individually verified ASIN. They are never represented as exact products.
    """
    terms = [query_text.strip()]
    lowered = query_text.lower()
    for value in (species, category):
        if value and value.lower() not in lowered:
            terms.append(value.lower())
    terms.append("pet supplies")
    url = "https://www.amazon.com/s/?" + urlencode({
        "field-keywords": " ".join(terms),
        "search-alias": "aps",
        "tag": "pawpantry-20",
        "linkCode": "osi",
    })
    return {
        "retailer": "amazon",
        "destination": "Amazon",
        "button_label": button_label,
        "url": url,
        "disclosure": AMAZON_DISCLOSURE,
        "kind": "search",
        "affiliate": True,
        "opens_after_user_click": True,
        "rel": "sponsored nofollow noopener",
        "source_page_url": source_page_url,
    }


def chewy_program():
    path = BASE_DIR / "data" / "chewy_program.json"
    return json.loads(path.read_text()) if path.exists() else {}


def retailer_options(p: Product, metadata: Optional[dict] = None) -> list[dict]:
    """Return only verified links, including everything a connector must display."""
    metadata = metadata if metadata is not None else catalog_metadata().get(str(p.id), {})
    if metadata.get("catalog_status") == "retired":
        return []
    options = []
    source_page_url = f"{PUBLIC_BASE_URL}/catalog#product-{p.id}"
    if valid_amazon_link(p.amazon_url):
        options.append({
            "retailer": "amazon",
            "destination": "Amazon",
            "button_label": "Check on Amazon",
            "url": p.amazon_url,
            "disclosure": AMAZON_DISCLOSURE,
            "kind": "product",
            "affiliate": True,
            "opens_after_user_click": True,
            "rel": "sponsored nofollow noopener",
            "source_page_url": source_page_url,
        })
    else:
        options.append(amazon_search_option(
            " ".join(value for value in (p.brand, p.name) if value),
            p.species, p.category, source_page_url,
            button_label="Find this product type on Amazon",
        ))
    if valid_chewy_link(p.chewy_url, metadata, chewy_program()):
        options.append({
            "retailer": "chewy",
            "destination": "Chewy",
            "button_label": "Check on Chewy",
            "url": p.chewy_url,
            "disclosure": DISCLOSURE,
            "kind": "product",
            "affiliate": True,
            "opens_after_user_click": True,
            "rel": "sponsored nofollow noopener",
            "source_page_url": source_page_url,
        })
    return options


def product_to_dict(p: Product) -> dict:
    metadata = catalog_metadata().get(str(p.id), {})
    retired = metadata.get("catalog_status") == "retired"
    options = retailer_options(p, metadata)
    return {
        "id": p.id, "name": p.name, "brand": p.brand,
        "species": p.species, "category": p.category,
        "package_size": p.package_size, "notes": p.notes,
        "website_url": None if retired else f"{PUBLIC_BASE_URL}/catalog#product-{p.id}",
        "catalog_status": "retired" if retired else "active",
        "replacement_product_id": metadata.get("replacement_product_id"),
        "amazon_link_available": any(option["retailer"] == "amazon" for option in options),
        "verified_amazon_product_link": valid_amazon_link(p.amazon_url) and not retired,
        "affiliate_search_available": any(
            option["retailer"] == "amazon" and option["kind"] == "search"
            for option in options),
        "chewy_link_available": any(option["retailer"] == "chewy" for option in options),
        "retailer_options": options,
    }


SEARCH_ALIASES = {
    "puppy": ("dog",), "puppies": ("dog",),
    "kitten": ("cat",), "kittens": ("cat",),
    "bunny": ("rabbit",), "bunnies": ("rabbit",),
    "parakeet": ("bird",), "cockatiel": ("bird",),
    "guinea": ("guinea-pig",), "cavy": ("guinea-pig",),
    "gecko": ("lizard", "reptile"), "frog": ("amphibian",),
    "toad": ("amphibian",), "mice": ("mouse",), "tortoise": ("turtle",),
    "terrarium": ("reptile", "habitat"), "substrate": ("bedding", "habitat"),
    "aquarium": ("fish", "habitat"), "tank": ("fish", "habitat"),
    "feed": ("food",), "hungry": ("food",), "kibble": ("food",), "hay": ("food",),
    "snack": ("treats",), "snacks": ("treats",),
    "training": ("training", "treats"), "clicker": ("training",),
    "chew": ("toys",), "chewer": ("dog", "toys"), "play": ("toys",),
    "scratch": ("furniture", "toys"), "scratching": ("furniture", "toys"),
    "poop": ("supplies", "waste", "bags"), "waste": ("supplies", "bags"),
    "bath": ("grooming",), "shampoo": ("grooming",), "brush": ("grooming",),
    "comb": ("grooming", "flea-tick"),
    "vitamin": ("supplements",), "vitamins": ("supplements",),
    "supplement": ("supplements",), "supplements": ("supplements",),
    "probiotic": ("supplements",), "joint": ("supplements",),
    "uvb": ("heating-lighting",), "thermostat": ("heating-lighting",),
    "lamp": ("heating-lighting",), "filter": ("maintenance",),
    "pump": ("maintenance",),
    "bed": ("beds",), "mat": ("beds",),
    "crate": ("carriers",), "carrier": ("carriers",),
    "leash": ("leashes",), "harness": ("leashes",), "collar": ("leashes",),
    "flea": ("flea-tick",), "tick": ("flea-tick",),
    "bowl": ("feeding",), "fountain": ("feeding",),
    "stain": ("cleaning",), "odor": ("cleaning",), "cleaner": ("cleaning",),
    "wipes": ("cleaning", "grooming"),
}
SEARCH_STOPWORDS = {"a", "an", "and", "for", "i", "is", "me", "my", "of", "on",
                    "please", "the", "to", "what", "with"}
KNOWN_SPECIES_TERMS = {
    "amphibian", "bird", "cat", "chinchilla", "dog", "ferret", "fish", "gerbil",
    "guinea-pig", "hamster", "hedgehog", "hermit-crab", "lizard", "mouse", "rabbit",
    "rat", "reptile", "snake", "turtle",
}
PRODUCT_TYPE_TERMS = {
    "bedding", "bowl", "carrier", "cleaner", "conditioner", "food", "hay",
    "litter", "substrate", "toy", "toys", "treat", "treats", "wipes",
}
PRODUCT_TYPE_TITLE_ALIASES = {
    "cleaner": {"cleaner", "cleaning"},
    "toy": {"toy", "toys"},
    "toys": {"toy", "toys"},
    "treat": {"treat", "treats"},
    "treats": {"treat", "treats"},
}


def search_tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", value.lower())
            if token not in SEARCH_STOPWORDS]


def requested_species(tokens: list[str]) -> set[str]:
    species = {token for token in tokens if token in KNOWN_SPECIES_TERMS}
    for token in tokens:
        species.update(alias for alias in SEARCH_ALIASES.get(token, ())
                       if alias in KNOWN_SPECIES_TERMS)
    return species


def product_search_score(product: Product, query: str) -> int:
    """Rank forgiving keyword matches without making suitability claims."""
    requested = search_tokens(query)
    if not requested:
        return 1
    text = " ".join((product.name, product.brand, product.species, product.category,
                     product.package_size, product.notes)).lower()
    available = set(search_tokens(text))
    title_tokens = set(search_tokens(product.name))
    species_signals = requested_species(requested)
    score = 20 if query.strip().lower() in text else 0
    for token in requested:
        if token in available:
            score += 6
        elif len(token) >= 4 and any(
                len(word) >= 4 and (token in word or word in token)
                for word in available):
            score += 2
        if token == product.category:
            score += 12
        for alias in SEARCH_ALIASES.get(token, ()):
            if alias == product.category:
                score += 12
            elif alias in available or alias == product.species:
                score += 4
        if (token in PRODUCT_TYPE_TERMS
                and title_tokens & PRODUCT_TYPE_TITLE_ALIASES.get(token, {token})):
            score += 12
        if token in KNOWN_SPECIES_TERMS and token == product.species:
            score += 10
    if species_signals:
        if product.species in species_signals:
            score += 24
        elif product.species == "any":
            score += 4
        else:
            score -= 16
    return score


def product_embedding_text(product: Product) -> str:
    return (f"Pet species: {product.species}. Supply category: {product.category}. "
            f"Product: {product.brand} {product.name}. Package: {product.package_size}. "
            f"Details: {product.notes}")


@lru_cache(maxsize=1)
def inventory_concepts() -> list[dict]:
    """Load broad shopping coverage records; these are product types, not live stock."""
    rows = [InventoryConcept.model_validate(row).model_dump() for row in json.loads(
        (BASE_DIR / "data" / "shopping_intents.json").read_text())]
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Shopping inventory IDs must be unique.")
    return rows


def intent_search_score(intent: dict, query: str) -> int:
    requested = search_tokens(query)
    if not requested:
        return 1
    normalized_query = " ".join(requested)
    normalized_title = " ".join(search_tokens(intent["title"]))
    text = " ".join((intent["title"], intent["species"], intent["category"],
                     " ".join(intent["keywords"]))).lower()
    available = set(search_tokens(text))
    title_tokens = set(search_tokens(intent["title"]))
    species_signals = requested_species(requested)
    score = 20 if query.strip().lower() in text else 0
    if normalized_title and (normalized_title in normalized_query
                             or normalized_query in normalized_title):
        score += 40
    if intent.get("variant_label") is None:
        score += 2
    else:
        base_tokens = set(search_tokens(intent["title"].split(" — ", 1)[0]))
        label_tokens = set(search_tokens(intent["variant_label"])) - {"option"}
        requested_set = set(requested)
        if base_tokens and label_tokens and base_tokens <= requested_set and label_tokens <= requested_set:
            score += 45
    for token in requested:
        if token in available:
            score += 6
        elif len(token) >= 4 and any(
                len(word) >= 4 and (token in word or word in token)
                for word in available):
            score += 2
        if token == intent["category"]:
            score += 12
        for alias in SEARCH_ALIASES.get(token, ()):
            if alias == intent["category"]:
                score += 12
            elif alias in available or alias == intent["species"]:
                score += 4
        if (token in PRODUCT_TYPE_TERMS
                and title_tokens & PRODUCT_TYPE_TITLE_ALIASES.get(token, {token})):
            score += 12
        if token in KNOWN_SPECIES_TERMS and token == intent["species"]:
            score += 10
    if species_signals:
        if intent["species"] in species_signals:
            score += 24
        elif intent["species"] == "any":
            score += 4
        else:
            score -= 16
    return score


def intent_embedding_text(intent: dict) -> str:
    return (f"Pet species: {intent['species']}. Shopping category: {intent['category']}. "
            f"Product type: {intent['title']}. Search terms: {' '.join(intent['keywords'])}. "
            f"Selection guidance: {intent['guidance']}")


def intent_website_url(intent: dict) -> str:
    return f"{PUBLIC_BASE_URL}/shop/{intent['id']}"


def intent_to_listing(intent: dict) -> dict:
    """Add a first-party source page and monetizable, user-clicked retailer action."""
    source_page_url = intent_website_url(intent)
    return {
        **intent,
        "website_url": source_page_url,
        "retailer_options": [amazon_search_option(
            intent["title"], intent["species"], intent["category"], source_page_url)],
    }


def matching_intents(species: Optional[str], category: Optional[str],
                     query_text: Optional[str], limit: int) -> tuple[list[dict], str]:
    intents = [intent for intent in inventory_concepts()
               if (not species or intent["species"] in {species.lower(), "any"})
               and (not category or intent["category"] == category.lower())]
    method = "keyword"
    if query_text:
        lexical = {intent["id"]: intent_search_score(intent, query_text) for intent in intents}
        # Embed only the reviewed base concepts. The thousands of constraint
        # variants inherit their base score, keeping the small-model request fast
        # and inexpensive while lexical ranking distinguishes the variant.
        bases = {intent["id"]: intent_embedding_text(intent) for intent in intents
                 if intent.get("variant_label") is None}
        semantic = SEMANTIC_RANKER.rank(query_text, bases)
        if semantic:
            method = "hybrid-semantic"
            maximum = max(lexical.values(), default=0) or 1
            combined = {
                intent["id"]: (0.35 * lexical[intent["id"]] / maximum
                               + 0.65 * max(0.0, semantic.get(
                                   intent.get("base_intent_id", intent["id"]), 0.0)))
                for intent in intents
            }
            intents = sorted(intents, key=lambda intent: (-combined[intent["id"]], intent["id"]))
        else:
            intents = [intent for intent in sorted(
                intents, key=lambda intent: (-lexical[intent["id"]], intent["id"]))
                if lexical[intent["id"]] > 0]
    return intents[:limit], method


def matching_products(db: Session, species: Optional[str], category: Optional[str],
                      query_text: Optional[str], limit: int) -> tuple[list[Product], str]:
    query = db.query(Product)
    retired_ids = [int(key) for key, value in catalog_metadata().items()
                   if value.get("catalog_status") == "retired"]
    if retired_ids:
        query = query.filter(Product.id.notin_(retired_ids))
    if species:
        query = query.filter(Product.species.in_([species.lower(), "any"]))
    if category:
        query = query.filter(Product.category == category.lower())
    products = query.all()
    method = "keyword"
    if query_text:
        lexical = {product.id: product_search_score(product, query_text) for product in products}
        semantic = SEMANTIC_RANKER.rank(
            query_text, {product.id: product_embedding_text(product) for product in products})
        if semantic:
            method = "hybrid-semantic"
            maximum = max(lexical.values(), default=0) or 1
            combined = {
                product.id: (0.35 * lexical[product.id] / maximum
                             + 0.65 * max(0.0, semantic.get(product.id, 0.0)))
                for product in products
            }
            products = sorted(products, key=lambda product: (-combined[product.id], product.id))
        else:
            products = [product for product in sorted(
                products, key=lambda product: (-lexical[product.id], product.id))
                if lexical[product.id] > 0]
    return products[:limit], method


def seed():
    """Synchronize managed catalog IDs without replacing pets or supply records."""
    Base.metadata.create_all(bind=engine)
    data = json.loads((BASE_DIR / "data" / "seed_products.json").read_text())
    ids = [row["id"] for row in data]
    if len(ids) != len(set(ids)):
        raise ValueError("Catalog IDs must be unique.")
    with SessionLocal.begin() as db:
        for row in data:
            product = db.get(Product, row["id"])
            if product is None:
                db.add(Product(**row))
            else:
                for field, value in row.items():
                    setattr(product, field, value)
        if engine.dialect.name == "postgresql":
            db.flush()
            db.execute(text("SELECT setval(pg_get_serial_sequence('products', 'id'), "
                            "COALESCE((SELECT MAX(id) FROM products), 1), "
                            "EXISTS(SELECT 1 FROM products))"))


@app.head("/ping", include_in_schema=False, status_code=204)
@app.get("/ping", include_in_schema=False, status_code=204)
def ping():
    """Process liveness only: frequent probes must not keep Neon compute awake."""
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@app.head("/health", include_in_schema=False)
@app.get("/health", operation_id="check_service_health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(503, "database unavailable") from None
    return {"ok": True}


@app.get("/ready", operation_id="check_connector_readiness")
def ready(db: Session = Depends(get_db)):
    """Connector readiness without revealing credentials or database details."""
    if not MUSE_CONNECTOR_API_KEY:
        raise HTTPException(503, "Muse connector credential is not configured")
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(503, "database unavailable") from None
    return {"ready": True, "scope": "stateless-muse-connector"}


# ---- homepage + static legal pages (served so /, /privacy and /terms work on the free subdomain) ----
@app.head("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/", response_class=HTMLResponse)
def home():
    return (BASE_DIR / "static" / "index.html").read_text()


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return (BASE_DIR / "static" / "privacy.html").read_text()


@app.get("/terms", response_class=HTMLResponse)
def terms():
    return (BASE_DIR / "static" / "terms.html").read_text()


@app.get("/guide", response_class=HTMLResponse)
def guide():
    return (BASE_DIR / "static" / "guide.html").read_text()


@app.get("/about", response_class=HTMLResponse)
def about():
    return (BASE_DIR / "static" / "about.html").read_text()


@app.get("/calculator", response_class=HTMLResponse)
def calculator():
    return (BASE_DIR / "static" / "calculator.html").read_text()


@app.get("/documentation", response_class=HTMLResponse, include_in_schema=False)
def connector_documentation():
    return (BASE_DIR / "static" / "documentation.html").read_text()


@app.get("/guides/{slug}", response_class=HTMLResponse)
def shopping_guide(slug: str):
    guides = json.loads((BASE_DIR / "data" / "guides.json").read_text())
    if slug not in {guide["slug"] for guide in guides}:
        raise HTTPException(404, "guide not found")
    return (BASE_DIR / "static" / "guides" / f"{slug}.html").read_text()


@app.get("/catalog", response_class=HTMLResponse)
def public_catalog(q: str = Query(default="", max_length=200),
                   species: str = Query(default="", max_length=60),
                   category: str = Query(default="", max_length=60),
                   db: Session = Depends(get_db)):
    sources = catalog_metadata()
    program = chewy_program()
    all_products = [p for p in db.query(Product).order_by(Product.id).all()
                    if sources.get(str(p.id), {}).get("catalog_status") != "retired"]
    products = [p for p in all_products
                if (not species or p.species in (species.lower(), "any"))
                and (not category or p.category == category.lower())
                and (not q or q.lower() in f"{p.name} {p.brand} {p.category} {p.notes}".lower())]
    cards = []
    for p in products:
        links = []
        source = sources.get(str(p.id), {})
        display_name = " ".join(value for value in (p.brand, p.name) if value)
        if valid_amazon_link(p.amazon_url):
            links.append(f'<div class="retailer"><a class="link" rel="sponsored nofollow noopener" href="{escape(p.amazon_url, quote=True)}">Check on Amazon</a>'
                         '<p class="link-disclosure">Affiliate link — Paw Pantry may earn a commission.</p></div>')
        else:
            fallback = amazon_search_option(
                " ".join(value for value in (p.brand, p.name) if value),
                p.species, p.category,
                f"{PUBLIC_BASE_URL}/catalog#product-{p.id}",
                button_label="Find this product type on Amazon",
            )
            links.append(f'<div class="retailer"><a class="link" rel="sponsored nofollow noopener" href="{escape(fallback["url"], quote=True)}">Find this product type on Amazon</a>'
                         '<p class="link-disclosure">Affiliate search link — results can change. Paw Pantry may earn a commission.</p></div>')
        if valid_chewy_link(p.chewy_url, source, program):
            links.append(f'<div class="retailer"><a class="link" rel="sponsored nofollow noopener" href="{escape(p.chewy_url, quote=True)}">Check on Chewy</a>'
                         '<p class="link-disclosure">Affiliate link — Paw Pantry may earn a commission.</p></div>')
        if not links:
            if source.get("url"):
                links.append(f'<a class="link" rel="noopener" href="{escape(source["url"], quote=True)}">Product information (not an affiliate link)</a>')
        pet_icon = p.species if p.species in {"dog", "cat", "rabbit", "fish", "bird", "hamster", "reptile"} else "paw"
        cards.append(f'<article id="product-{p.id}"><p class="tag">'
                     f'<img class="pet-icon" src="/static/pets/{pet_icon}.svg" width="38" height="38" alt="" aria-hidden="true" loading="lazy">'
                     f'{escape(p.species)} · {escape(p.category)}</p>'
                     f'<h2>{escape(display_name)}</h2>'
                     f'<p>{escape(p.package_size)}</p><p>{escape(p.notes)}</p>{"".join(links)}</article>')
    def options(values, selected, label):
        display_labels = {
            "flea-tick": "Flea & Tick",
            "guinea-pig": "Guinea Pig",
            "water-care": "Water Care",
        }
        return f'<option value="">{label}</option>' + ''.join(
            f'<option value="{escape(v, quote=True)}"{(" selected" if v == selected.lower() else "")}>{escape(display_labels.get(v, v.replace("-", " ").title()))}</option>'
            for v in sorted(values))
    page = (BASE_DIR / "static" / "catalog.html").read_text()
    replacements = {
        "{{QUERY}}": escape(q, quote=True),
        "{{SPECIES}}": options({p.species for p in all_products}, species, "All pets"),
        "{{CATEGORIES}}": options({p.category for p in all_products}, category, "All categories"),
        "{{COUNT}}": str(len(products)),
        "{{PRODUCTS}}": ''.join(cards) or '<p>No matching products yet. Try a broader search.</p>',
    }
    return re.sub(r"\{\{[A-Z]+\}\}", lambda match: replacements.get(match.group(), match.group()), page)


@app.get("/recommendations", response_class=HTMLResponse, include_in_schema=False)
def public_recommendations(q: str = Query(default="", max_length=200),
                           species: str = Query(default="", max_length=60),
                           category: str = Query(default="", max_length=60)):
    """Browsable first-party index for the broad shopping-intent library."""
    matches, _ = matching_intents(species or None, category or None, q or None, 36)
    all_intents = inventory_concepts()
    cards = ''.join(
        f'<article><p class="tag">{escape(row["species"].replace("-", " ").title())} · '
        f'{escape(row["category"].replace("-", " ").title())}</p>'
        f'<h2><a href="/shop/{escape(row["id"], quote=True)}">{escape(row["title"])}</a></h2>'
        f'<p>{escape(row["guidance"])}</p></article>'
        for row in matches
    ) or '<p>No matching product types yet. Try a broader search.</p>'

    def options(values, selected, label):
        return f'<option value="">{label}</option>' + ''.join(
            f'<option value="{escape(value, quote=True)}"'
            f'{(" selected" if value == selected.lower() else "")}>'
            f'{escape(value.replace("-", " ").title())}</option>'
            for value in sorted(values))

    page = (BASE_DIR / "static" / "recommendations.html").read_text()
    replacements = {
        "{{QUERY}}": escape(q, quote=True),
        "{{SPECIES}}": options({row["species"] for row in all_intents}, species, "All pets"),
        "{{CATEGORIES}}": options(
            {row["category"] for row in all_intents}, category, "All categories"),
        "{{TOTAL}}": f"{len(all_intents):,}",
        "{{COUNT}}": str(len(matches)),
        "{{RESULTS}}": cards,
    }
    return re.sub(r"\{\{[A-Z]+\}\}", lambda match: replacements.get(match.group(), match.group()), page)


@app.get("/shop/{intent_id}", response_class=HTMLResponse, include_in_schema=False)
def shopping_intent_page(intent_id: str):
    """First-party guidance page with a deliberate Amazon affiliate action."""
    intent = next((row for row in inventory_concepts() if row["id"] == intent_id), None)
    if not intent:
        raise HTTPException(404, "shopping recommendation not found")
    source_page_url = intent_website_url(intent)
    amazon = amazon_search_option(
        intent["title"], intent["species"], intent["category"], source_page_url)
    page = (BASE_DIR / "static" / "shop.html").read_text()
    replacements = {
        "{{TITLE}}": escape(intent["title"]),
        "{{SPECIES}}": escape(intent["species"].replace("-", " ").title()),
        "{{CATEGORY}}": escape(intent["category"].replace("-", " ").title()),
        "{{GUIDANCE}}": escape(intent["guidance"]),
        "{{AMAZON_URL}}": escape(amazon["url"], quote=True),
        "{{CANONICAL_URL}}": escape(source_page_url, quote=True),
    }
    return re.sub(r"\{\{[A-Z_]+\}\}", lambda match: replacements.get(match.group(), match.group()), page)


# ---- pets ----
@app.post("/pets", dependencies=[Depends(require_key)])
def create_pet(body: PetCreate, db: Session = Depends(get_db)):
    pet = Pet(**body.model_dump())
    db.add(pet)
    db.commit()
    db.refresh(pet)
    return pet_to_dict(pet)


@app.get("/pets", dependencies=[Depends(require_key)])
def list_pets(db: Session = Depends(get_db)):
    return [pet_to_dict(p) for p in db.query(Pet).all()]


@app.get("/pets/{pet_id}", dependencies=[Depends(require_key)])
def get_pet(pet_id: int, db: Session = Depends(get_db)):
    pet = db.get(Pet, pet_id)
    if not pet:
        raise HTTPException(404, "pet not found")
    return pet_to_dict(pet)


@app.patch("/pets/{pet_id}", dependencies=[Depends(require_key)])
def update_pet(pet_id: int, body: PetUpdate, db: Session = Depends(get_db)):
    pet = db.get(Pet, pet_id)
    if not pet:
        raise HTTPException(404, "pet not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(pet, k, v)
    db.commit()
    return pet_to_dict(pet)


@app.delete("/pets/{pet_id}", dependencies=[Depends(require_key)])
def delete_pet(pet_id: int, db: Session = Depends(get_db)):
    pet = db.get(Pet, pet_id)
    if not pet:
        raise HTTPException(404, "pet not found")
    db.delete(pet)
    db.commit()
    return {"deleted": pet_id}


# ---- supplies + run-out predictions ----
@app.post("/pets/{pet_id}/supplies", dependencies=[Depends(require_key)])
def track_supply(pet_id: int, body: SupplyCreate, db: Session = Depends(get_db)):
    pet = db.get(Pet, pet_id)
    product = db.get(Product, body.product_id)
    if not pet or not product:
        raise HTTPException(404, "pet or product not found")
    s = PetSupply(pet_id=pet_id, **body.model_dump())
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"id": s.id, "pet_id": pet_id, "product_id": s.product_id}


@app.get("/pets/{pet_id}/runout", dependencies=[Depends(require_key)])
def runout(pet_id: int, db: Session = Depends(get_db)):
    """Days of supply left per tracked product, soonest first."""
    pet = db.get(Pet, pet_id)
    if not pet:
        raise HTTPException(404, "pet not found")
    today = date.today()
    out = []
    for s in pet.supplies:
        if not s.daily_use:
            continue
        days_total = s.package_amount / s.daily_use
        days_left = round(days_total - (today - s.last_purchased).days, 1)
        out.append({
            "supply_id": s.id,
            "product": product_to_dict(s.product),
            "days_left": days_left,
            "runs_out": str(s.last_purchased + timedelta(days=ceil(days_total))),
            "reorder_by": str(s.last_purchased + timedelta(days=ceil(days_total) - 5)),
            "overdue": days_left <= 0,
            "unit": s.unit,
        })
    return sorted(out, key=lambda r: r["days_left"])


# ---- catalog ----
@app.get(
    "/products",
    dependencies=[Depends(require_connector_key)],
    response_model=list[CatalogProduct],
    operation_id="search_curated_products",
)
def list_products(species: Optional[str] = Query(default=None, max_length=60),
                  category: Optional[str] = Query(default=None, max_length=60),
                  q: Optional[str] = Query(default=None, max_length=200),
                  limit: int = Query(default=50, ge=1, le=500),
                  offset: int = Query(default=0, ge=0, le=10000),
                  db: Session = Depends(get_db)):
    """Search products and return display-ready retailer buttons and disclosures.

    Clients may surface each `retailer_options` entry directly. They must show its
    disclosure with the button and open the URL only after the user chooses it.
    """
    products, _ = matching_products(db, species, category, q, limit + offset)
    return [product_to_dict(p) for p in products[offset:offset + limit]]


@app.get(
    "/catalog-stats",
    response_model=CatalogStats,
    operation_id="get_catalog_statistics",
)
def catalog_stats(db: Session = Depends(get_db)):
    """Public, non-sensitive counts describing the current connector inventory."""
    metadata = catalog_metadata()
    products = db.query(Product).all()
    active = [product for product in products
              if metadata.get(str(product.id), {}).get("catalog_status") != "retired"]
    species_counts = {}
    category_counts = {}
    for product in active:
        species_counts[product.species] = species_counts.get(product.species, 0) + 1
        category_counts[product.category] = category_counts.get(product.category, 0) + 1
    intents = inventory_concepts()
    shopping_species_counts = {}
    shopping_category_counts = {}
    for intent in intents:
        shopping_species_counts[intent["species"]] = (
            shopping_species_counts.get(intent["species"], 0) + 1)
        shopping_category_counts[intent["category"]] = (
            shopping_category_counts.get(intent["category"], 0) + 1)
    return {
        "active_curated_products": len(active),
        "retired_products": len(products) - len(active),
        "shopping_intents": len(intents),
        "verified_amazon_products": sum(valid_amazon_link(product.amazon_url)
                                        for product in active),
        "affiliate_enabled_active_products": sum(bool(retailer_options(product))
                                                 for product in active),
        "affiliate_enabled_intents": len(intents),
        "verified_chewy_products": sum(valid_chewy_link(
            product.chewy_url, metadata.get(str(product.id), {}), chewy_program())
            for product in active),
        "species_counts": species_counts,
        "category_counts": category_counts,
        "shopping_species_counts": shopping_species_counts,
        "shopping_category_counts": shopping_category_counts,
        "broader_amazon_search_enabled": True,
        "first_party_recommendation_pages": len(intents),
        "semantic_search_enabled": SEMANTIC_RANKER.enabled,
        "semantic_model": SEMANTIC_RANKER.model if SEMANTIC_RANKER.enabled else None,
    }


@app.get(
    "/inventory",
    dependencies=[Depends(require_connector_key)],
    response_model=list[InventoryListing],
    operation_id="search_product_type_inventory",
)
def inventory(species: Optional[str] = Query(default=None, max_length=60),
              category: Optional[str] = Query(default=None, max_length=60),
              q: Optional[str] = Query(default=None, max_length=200),
              limit: int = Query(default=25, ge=1, le=100),
              offset: int = Query(default=0, ge=0, le=10000)):
    """Search broad product-type coverage; records are not live retailer stock."""
    matches, _ = matching_intents(species, category, q, limit + offset)
    return [intent_to_listing(intent) for intent in matches[offset:offset + limit]]


@app.get(
    "/shopping-options",
    dependencies=[Depends(require_connector_key)],
    response_model=ShoppingOptionsResult,
    operation_id="find_shopping_options",
)
def shopping_options(q: str = Query(min_length=2, max_length=200),
                     species: Optional[str] = Query(default=None, max_length=60),
                     category: Optional[str] = Query(default=None, max_length=60),
                     limit: int = Query(default=5, ge=1, le=20),
                     db: Session = Depends(get_db)):
    """Return ranked catalog matches plus a broader, user-initiated Amazon search.

    Use this operation for open-ended shopping requests. Curated products have
    individually verified variants. The broader Amazon action opens changing search
    results, so the client must not describe it as a specific recommendation.
    """
    matches, method = matching_products(db, species, category, q, limit)
    intents, intent_method = matching_intents(species, category, q, limit)
    if intent_method == "hybrid-semantic":
        method = intent_method
    products = [product_to_dict(p) for p in matches]
    return {
        "query": q,
        "curated_products": products,
        "matched_inventory": [intent_to_listing(intent) for intent in intents],
        "broader_amazon_search": amazon_search_option(
            q, species, category,
            f"{PUBLIC_BASE_URL}/recommendations?{urlencode({'q': q})}",
            button_label="See more options on Amazon"),
        "matching_method": method,
        "semantic_model": SEMANTIC_RANKER.model if method == "hybrid-semantic" else None,
        "guidance": ("Inventory matches are product types, not live stock or suitability "
                     "guarantees. Curated products are examples. Amazon search results can "
                     "change. Check the current product, seller, price, ingredients or "
                     "materials, size, and suitability before buying."),
    }


@app.get(
    "/products/{product_id}/link",
    dependencies=[Depends(require_connector_key)],
    response_model=ProductLinkResult,
    operation_id="get_retailer_link",
)
def product_link(product_id: int, retailer: Literal["amazon", "chewy"] = "amazon", db: Session = Depends(get_db)):
    """Return one display-ready, user-initiated affiliate retailer action."""
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(404, "product not found")
    source = catalog_metadata().get(str(p.id), {})
    if source.get("catalog_status") == "retired":
        raise HTTPException(409, "affiliate link not available for a retired product")
    url = p.chewy_url if retailer == "chewy" else p.amazon_url
    if retailer == "chewy" and not valid_chewy_link(url, source, chewy_program()):
        raise HTTPException(409, "verified Chewy affiliate link not available for this product yet")
    if retailer == "amazon" and not valid_amazon_link(url):
        option = amazon_search_option(
            " ".join(value for value in (p.brand, p.name) if value),
            p.species, p.category,
            f"{PUBLIC_BASE_URL}/catalog#product-{p.id}",
            button_label="Find this product type on Amazon")
        return {"product": product_to_dict(p), **option}
    destination = "Amazon" if retailer == "amazon" else "Chewy"
    return {"product": product_to_dict(p), "retailer": retailer,
            "url": url, "disclosure": AMAZON_DISCLOSURE if retailer == "amazon" else DISCLOSURE,
            "destination": destination, "button_label": f"Check on {destination}",
            "kind": "product",
            "affiliate": True, "opens_after_user_click": True,
            "rel": "sponsored nofollow noopener",
            "source_page_url": f"{PUBLIC_BASE_URL}/catalog#product-{p.id}"}


@app.post(
    "/refill-estimate",
    dependencies=[Depends(require_connector_key)],
    response_model=RefillEstimateResult,
    operation_id="estimate_refill_date",
)
def refill_estimate(body: RefillEstimateRequest):
    """Calculate a refill estimate without storing a pet profile or supply record."""
    days_total = body.package_amount / body.daily_use
    elapsed = (date.today() - body.purchase_date).days
    days_left = round(days_total - elapsed, 1)
    runs_out = body.purchase_date + timedelta(days=ceil(days_total))
    return {
        "days_total": round(days_total, 1),
        "days_left": days_left,
        "runs_out": runs_out,
        "reorder_by": runs_out - timedelta(days=body.reorder_lead_days),
        "overdue": days_left <= 0,
        "unit": body.unit,
        "guidance": ("Estimate only. Use the same unit for package amount and daily use, "
                     "and check the actual supply before reordering."),
    }


# The private owner can inspect, correct, or retire a tracked supply.
def supply_to_dict(supply):
    return {"id": supply.id, "pet_id": supply.pet_id,
            "product_id": supply.product_id, "last_purchased": supply.last_purchased,
            "package_amount": supply.package_amount, "daily_use": supply.daily_use,
            "unit": supply.unit}


@app.get("/pets/{pet_id}/supplies", dependencies=[Depends(require_key)])
def list_supplies(pet_id: int, db: Session = Depends(get_db)):
    pet = db.get(Pet, pet_id)
    if not pet:
        raise HTTPException(404, "pet not found")
    return [supply_to_dict(s) for s in pet.supplies]


@app.put("/pets/{pet_id}/supplies/{supply_id}", dependencies=[Depends(require_key)])
def replace_supply(pet_id: int, supply_id: int, body: SupplyCreate,
                   db: Session = Depends(get_db)):
    supply = db.get(PetSupply, supply_id)
    if not supply or supply.pet_id != pet_id or not db.get(Product, body.product_id):
        raise HTTPException(404, "pet supply or product not found")
    for key, value in body.model_dump().items():
        setattr(supply, key, value)
    db.commit()
    return supply_to_dict(supply)


@app.delete("/pets/{pet_id}/supplies/{supply_id}", dependencies=[Depends(require_key)])
def stop_tracking_supply(pet_id: int, supply_id: int, db: Session = Depends(get_db)):
    supply = db.get(PetSupply, supply_id)
    if not supply or supply.pet_id != pet_id:
        raise HTTPException(404, "pet supply not found")
    db.delete(supply)
    db.commit()
    return {"deleted": supply_id}


MUSE_OPENAPI_PATHS = {
    "/health", "/ready", "/products", "/inventory", "/catalog-stats", "/shopping-options",
    "/products/{product_id}/link", "/refill-estimate",
}


def muse_openapi():
    """Publish only the stateless, connector-safe contract to Muse and Swagger."""
    if app.openapi_schema:
        return app.openapi_schema
    connector_routes = [
        route for route in app.routes
        if getattr(route, "path", None) in MUSE_OPENAPI_PATHS
    ]
    schema = get_openapi(
        title="Paw Pantry Connector API",
        version="0.12.0",
        description=("Stateless pet-supply search and refill estimates for Muse. "
                     "This contract cannot access Paw Pantry's private pet-profile workspace. "
                     "Inventory matches include a first-party Paw Pantry guidance page and a "
                     "tagged retailer search action. Show every returned affiliate disclosure "
                     "beside its retailer action and open retailer URLs only after a user chooses them."),
        routes=connector_routes,
    )
    schema["servers"] = [{"url": "https://paw-pantry.onrender.com"}]
    app.openapi_schema = schema
    return schema


app.openapi = muse_openapi

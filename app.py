"""Paw Pantry API — pet replenishment backend (zero-ops: affiliate model).

Serves pet profiles, a curated product catalog mapped to affiliate links,
run-out predictions, and the hosted privacy policy + terms pages.
"""
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import date, timedelta
from hmac import compare_digest
from html import escape
from math import ceil
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.security import APIKeyHeader
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

BASE_DIR = Path(__file__).parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/pawpantry.db")
API_KEY = os.getenv("PAW_PANTRY_API_KEY", "")
if not API_KEY or API_KEY == "dev-key-change-me":
    raise RuntimeError("Set a private PAW_PANTRY_API_KEY before starting Paw Pantry.")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


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


@asynccontextmanager
async def lifespan(application):
    seed()
    yield
    engine.dispose()


app = FastAPI(
    title="Paw Pantry API", version="0.2.0", lifespan=lifespan,
    description="Private single-owner prototype. All pet records share one API key. "
                "Not suitable for unrelated users until user isolation is implemented. "
                "Supply dates are estimates; no scheduled reminders or purchases are made."
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


def product_to_dict(p: Product) -> dict:
    metadata = catalog_metadata().get(str(p.id), {})
    retired = metadata.get("catalog_status") == "retired"
    return {
        "id": p.id, "name": p.name, "brand": p.brand,
        "species": p.species, "category": p.category,
        "package_size": p.package_size, "notes": p.notes,
        "website_url": None if retired else f"https://paw-pantry.onrender.com/catalog#product-{p.id}",
        "catalog_status": "retired" if retired else "active",
        "replacement_product_id": metadata.get("replacement_product_id"),
        "amazon_link_available": valid_amazon_link(p.amazon_url),
    }


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


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(503, "database unavailable") from None
    return {"ok": True}


# ---- homepage + static legal pages (served so /, /privacy and /terms work on the free subdomain) ----
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
    all_products = [p for p in db.query(Product).order_by(Product.id).all()
                    if sources.get(str(p.id), {}).get("catalog_status") != "retired"]
    products = [p for p in all_products
                if (not species or p.species in (species.lower(), "any"))
                and (not category or p.category == category.lower())
                and (not q or q.lower() in f"{p.name} {p.brand} {p.category} {p.notes}".lower())]
    cards = []
    for p in products:
        links = []
        if valid_amazon_link(p.amazon_url):
            links.append(f'<a class="link" rel="sponsored nofollow noopener" href="{escape(p.amazon_url, quote=True)}">View on Amazon (affiliate link)</a>')
        source = sources.get(str(p.id), {}).get("url", "")
        if source and not p.amazon_url:
            links.append(f'<a class="link" rel="noopener" href="{escape(source, quote=True)}">Manufacturer product information</a>')
        if not p.amazon_url and not p.chewy_url:
            links.append('<p class="pending">Affiliate purchase link not available yet.</p>')
        cards.append(f'<article id="product-{p.id}"><p class="tag">{escape(p.species)} · {escape(p.category)}</p>'
                     f'<h2>{escape(p.brand)} {escape(p.name)}</h2>'
                     f'<p>{escape(p.package_size)}</p><p>{escape(p.notes)}</p>{"".join(links)}</article>')
    def options(values, selected, label):
        return f'<option value="">{label}</option>' + ''.join(
            f'<option value="{escape(v, quote=True)}"{(" selected" if v == selected.lower() else "")}>{escape(v.replace("-", " & ").title())}</option>'
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
@app.get("/products", dependencies=[Depends(require_key)])
def list_products(species: Optional[str] = None, category: Optional[str] = None,
                  q: Optional[str] = Query(default=None, max_length=200), db: Session = Depends(get_db)):
    query = db.query(Product)
    retired_ids = [int(key) for key, value in catalog_metadata().items()
                   if value.get("catalog_status") == "retired"]
    if retired_ids:
        query = query.filter(Product.id.notin_(retired_ids))
    if species:
        query = query.filter(Product.species.in_([species.lower(), "any"]))
    if category:
        query = query.filter(Product.category == category.lower())
    if q:
        like = f"%{q}%"
        query = query.filter(Product.name.ilike(like) | Product.brand.ilike(like) |
                             Product.category.ilike(like) | Product.notes.ilike(like))
    return [product_to_dict(p) for p in query.all()]


DISCLOSURE = ("Paw Pantry may earn a commission if you buy through this link, "
              "at no extra cost to you.")


@app.get("/products/{product_id}/link", dependencies=[Depends(require_key)])
def product_link(product_id: int, retailer: Literal["amazon", "chewy"] = "amazon", db: Session = Depends(get_db)):
    """Affiliate purchase link + required disclosure text."""
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(404, "product not found")
    url = p.chewy_url if retailer == "chewy" else p.amazon_url
    expected_hosts = {"amazon": {"www.amazon.com", "amazon.com", "amzn.to"},
                      "chewy": {"www.chewy.com", "chewy.com", "chewy.prf.hn"}}
    parsed = urlparse(url or "")
    if not url or "REPLACE" in url or parsed.scheme != "https" or parsed.hostname not in expected_hosts[retailer]:
        raise HTTPException(409, "affiliate link not configured for this product yet")
    if retailer == "amazon" and not valid_amazon_link(url):
        raise HTTPException(409, "affiliate tracking tag is not configured correctly")
    return {"product": product_to_dict(p), "retailer": retailer,
            "url": url, "disclosure": DISCLOSURE + (" As an Amazon Associate I earn from qualifying purchases." if retailer == "amazon" else ""),
            "destination": "Amazon" if retailer == "amazon" else "Chewy"}


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

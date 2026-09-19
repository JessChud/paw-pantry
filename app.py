"""Paw Pantry API — pet replenishment backend (zero-ops: affiliate model).

Serves pet profiles, a curated product catalog mapped to affiliate links,
run-out predictions, and the hosted privacy policy + terms pages.
"""
import json
import os
from datetime import date
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

BASE_DIR = Path(__file__).parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/pawpantry.db")
API_KEY = os.getenv("PAW_PANTRY_API_KEY", "dev-key-change-me")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
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
class PetCreate(BaseModel):
    name: str
    species: str
    breed: str = ""
    age_years: float = 0
    weight_lb: float = 0
    allergies: str = ""
    notes: str = ""


class PetUpdate(BaseModel):
    name: Optional[str] = None
    species: Optional[str] = None
    breed: Optional[str] = None
    age_years: Optional[float] = None
    weight_lb: Optional[float] = None
    allergies: Optional[str] = None
    notes: Optional[str] = None


class SupplyCreate(BaseModel):
    product_id: int
    last_purchased: date = date.today()
    package_amount: float
    daily_use: float
    unit: str = "oz"


# ---------------- app ----------------
app = FastAPI(title="Paw Pantry API", version="0.1.0")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_key(x_api_key: str = Header(default="")):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")


def pet_to_dict(pet: Pet) -> dict:
    return {
        "id": pet.id, "name": pet.name, "species": pet.species,
        "breed": pet.breed, "age_years": pet.age_years,
        "weight_lb": pet.weight_lb, "allergies": pet.allergies,
        "notes": pet.notes,
    }


def product_to_dict(p: Product) -> dict:
    return {
        "id": p.id, "name": p.name, "brand": p.brand,
        "species": p.species, "category": p.category,
        "package_size": p.package_size, "notes": p.notes,
    }


@app.on_event("startup")
def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(Product).count() == 0:
            data = json.loads((BASE_DIR / "data" / "seed_products.json").read_text())
            for row in data:
                db.add(Product(**row))
            db.commit()
    finally:
        db.close()


@app.get("/health")
def health():
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


# ---- pets ----
@app.post("/pets", dependencies=[Depends(require_key)])
def create_pet(body: PetCreate, db: Session = Depends(get_db)):
    pet = Pet(**body.dict())
    db.add(pet)
    db.commit()
    db.refresh(pet)
    return pet_to_dict(pet)


@app.get("/pets", dependencies=[Depends(require_key)])
def list_pets(db: Session = Depends(get_db)):
    return [pet_to_dict(p) for p in db.query(Pet).all()]


@app.get("/pets/{pet_id}", dependencies=[Depends(require_key)])
def get_pet(pet_id: int, db: Session = Depends(get_db)):
    pet = db.query(Pet).get(pet_id)
    if not pet:
        raise HTTPException(404, "pet not found")
    return pet_to_dict(pet)


@app.patch("/pets/{pet_id}", dependencies=[Depends(require_key)])
def update_pet(pet_id: int, body: PetUpdate, db: Session = Depends(get_db)):
    pet = db.query(Pet).get(pet_id)
    if not pet:
        raise HTTPException(404, "pet not found")
    for k, v in body.dict(exclude_unset=True).items():
        setattr(pet, k, v)
    db.commit()
    return pet_to_dict(pet)


@app.delete("/pets/{pet_id}", dependencies=[Depends(require_key)])
def delete_pet(pet_id: int, db: Session = Depends(get_db)):
    pet = db.query(Pet).get(pet_id)
    if not pet:
        raise HTTPException(404, "pet not found")
    db.delete(pet)
    db.commit()
    return {"deleted": pet_id}


# ---- supplies + run-out predictions ----
@app.post("/pets/{pet_id}/supplies", dependencies=[Depends(require_key)])
def track_supply(pet_id: int, body: SupplyCreate, db: Session = Depends(get_db)):
    pet = db.query(Pet).get(pet_id)
    product = db.query(Product).get(body.product_id)
    if not pet or not product:
        raise HTTPException(404, "pet or product not found")
    s = PetSupply(pet_id=pet_id, **body.dict())
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"id": s.id, "pet_id": pet_id, "product_id": s.product_id}


@app.get("/pets/{pet_id}/runout", dependencies=[Depends(require_key)])
def runout(pet_id: int, db: Session = Depends(get_db)):
    """Days of supply left per tracked product, soonest first."""
    pet = db.query(Pet).get(pet_id)
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
            "runs_out": str(today + __import__("datetime").timedelta(days=max(0, int(days_left)))),
            "reorder_by": str(today + __import__("datetime").timedelta(days=max(0, int(days_left) - 5))),
            "unit": s.unit,
        })
    return sorted(out, key=lambda r: r["days_left"])


# ---- catalog ----
@app.get("/products", dependencies=[Depends(require_key)])
def list_products(species: Optional[str] = None, category: Optional[str] = None,
                  q: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Product)
    if species:
        query = query.filter(Product.species == species)
    if category:
        query = query.filter(Product.category == category)
    if q:
        like = f"%{q}%"
        query = query.filter(Product.name.ilike(like) | Product.brand.ilike(like))
    return [product_to_dict(p) for p in query.all()]


DISCLOSURE = ("Paw Pantry may earn a commission if you buy through this link, "
              "at no extra cost to you.")


@app.get("/products/{product_id}/link", dependencies=[Depends(require_key)])
def product_link(product_id: int, retailer: str = "chewy", db: Session = Depends(get_db)):
    """Affiliate purchase link + required disclosure text."""
    p = db.query(Product).get(product_id)
    if not p:
        raise HTTPException(404, "product not found")
    url = p.chewy_url if retailer == "chewy" else p.amazon_url
    if not url or "REPLACE" in url:
        raise HTTPException(409, "affiliate link not configured for this product yet")
    return {"product": product_to_dict(p), "retailer": retailer,
            "url": url, "disclosure": DISCLOSURE}

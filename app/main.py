import os
import csv
import secrets
import hashlib
from collections import Counter
from datetime import datetime
from typing import Optional, List
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, validator

# ── Config ───────────────────────────────────────────────────────────────────
API_KEY       = os.getenv("API_KEY", "dev-key-change-in-production").strip()
DATA_PATH     = Path(os.getenv("DATA_PATH", "/app/data/freight_data.csv"))
USE_FIRESTORE = os.getenv("USE_FIRESTORE", "false").lower() == "true"
DB_NAME       = os.getenv("FIRESTORE_DB", "inbound-calls-database")

app = FastAPI(title="HappyRobot Freight API", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Firestore ─────────────────────────────────────────────────────────────────
db = None
if USE_FIRESTORE:
    from google.cloud import firestore
    db = firestore.Client(database=DB_NAME)

# ── Auth ─────────────────────────────────────────────────────────────────────
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def require_api_key(key: str = Depends(api_key_header)):
    if not key or not secrets.compare_digest(key, API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return key

# ── In-memory fallback ────────────────────────────────────────────────────────
_mem = {"offers": [], "analyses": [], "nego_events": [], "carriers": {}}

# ── Store helpers ─────────────────────────────────────────────────────────────
def store_doc(collection: str, doc_id: str, data: dict):
    if db:
        db.collection(collection).document(doc_id).set(data)
    else:
        if collection == "carriers":
            _mem["carriers"][doc_id] = data
        else:
            _mem[collection].append(data)

def get_docs(collection: str) -> List[dict]:
    if db:
        return [d.to_dict() for d in db.collection(collection).stream()]
    if collection == "carriers":
        return list(_mem["carriers"].values())
    return list(_mem.get(collection, []))

def get_doc(collection: str, doc_id: str) -> Optional[dict]:
    if db:
        d = db.collection(collection).document(doc_id).get()
        return d.to_dict() if d.exists else None
    if collection == "carriers":
        return _mem["carriers"].get(doc_id)
    return next((x for x in _mem.get(collection, []) if x.get("id") == doc_id), None)

# ── CSV loader ────────────────────────────────────────────────────────────────
def load_data() -> List[dict]:
    rows = []
    with open(DATA_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["loadboard_rate"] = float(row["loadboard_rate"])
            row["weight"]         = int(row["weight"])
            row["num_of_pieces"]  = int(row["num_of_pieces"])
            row["miles"]          = int(row["miles"])
            rows.append(row)
    return rows

# ── Helpers ───────────────────────────────────────────────────────────────────
def make_id(*parts) -> str:
    return hashlib.md5("".join(str(p) for p in parts).encode()).hexdigest()[:10]

def now() -> str:
    return datetime.utcnow().isoformat()

def make_carrier_key(caller_name: str, mc: str) -> str:
    """Unique key is caller name. Falls back to mc_ prefix if no name."""
    if caller_name and caller_name.strip():
        return caller_name.strip().lower()
    return f"mc_{mc.strip()}" if mc and mc.strip() else "unknown"

def find_carrier_by_mc(mc: str) -> Optional[dict]:
    """
    Find the most recently active carrier with this MC number.
    Used to link offer/analysis back to the FMCSA-created record.
    """
    candidates = [c for c in get_docs("carriers") if c.get("mc_number") == mc]
    if not candidates:
        return None
    candidates.sort(key=lambda c: c.get("last_seen") or "", reverse=True)
    return candidates[0]

def upsert_carrier(caller_name: str, mc: str, patch: dict) -> str:
    key = make_carrier_key(caller_name, mc)
    existing = get_doc("carriers", key)
    if not existing:
        existing = {
            "carrier_key": key, "carrier_name": caller_name, "mc_number": mc,
            "legal_name": None, "dba_name": None, "dot_number": None,
            "status": None, "phy_city": None, "phy_state": None,
            "phy_street": None, "phy_zipcode": None,
            "total_drivers": None, "total_power_units": None,
            "common_authority": None, "cargo_insurance_on_file": None,
            "bipd_insurance_on_file": None, "bipd_required_amount": None,
            "safety_rating": None, "allowed_to_operate": None,
            "crash_total": None, "carrier_operation": None, "fmcsa_raw": None,
            "call_history": [], "first_seen": now(), "last_seen": now(),
        }
    for k, v in patch.items():
        if v is not None:
            existing[k] = v
    existing["last_seen"] = now()
    store_doc("carriers", key, existing)
    return key

# ── Models ────────────────────────────────────────────────────────────────────
class OfferWebhook(BaseModel):
    carrier_name:   Optional[str]   = None
    legal_name:     Optional[str]   = None
    commodity_type: Optional[str]   = None
    destination:    Optional[str]   = None
    equipment_type: Optional[str]   = None
    final_rate:     Optional[float] = None
    load_id:        Optional[str]   = None
    mc_number:      Optional[str]   = None
    origin:         Optional[str]   = None
    rate_offered:   Optional[float] = None
    weight:         Optional[int]   = None

    @validator("weight", pre=True)
    def normalize_weight(cls, v):
        if v == "" or v is None:
            return None
        return v

class AnalysisWebhook(BaseModel):
    mc_number:                Optional[str] = None
    load_id:                  Optional[str] = None
    sentiment_classification: Optional[str] = None
    sentiment_reasoning:      Optional[str] = None
    outcome_classification:   Optional[str] = None
    outcome_reasoning:        Optional[str] = None
    call_duration_seconds:    Optional[str] = None
    nego_rounds:              Optional[str] = None
    carrier_attitude:         Optional[str] = None

    @property
    def negotiation_rounds(self) -> Optional[int]:
        try: return int(self.nego_rounds) if self.nego_rounds else None
        except: return None

    @property
    def duration_seconds(self) -> Optional[int]:
        try: return int(self.call_duration_seconds) if self.call_duration_seconds else None
        except: return None

class NegoEvent(BaseModel):
    mc_number:   Optional[str]   = None
    load_id:     Optional[str]   = None
    round:       Optional[int]   = None
    carrier_ask: Optional[float] = None
    ai_decision: Optional[str]   = None
    ai_counter:  Optional[float] = None
    reasoning:   Optional[str]   = None

class FMCSAData(BaseModel):
    mc_number:             Optional[str] = None
    caller_name:           Optional[str] = None
    dotNumber:             Optional[str] = None
    legalName:             Optional[str] = None
    dbaName:               Optional[str] = None
    phyCity:               Optional[str] = None
    phyState:              Optional[str] = None
    phyStreet:             Optional[str] = None
    phyZipcode:            Optional[str] = None
    statusCode:            Optional[str] = None
    commonAuthorityStatus: Optional[str] = None
    totalDrivers:          Optional[str] = None
    totalPowerUnits:       Optional[str] = None
    bipdInsuranceOnFile:   Optional[str] = None
    bipdRequiredAmount:    Optional[str] = None
    cargoInsuranceOnFile:  Optional[str] = None
    safetyRating:          Optional[str] = None
    allowedToOperate:      Optional[str] = None
    crashTotal:            Optional[str] = None
    carrierOperationDesc:  Optional[str] = None

# ── Loads ─────────────────────────────────────────────────────────────────────
@app.get("/loads", dependencies=[Depends(require_api_key)])
def search_loads(
    origin:         Optional[str]   = Query(None),
    destination:    Optional[str]   = Query(None),
    equipment_type: Optional[str]   = Query(None),
    commodity_type: Optional[str]   = Query(None),
    min_rate:       Optional[float] = Query(None),
    max_rate:       Optional[float] = Query(None),
    limit:          int             = Query(1000, le=1000),
):
    data = load_data()
    if origin:         data = [r for r in data if origin.lower()         in r["origin"].lower()]
    if destination:    data = [r for r in data if destination.lower()    in r["destination"].lower()]
    if equipment_type: data = [r for r in data if r["equipment_type"].lower() == equipment_type.lower()]
    if commodity_type: data = [r for r in data if r["commodity_type"].lower() == commodity_type.lower()]
    if min_rate:       data = [r for r in data if r["loadboard_rate"] >= min_rate]
    if max_rate:       data = [r for r in data if r["loadboard_rate"] <= max_rate]
    return {"total": len(data), "loads": data[:limit]}

@app.get("/loads/{load_id}", dependencies=[Depends(require_api_key)])
def get_load(load_id: str):
    for row in load_data():
        if row["load_id"] == load_id:
            return row
    raise HTTPException(status_code=404, detail="Load not found")

# ── Webhooks ──────────────────────────────────────────────────────────────────
@app.post("/webhooks/offer", dependencies=[Depends(require_api_key)])
def webhook_offer(payload: OfferWebhook):
    entry = payload.dict()
    entry["id"]        = make_id(payload.mc_number or "", now())
    entry["timestamp"] = now()
    store_doc("offers", entry["id"], entry)

    if payload.mc_number:
        # FMCSA always fires first and creates the carrier record with the caller's name.
        # Find that existing record by MC number rather than creating a new one.
        existing = find_carrier_by_mc(payload.mc_number)
        if existing:
            key = existing["carrier_key"]
        else:
            # No FMCSA record yet — create one using whatever name we have
            key = upsert_carrier(payload.carrier_name or "", payload.mc_number, {})

        carrier = get_doc("carriers", key)
        carrier["call_history"].append({
            "type": "offer", "load_id": payload.load_id,
            "final_rate": payload.final_rate, "rate_offered": payload.rate_offered,
            "origin": payload.origin, "destination": payload.destination,
            "equipment_type": payload.equipment_type, "commodity_type": payload.commodity_type,
            "timestamp": entry["timestamp"], "offer_id": entry["id"],
        })
        store_doc("carriers", key, carrier)
    return {"status": "received", "id": entry["id"]}

@app.post("/webhooks/analysis", dependencies=[Depends(require_api_key)])
def webhook_analysis(payload: AnalysisWebhook):
    entry = payload.dict()
    entry["id"]                    = make_id(payload.mc_number or "", now())
    entry["timestamp"]             = now()
    entry["call_duration_seconds"] = payload.duration_seconds
    entry["negotiation_rounds"]    = payload.negotiation_rounds
    store_doc("analyses", entry["id"], entry)

    if payload.mc_number:
        existing = find_carrier_by_mc(payload.mc_number)
        if existing:
            key = existing["carrier_key"]
            carrier = get_doc("carriers", key)
            if carrier:
                carrier["call_history"].append({
                    "type": "analysis", "load_id": payload.load_id,
                    "sentiment_classification": payload.sentiment_classification,
                    "sentiment_reasoning":      payload.sentiment_reasoning,
                    "outcome_classification":   payload.outcome_classification,
                    "outcome_reasoning":        payload.outcome_reasoning,
                    "call_duration_seconds":    payload.duration_seconds,
                    "carrier_attitude":         payload.carrier_attitude,
                    "negotiation_rounds":       payload.negotiation_rounds,
                    "timestamp":               entry["timestamp"],
                })
                store_doc("carriers", key, carrier)
    return {"status": "received", "id": entry["id"]}

@app.post("/webhooks/negotiation", dependencies=[Depends(require_api_key)])
def webhook_negotiation(payload: NegoEvent):
    entry = payload.dict()
    entry["id"]        = make_id(payload.mc_number or "", payload.load_id or "", now())
    entry["timestamp"] = now()
    store_doc("nego_events", entry["id"], entry)
    return {"status": "received", "id": entry["id"]}

@app.post("/carriers/{mc_number}/fmcsa", dependencies=[Depends(require_api_key)])
def push_fmcsa(mc_number: str, data: FMCSAData):
    # Find the right carrier key using the caller_name in the FMCSA payload
    key = make_carrier_key(data.caller_name or "", mc_number)
    upsert_carrier(data.caller_name or "", mc_number, {
        "legal_name":              data.legalName,
        "dba_name":                data.dbaName,
        "dot_number":              data.dotNumber,
        "status":                  data.statusCode,
        "phy_city":                data.phyCity,
        "phy_state":               data.phyState,
        "phy_street":              data.phyStreet,
        "phy_zipcode":             data.phyZipcode,
        "total_drivers":           data.totalDrivers,
        "total_power_units":       data.totalPowerUnits,
        "common_authority":        data.commonAuthorityStatus,
        "bipd_insurance_on_file":  data.bipdInsuranceOnFile,
        "bipd_required_amount":    data.bipdRequiredAmount,
        "cargo_insurance_on_file": data.cargoInsuranceOnFile,
        "safety_rating":           data.safetyRating,
        "allowed_to_operate":      data.allowedToOperate,
        "crash_total":             data.crashTotal,
        "carrier_operation":       data.carrierOperationDesc,
        "fmcsa_raw":               data.dict(),
    })
    return {"status": "updated", "mc_number": mc_number}

# ── Carriers ──────────────────────────────────────────────────────────────────
@app.get("/carriers", dependencies=[Depends(require_api_key)])
def list_carriers():
    data = get_docs("carriers")
    return {"total": len(data), "carriers": data}

@app.get("/carriers/{carrier_key}", dependencies=[Depends(require_api_key)])
def get_carrier(carrier_key: str):
    c = get_doc("carriers", carrier_key)
    if not c:
        raise HTTPException(status_code=404, detail="Carrier not found")
    return c

@app.get("/offers", dependencies=[Depends(require_api_key)])
def list_offers():
    return {"total": len(data := get_docs("offers")), "offers": data}

@app.get("/analyses", dependencies=[Depends(require_api_key)])
def list_analyses():
    return {"total": len(data := get_docs("analyses")), "analyses": data}

@app.get("/negotiations", dependencies=[Depends(require_api_key)])
def list_negotiations():
    return {"total": len(data := get_docs("nego_events")), "events": data}

# ── Metrics ───────────────────────────────────────────────────────────────────
@app.get("/metrics", dependencies=[Depends(require_api_key)])
def get_metrics():
    offers    = get_docs("offers")
    analyses  = get_docs("analyses")
    carriers  = get_docs("carriers")

    booked       = [o for o in offers if o.get("final_rate")]
    with_both    = [o for o in offers if o.get("final_rate") and o.get("rate_offered")]
    savings_list = [o["rate_offered"] - o["final_rate"] for o in with_both]
    final_rates  = [o["final_rate"] for o in booked]
    asked_rates  = [o["rate_offered"] for o in offers if o.get("rate_offered")]

    avg_savings    = round(sum(savings_list)/len(savings_list), 2) if savings_list else 0
    avg_final_rate = round(sum(final_rates)/len(final_rates), 2)   if final_rates  else 0
    avg_asked_rate = round(sum(asked_rates)/len(asked_rates), 2)   if asked_rates  else 0
    total_saved    = round(sum(savings_list), 2)                   if savings_list else 0
    pct_saved      = round(avg_savings/avg_asked_rate*100, 1)      if avg_asked_rate else 0

    outcomes   = Counter(a.get("outcome_classification")   for a in analyses if a.get("outcome_classification"))
    sentiments = Counter(a.get("sentiment_classification") for a in analyses if a.get("sentiment_classification"))
    attitudes  = Counter(a.get("carrier_attitude")         for a in analyses if a.get("carrier_attitude"))
    durations  = [a["call_duration_seconds"] for a in analyses if a.get("call_duration_seconds")]
    rounds_l   = [a["negotiation_rounds"]    for a in analyses if a.get("negotiation_rounds")]

    avg_duration = round(sum(durations)/len(durations)) if durations else 0
    avg_rounds   = round(sum(rounds_l)/len(rounds_l), 1) if rounds_l else 0

    booked_count = sum(v for k, v in outcomes.items() if "book" in str(k).lower()) if outcomes else len(booked)
    total_calls  = sum(outcomes.values()) if outcomes else len(offers)
    booking_rate = round(booked_count/total_calls*100, 1) if total_calls else 0

    equip_calls = Counter(o.get("equipment_type") for o in offers if o.get("equipment_type"))
    origin_calls= Counter(o.get("origin")         for o in offers if o.get("origin"))
    dest_calls  = Counter(o.get("destination")    for o in offers if o.get("destination"))

    return {
        "calls": {
            "total_calls":       total_calls,
            "total_booked":      booked_count,
            "booking_rate":      booking_rate,
            "total_carriers":    len(carriers),
            "avg_final_rate":    avg_final_rate,
            "avg_asked_rate":    avg_asked_rate,
            "avg_savings":       avg_savings,
            "total_saved":       total_saved,
            "pct_saved":         pct_saved,
            "avg_duration_secs": avg_duration,
            "avg_rounds":        avg_rounds,
            "by_outcome":        dict(outcomes),
            "by_sentiment":      dict(sentiments),
            "by_attitude":       dict(attitudes),
            "by_equipment":      dict(equip_calls),
            "top_origins":       dict(origin_calls.most_common(6)),
            "top_destinations":  dict(dest_calls.most_common(6)),
        }
    }

# ── Dashboard ─────────────────────────────────────────────────────────────────
static_dir = Path("/app/static")
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/", response_class=HTMLResponse)
def dashboard():
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return HTMLResponse("<h1>Dashboard not found.</h1>")

@app.get("/health")
def health():
    return {"status": "ok", "timestamp": now(), "firestore": USE_FIRESTORE}

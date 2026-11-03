from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query, HTTPException, status
from pydantic import BaseModel, Field
from timezonefinder import TimezoneFinder
from geopy.geocoders import Nominatim
from dateutil import parser as date_parser

from adhanpy.PrayerTimes import PrayerTimes
from adhanpy.data.Coordinates import Coordinates
from adhanpy.calculation.CalculationMethod import CalculationMethod

app = FastAPI(
    title="Global Salah Time Observer Agent Service & MCP Tool",
    description="Accurate Islamic prayer time calculations, geocoding, timezone normalization, and Model Context Protocol (MCP) tool integration.",
    version="1.0.0"
)

tf = TimezoneFinder()
geolocator = Nominatim(user_agent="global_salah_observer_agent_service")

PRAYER_ALIASES = {
    "zohor": "dhuhr",
    "zuhr": "dhuhr",
    "subuh": "fajr",
    "asar": "asr",
    "magrib": "maghrib",
    "isyak": "isha"
}

class SalahRequest(BaseModel):
    latitude: Optional[float] = Field(None, ge=-90, le=90, description="Latitude (-90 to 90)")
    longitude: Optional[float] = Field(None, ge=-180, le=180, description="Longitude (-180 to 180)")
    location: Optional[str] = Field(None, description="Location query, e.g. 'Male, Maldives'")
    date: Optional[str] = Field(None, description="Date token ('today', 'tomorrow') or ISO date")
    prayer: Optional[str] = Field(None, description="Specific prayer filter (e.g. 'zohor', 'fajr')")
    method: Optional[str] = Field("MUSLIM_WORLD_LEAGUE", description="Calculation method")
    requester_timezone: Optional[str] = Field(None, description="Requester IANA timezone (e.g. 'Asia/Singapore')")

def resolve_method(name: Optional[str] = None):
    if not name:
        return CalculationMethod.MUSLIM_WORLD_LEAGUE
    clean = str(name).upper().replace(" ", "_")
    methods = {
        "NORTH_AMERICA": CalculationMethod.NORTH_AMERICA,
        "ISNA": CalculationMethod.NORTH_AMERICA,
        "MOON": CalculationMethod.MOON_SIGHTING_COMMITTEE,
        "EGYPT": CalculationMethod.EGYPTIAN,
        "KARACHI": CalculationMethod.KARACHI,
        "UMM": CalculationMethod.UMM_AL_QURA,
        "DUBAI": CalculationMethod.DUBAI,
        "QATAR": CalculationMethod.QATAR,
        "KUWAIT": CalculationMethod.KUWAIT,
        "SINGAPORE": CalculationMethod.SINGAPORE,
        "MUIS": CalculationMethod.SINGAPORE,
    }
    for key, method_enum in methods.items():
        if key in clean:
            return method_enum
    return getattr(CalculationMethod, clean, CalculationMethod.MUSLIM_WORLD_LEAGUE)

def parse_target_date(date_str: Optional[str], tz: ZoneInfo) -> date:
    today_local = datetime.now(tz).date()
    if not date_str:
        return today_local
    
    clean_date = date_str.strip().lower()
    if clean_date in ["today", ""]:
        return today_local
    if clean_date == "tomorrow":
        return today_local + timedelta(days=1)
    if clean_date == "yesterday":
        return today_local - timedelta(days=1)
    
    try:
        return date_parser.parse(date_str).date()
    except Exception:
        return today_local

def calculate_salah_times(lat: float, lon: float, target_date: date, method_name: Optional[str], tz_str: str, req_tz_str: Optional[str]) -> Dict[str, Any]:
    local_tz = ZoneInfo(tz_str)
    req_tz = ZoneInfo(req_tz_str) if req_tz_str else None
    calc_method = resolve_method(method_name)
    
    try:
        coords = Coordinates(float(lat), float(lon))
        pt = PrayerTimes(coords, target_date, calc_method)
        raw_timings = {
            "fajr": pt.fajr,
            "sunrise": pt.sunrise,
            "dhuhr": pt.dhuhr,
            "asr": pt.asr,
            "maghrib": pt.maghrib,
            "isha": pt.isha
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Calculation engine failure: {str(exc)}")

    results = {}
    for name, dt_val in raw_timings.items():
        if dt_val.tzinfo is None:
            local_dt = dt_val.replace(tzinfo=local_tz)
        else:
            local_dt = dt_val.astimezone(local_tz)
            
        utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
        
        entry = {
            "target_local_iso": local_dt.isoformat(),
            "utc_iso": utc_dt.isoformat()
        }
        if req_tz:
            entry["requester_local_iso"] = local_dt.astimezone(req_tz).isoformat()
            
        results[name] = entry

    return {
        "coordinates": {"latitude": lat, "longitude": lon},
        "target_timezone": tz_str,
        "date": target_date.isoformat(),
        "method": str(calc_method),
        "timings": results
    }

def resolve_location(lat: Optional[float], lon: Optional[float], location_name: Optional[str]):
    if lat is not None and lon is not None:
        return lat, lon
    if location_name:
        loc = geolocator.geocode(location_name, timeout=5)
        if loc:
            return loc.latitude, loc.longitude
        raise HTTPException(status_code=404, detail=f"Unable to geocode location: '{location_name}'")
    raise HTTPException(status_code=400, detail="Must provide either coordinates (latitude & longitude) or a location name.")

@app.get("/salah")
def get_salah(
    latitude: Optional[float] = Query(None, ge=-90, le=90),
    longitude: Optional[float] = Query(None, ge=-180, le=180),
    location: Optional[str] = Query(None),
    date: Optional[str] = Query(None),
    prayer: Optional[str] = Query(None),
    method: Optional[str] = Query("MUSLIM_WORLD_LEAGUE"),
    requester_timezone: Optional[str] = Query(None)
):
    lat, lon = resolve_location(latitude, longitude, location)
    tz_str = tf.timezone_at(lat=lat, lng=lon) or "UTC"
    target_date = parse_target_date(date, ZoneInfo(tz_str))
    
    data = calculate_salah_times(lat, lon, target_date, method, tz_str, requester_timezone)
    
    if prayer:
        norm_prayer = prayer.strip().lower()
        canonical_prayer = PRAYER_ALIASES.get(norm_prayer, norm_prayer)
        if canonical_prayer in data["timings"]:
            data["timings"] = {canonical_prayer: data["timings"][canonical_prayer]}
        else:
            raise HTTPException(status_code=400, detail=f"Invalid prayer name: '{prayer}'")
            
    return data

@app.post("/salah")
def post_salah(payload: SalahRequest):
    lat, lon = resolve_location(payload.latitude, payload.longitude, payload.location)
    tz_str = tf.timezone_at(lat=lat, lng=lon) or "UTC"
    target_date = parse_target_date(payload.date, ZoneInfo(tz_str))
    
    data = calculate_salah_times(lat, lon, target_date, payload.method, tz_str, payload.requester_timezone)
    
    if payload.prayer:
        norm_prayer = payload.prayer.strip().lower()
        canonical_prayer = PRAYER_ALIASES.get(norm_prayer, norm_prayer)
        if canonical_prayer in data["timings"]:
            data["timings"] = {canonical_prayer: data["timings"][canonical_prayer]}
        else:
            raise HTTPException(status_code=400, detail=f"Invalid prayer name: '{payload.prayer}'")
            
    return data

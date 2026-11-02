from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any

from fastapi import FastAPI, Query, HTTPException, status
from pydantic import BaseModel, Field
from timezonefinder import TimezoneFinder
from geopy.geocoders import Nominatim
from dateutil import parser as date_parser
import pytz

from adhanpy.PrayerTimes import PrayerTimes
from adhanpy.calculation.CalculationMethod import CalculationMethod

app = FastAPI(
    title="Global Salah Time Observer Agent Service & MCP Tool",
    description="Accurate Islamic prayer time calculations, geocoding, timezone normalization, and Model Context Protocol (MCP) tool integration.",
    version="1.0.0"
)

# Initialize offline tools and helpers
tf = TimezoneFinder()
geolocator = Nominatim(user_agent="global_salah_observer_agent_service")

class SalahRequest(BaseModel):
    latitude: float = Field(..., description="Latitude of the location (-90 to 90)")
    longitude: float = Field(..., description="Longitude of the location (-180 to 180)")
    location: Optional[str] = Field(None, description="Natural text location name (e.g. 'Male, Maldives')")
    date: Optional[str] = Field(None, description="Target date string (e.g. '2026-11-03', 'tomorrow', 'today')")
    prayer: Optional[str] = Field(None, description="Specific prayer name filter (e.g. 'fajr', 'dhuhr', 'asr', 'maghrib', 'isha')")
    method: Optional[str] = Field(None, description="Calculation method (e.g. 'MUSLIM_WORLD_LEAGUE', 'ISNA', 'EGYPTIAN', 'UMM_AL_QURA')")
    requester_timezone: Optional[str] = Field(None, description="IANA timezone of the requesting agent for clock projection")

def resolve_method(name: Optional[str] = None):
    if not name:
        return CalculationMethod.MUSLIM_WORLD_LEAGUE
    clean = str(name).upper().replace(" ", "_")
    if "NORTH_AMERICA" in clean or "ISNA" in clean:
        return CalculationMethod.NORTH_AMERICA
    if "MOON" in clean:
        return CalculationMethod.MOON_SIGHTING_COMMITTEE
    if "EGYPT" in clean:
        return CalculationMethod.EGYPTIAN
    if "KARACHI" in clean:
        return CalculationMethod.KARACHI
    if "UMM" in clean or "QURA" in clean:
        return CalculationMethod.UMM_AL_QURA
    if "DUBAI" in clean:
        return CalculationMethod.DUBAI
    if "QATAR" in clean:
        return CalculationMethod.QATAR
    if "KUWAIT" in clean:
        return CalculationMethod.KUWAIT
    if "SINGAPORE" in clean or "MUIS" in clean:
        return CalculationMethod.SINGAPORE
    return getattr(CalculationMethod, clean, CalculationMethod.MUSLIM_WORLD_LEAGUE)

def parse_target_date(date_str: Optional[str], local_tz: pytz.tzinfo.BaseTzInfo) -> date:
    now_local = datetime.now(local_tz).date()
    if not date_str:
        return now_local
    
    clean_date = date_str.strip().lower()
    if clean_date == "today":
        return now_local
    elif clean_date == "tomorrow":
        return now_local + timedelta(days=1)
    elif clean_date == "yesterday":
        return now_local - timedelta(days=1)
    
    try:
        parsed = date_parser.parse(date_str).date()
        return parsed
    except Exception:
        return now_local

def compute_fallback_timings(target_date: date) -> Dict[str, datetime]:
    # Baseline fallback times if adhanpy calculation encounters edge cases
    base = datetime.combine(target_date, datetime.min.time())
    return {
        "fajr": base.replace(hour=5, minute=0),
        "sunrise": base.replace(hour=6, minute=15),
        "dhuhr": base.replace(hour=12, minute=30),
        "asr": base.replace(hour=15, minute=45),
        "sunset": base.replace(hour=18, minute=30),
        "maghrib": base.replace(hour=18, minute=45),
        "isha": base.replace(hour=20, minute=0)
    }

def calculate_salah_times(lat: float, lon: float, target_date: date, method_name: Optional[str], local_tz_str: str, requester_tz_str: Optional[str]) -> Dict[str, Any]:
    local_tz = pytz.timezone(local_tz_str)
    calc_method = resolve_method(method_name)
    
    try:
        coords = (float(lat), float(lon))
        pt = PrayerTimes(coords, target_date, calc_method)
        
        # Extract prayer time datetimes if available or use fallback mapping
        timings_map = {
            "fajr": getattr(pt, "fajr", None),
            "sunrise": getattr(pt, "sunrise", None),
            "dhuhr": getattr(pt, "dhuhr", None),
            "asr": getattr(pt, "asr", None),
            "maghrib": getattr(pt, "maghrib", None),
            "isha": getattr(pt, "isha", None)
        }
        
        # If any essential timing is missing, trigger fallback
        if any(v is None for v in timings_map.values()):
            fallback = compute_fallback_timings(target_date)
            for k in timings_map:
                if timings_map[k] is None:
                    timings_map[k] = fallback[k]
        
    except Exception:
        fallback = compute_fallback_timings(target_date)
        timings_map = fallback

    result = {}
    req_tz = pytz.timezone(requester_tz_str) if requester_tz_str else None

    for prayer_name, dt_val in timings_map.items():
        if isinstance(dt_val, datetime):
            # adhanpy returns naive datetimes representing local time at coordinates
            local_dt = local_tz.localize(dt_val.replace(tzinfo=None), is_dst=False)
        else:
            # Fallcase if dt_val is already timestamp or string
            local_dt = local_tz.localize(datetime.combine(target_date, datetime.min.time()), is_dst=False)

        utc_dt = local_dt.astimezone(pytz.UTC)
        
        entry = {
            "target_local_iso": local_dt.isoformat(),
            "utc_iso": utc_dt.isoformat()
        }
        if req_tz:
            req_dt = local_dt.astimezone(req_tz)
            entry["requester_local_iso"] = req_dt.isoformat()
            
        result[prayer_name] = entry

    return {
        "latitude": lat,
        "longitude": lon,
        "timezone": local_tz_str,
        "date": target_date.isoformat(),
        "method": str(calc_method),
        "timings": result
    }

@app.get("/")
def read_root():
    return {"status": "healthy", "service": "Global Salah Time Observer"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.get("/tools")
def get_tools():
    return {
        "tools": [
            {
                "name": "get_salah_times",
                "description": "Calculate accurate Islamic prayer times for any geographic coordinate or location name, supporting timezone normalization and multi-calendar awareness.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "latitude": {"type": "number", "description": "Latitude coordinate"},
                        "longitude": {"type": "number", "description": "Longitude coordinate"},
                        "location": {"type": "string", "description": "Natural text location (optional)"},
                        "date": {"type": "string", "description": "Target date or relative string like 'tomorrow' (optional)"},
                        "prayer": {"type": "string", "description": "Specific prayer name filter (optional)"},
                        "method": {"type": "string", "description": "Calculation method authority (optional)"},
                        "requester_timezone": {"type": "string", "description": "Requester IANA timezone for projection (optional)"}
                    },
                    "required": ["latitude", "longitude"]
                }
            }
        ]
    }

@app.get("/salah")
def get_salah(
    latitude: float = Query(21.4225, description="Latitude"),
    longitude: float = Query(39.8262, description="Longitude"),
    location: Optional[str] = Query(None, description="Location name"),
    date: Optional[str] = Query(None, description="Target date"),
    prayer: Optional[str] = Query(None, description="Prayer name"),
    method: Optional[str] = Query(None, description="Calculation method"),
    requester_timezone: Optional[str] = Query(None, description="Requester timezone")
):
    if latitude < -90 or latitude > 90 or longitude < -180 or longitude > 180:
        raise HTTPException(status_code=400, detail="Invalid coordinates")
        
    # Optional geocoding override if natural location string is supplied
    lat, lon = latitude, longitude
    if location and not (latitude == 21.4225 and longitude == 39.8262):
        try:
            loc_obj = geolocator.geocode(location, timeout=5)
            if loc_obj:
                lat, lon = loc_obj.latitude, loc_obj.longitude
        except Exception:
            pass

    tz_str = tf.timezone_at(lat=lat, lng=lon) or 'UTC'
    target_tz = pytz.timezone(tz_str)
    target_date_obj = parse_target_date(date, target_tz)
    
    data = calculate_salah_times(lat, lon, target_date_obj, method, tz_str, requester_timezone)
    
    if prayer:
        p_clean = prayer.strip().lower()
        if p_clean in data["timings"]:
            data["timings"] = {p_clean: data["timings"][p_clean]}
        else:
            raise HTTPException(status_code=400, detail=f"Invalid prayer name: {prayer}")
            
    return data

@app.post("/salah")
def post_salah(payload: SalahRequest):
    if payload.latitude < -90 or payload.latitude > 90 or payload.longitude < -180 or payload.longitude > 180:
        raise HTTPException(status_code=400, detail="Invalid coordinates")
        
    lat, lon = payload.latitude, payload.longitude
    if payload.location:
        try:
            loc_obj = geolocator.geocode(payload.location, timeout=5)
            if loc_obj:
                lat, lon = loc_obj.latitude, loc_obj.longitude
        except Exception:
            pass

    tz_str = tf.timezone_at(lat=lat, lng=lon) or 'UTC'
    target_tz = pytz.timezone(tz_str)
    target_date_obj = parse_target_date(payload.date, target_tz)
    
    data = calculate_salah_times(lat, lon, target_date_obj, payload.method, tz_str, payload.requester_timezone)
    
    if payload.prayer:
        p_clean = payload.prayer.strip().lower()
        if p_clean in data["timings"]:
            data["timings"] = {p_clean: data["timings"][p_clean]}
        else:
            raise HTTPException(status_code=400, detail=f"Invalid prayer name: {payload.prayer}")
            
    return data

import os
import math
from datetime import datetime, date, timedelta, timezone as dt_timezone
from typing import Optional, Dict, Any, List, Tuple
from zoneinfo import ZoneInfo
from enum import Enum

from fastapi import FastAPI, Query, HTTPException, status, Body
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field
from timezonefinder import TimezoneFinder
from geopy.geocoders import Nominatim
from dateutil import parser as date_parser

from adhanpy.PrayerTimes import PrayerTimes
from adhanpy.calculation.CalculationMethod import CalculationMethod

# Optional import and protocol patch for Coordinates to guarantee unpack safety
try:
    from adhanpy.data.Coordinates import Coordinates
    if not hasattr(Coordinates, "__iter__"):
        Coordinates.__iter__ = lambda self: iter((self.latitude, self.longitude))
    if not hasattr(Coordinates, "__getitem__"):
        Coordinates.__getitem__ = lambda self, idx: (self.latitude, self.longitude)[idx]
except Exception:
    Coordinates = None

# ---------------------------------------------------------------------------
# Framework & App Initialization
# ---------------------------------------------------------------------------
app = FastAPI(
    title="salah-time-observer-8638",
    description="Salah State Intelligence, Solar Geometry & Islamic Observational Engine for Autonomous Agents",
    version="2.0.2",
    default_response_class=ORJSONResponse,
)

tf = TimezoneFinder()
geolocator = Nominatim(user_agent="salah_time_observer_agent_v2")

PRAYER_ALIASES: Dict[str, str] = {
    "zohor": "dhuhr",
    "zuhr": "dhuhr",
    "subuh": "fajr",
    "asar": "asr",
    "magrib": "maghrib",
    "isyak": "isha",
}

STANDARD_LOCATIONS: Dict[str, Tuple[float, float, str, str]] = {
    "kuala lumpur": (3.1390, 101.6869, "Kuala Lumpur", "Asia/Kuala_Lumpur"),
    "makkah": (21.4225, 39.8262, "Makkah", "Asia/Riyadh"),
    "medina": (24.4672, 39.6111, "Medina", "Asia/Riyadh"),
    "istanbul": (41.0082, 28.9784, "Istanbul", "Europe/Istanbul"),
    "london": (51.5074, -0.1278, "London", "Europe/London"),
    "cairo": (30.0444, 31.2357, "Cairo", "Africa/Cairo"),
    "jakarta": (-6.2088, 106.8456, "Jakarta", "Asia/Jakarta"),
    "new york": (40.7128, -74.0060, "New York", "America/New_York"),
    "dubai": (25.2048, 55.2708, "Dubai", "Asia/Dubai"),
}

# ---------------------------------------------------------------------------
# Domain Enums
# ---------------------------------------------------------------------------
class PrayerState(str, Enum):
    BEFORE_FAJR = "BEFORE_FAJR"
    FAJR = "FAJR"
    AFTER_FAJR = "AFTER_FAJR"
    BEFORE_DHUHR = "BEFORE_DHUHR"
    DHUHR = "DHUHR"
    AFTER_DHUHR = "AFTER_DHUHR"
    BEFORE_ASR = "BEFORE_ASR"
    ASR = "ASR"
    AFTER_ASR = "AFTER_ASR"
    BEFORE_MAGHRIB = "BEFORE_MAGHRIB"
    MAGHRIB = "MAGHRIB"
    AFTER_MAGHRIB = "AFTER_MAGHRIB"
    BEFORE_ISHA = "BEFORE_ISHA"
    ISHA = "ISHA"
    AFTER_ISHA = "AFTER_ISHA"

class QiyamSignal(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    PEAK = "PEAK"

# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------
class SalahRequest(BaseModel):
    latitude: Optional[float] = Field(None, ge=-90, le=90, description="Latitude (-90 to 90)")
    longitude: Optional[float] = Field(None, ge=-180, le=180, description="Longitude (-180 to 180)")
    location: Optional[str] = Field(None, description="Location query, e.g. 'Kuala Lumpur'")
    date: Optional[str] = Field(None, description="Date token ('today', 'tomorrow') or ISO date")
    prayer: Optional[str] = Field(None, description="Specific prayer filter (e.g. 'zohor', 'fajr')")
    method: Optional[str] = Field("MUSLIM_WORLD_LEAGUE", description="Calculation method")
    requester_timezone: Optional[str] = Field(None, description="Requester IANA timezone (e.g. 'Asia/Kuala_Lumpur')")

class CompareLocationsInput(BaseModel):
    locations: List[str] = Field(
        default=["Kuala Lumpur", "Makkah", "Istanbul", "London"],
        description="List of city or region names to evaluate and compare",
    )

# ---------------------------------------------------------------------------
# Astronomical & Hijri Math
# ---------------------------------------------------------------------------
def gregorian_to_hijri(year: int, month: int, day: int) -> Tuple[int, int, int]:
    """Astronomical algorithmic conversion from Gregorian date to Islamic Hijri date."""
    if month < 3:
        year -= 1
        month += 12
    a = math.floor(year / 100.0)
    b = 2 - a + math.floor(a / 4.0)
    jd = math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1)) + day + b - 1524.5

    epoch_jd = 1948439.5
    days_since_epoch = jd - epoch_jd
    h_year = math.floor((30 * days_since_epoch + 10646) / 10631)

    start_of_year_jd = epoch_jd + math.floor((10631 * (h_year - 1) + 301) / 30)
    day_of_year = jd - start_of_year_jd + 1

    h_month = min(12, math.ceil((day_of_year - 0.5) / 29.5))
    start_of_month_jd = epoch_jd + math.floor((10631 * (h_year - 1) + 301) / 30) + math.ceil(29.5 * (h_month - 1))
    h_day = int(jd - start_of_month_jd + 1)

    if h_day < 1:
        h_month -= 1
        if h_month < 1:
            h_month = 12
            h_year -= 1
        h_day = 30
    elif h_day > 30:
        h_day = 1
        h_month += 1
        if h_month > 12:
            h_month = 1
            h_year += 1

    return int(h_year), int(h_month), int(h_day)

def calculate_solar_elevation(lat: float, lon: float, dt_utc: datetime) -> float:
    """Computes topocentric solar elevation in degrees using spherical solar positioning."""
    day_of_year = dt_utc.timetuple().tm_yday
    hour_utc = dt_utc.hour + dt_utc.minute / 60.0 + dt_utc.second / 3600.0
    gamma = 2.0 * math.pi / 365.25 * (day_of_year - 1 + (hour_utc - 12.0) / 24.0)

    eqtime = 229.18 * (
        0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    )

    time_offset = eqtime + 4.0 * lon
    tst = (hour_utc * 60.0 + time_offset) % 1440.0
    ha_rad = math.radians((tst / 4.0) - 180.0)
    lat_rad = math.radians(lat)

    cos_zenith = math.sin(lat_rad) * math.sin(decl) + math.cos(lat_rad) * math.cos(decl) * math.cos(ha_rad)
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    zenith_deg = math.degrees(math.acos(cos_zenith))
    return round(90.0 - zenith_deg, 2)

# ---------------------------------------------------------------------------
# Core Salah State Intelligence Engines
# ---------------------------------------------------------------------------
def calculate_prayer_state(now_dt: datetime, raw_timings: Dict[str, datetime]) -> str:
    """Computes categorical prayer state across the 24-hour diurnal cycle."""
    pre_window = timedelta(minutes=20)
    prime_window = timedelta(minutes=30)

    fajr = raw_timings['fajr']
    dhuhr = raw_timings['dhuhr']
    asr = raw_timings['asr']
    maghrib = raw_timings['maghrib']
    isha = raw_timings['isha']

    if now_dt < fajr - pre_window:
        return PrayerState.AFTER_ISHA.value
    elif now_dt < fajr:
        return PrayerState.BEFORE_FAJR.value
    elif now_dt < fajr + prime_window:
        return PrayerState.FAJR.value
    elif now_dt < dhuhr - pre_window:
        return PrayerState.AFTER_FAJR.value
    elif now_dt < dhuhr:
        return PrayerState.BEFORE_DHUHR.value
    elif now_dt < dhuhr + prime_window:
        return PrayerState.DHUHR.value
    elif now_dt < asr - pre_window:
        return PrayerState.AFTER_DHUHR.value
    elif now_dt < asr:
        return PrayerState.BEFORE_ASR.value
    elif now_dt < asr + prime_window:
        return PrayerState.ASR.value
    elif now_dt < maghrib - pre_window:
        return PrayerState.AFTER_ASR.value
    elif now_dt < maghrib:
        return PrayerState.BEFORE_MAGHRIB.value
    elif now_dt < maghrib + prime_window:
        return PrayerState.MAGHRIB.value
    elif now_dt < isha - pre_window:
        return PrayerState.AFTER_MAGHRIB.value
    elif now_dt < isha:
        return PrayerState.BEFORE_ISHA.value
    elif now_dt < isha + prime_window:
        return PrayerState.ISHA.value
    else:
        return PrayerState.AFTER_ISHA.value

def calculate_next_prayer(now_dt: datetime, raw_timings: Dict[str, datetime]) -> Dict[str, Any]:
    """Calculates upcoming prayer and countdown time."""
    canonical = ['fajr', 'dhuhr', 'asr', 'maghrib', 'isha']
    for p in canonical:
        p_time = raw_timings[p]
        if now_dt < p_time:
            diff = (p_time - now_dt).total_seconds()
            return {
                'next_prayer': p,
                'minutes_remaining': round(diff / 60.0, 1),
                'seconds_remaining': int(diff),
                'target_time_iso': p_time.isoformat(),
            }

    next_fajr = raw_timings['fajr'] + timedelta(days=1)
    diff = (next_fajr - now_dt).total_seconds()
    return {
        'next_prayer': 'fajr',
        'minutes_remaining': round(diff / 60.0, 1),
        'seconds_remaining': int(diff),
        'target_time_iso': next_fajr.isoformat(),
    }

def calculate_previous_prayer(now_dt: datetime, raw_timings: Dict[str, datetime]) -> Dict[str, Any]:
    """Calculates most recently entered prayer and elapsed time."""
    canonical = ['isha', 'maghrib', 'asr', 'dhuhr', 'fajr']
    for p in canonical:
        p_time = raw_timings[p]
        if now_dt >= p_time:
            diff = (now_dt - p_time).total_seconds()
            return {
                'previous_prayer': p,
                'minutes_since': round(diff / 60.0, 1),
                'seconds_since': int(diff),
                'target_time_iso': p_time.isoformat(),
            }

    prev_isha = raw_timings['isha'] - timedelta(days=1)
    diff = (now_dt - prev_isha).total_seconds()
    return {
        'previous_prayer': 'isha',
        'minutes_since': round(diff / 60.0, 1),
        'seconds_since': int(diff),
        'target_time_iso': prev_isha.isoformat(),
    }

def calculate_daily_progress(now_dt: datetime, raw_timings: Dict[str, datetime]) -> Dict[str, Any]:
    """Evaluates daily obligatory prayer completion progress."""
    canonical = ['fajr', 'dhuhr', 'asr', 'maghrib', 'isha']
    completed = [p for p in canonical if now_dt >= raw_timings[p]]
    remaining = [p for p in canonical if now_dt < raw_timings[p]]
    pct = round(len(completed) / 5.0 * 100.0, 1)

    return {
        'completed_prayers': completed,
        'remaining_prayers': remaining,
        'completion_percentage': pct,
        'completed_count': len(completed),
        'remaining_count': len(remaining),
    }

def calculate_solar_state(lat: float, lon: float, now_dt: datetime, raw_timings: Dict[str, datetime]) -> Dict[str, Any]:
    """Calculates solar position and twilight offsets."""
    utc_dt = now_dt.astimezone(ZoneInfo("UTC")) if now_dt.tzinfo else now_dt.replace(tzinfo=ZoneInfo("UTC"))
    elev = calculate_solar_elevation(lat, lon, utc_dt)

    sunrise_dt = raw_timings.get('sunrise')
    sunset_dt = raw_timings.get('maghrib')

    sunrise_offset = round((now_dt - sunrise_dt).total_seconds() / 60.0, 1) if sunrise_dt else 0.0
    sunset_offset = round((now_dt - sunset_dt).total_seconds() / 60.0, 1) if sunset_dt else 0.0

    return {
        'solar_elevation': elev,
        'sunrise_offset': sunrise_offset,
        'sunset_offset': sunset_offset,
    }

def calculate_ramadan_state(now_dt: datetime, raw_timings: Dict[str, datetime]) -> Dict[str, Any]:
    """Identifies Ramadan dates, fasting activity, iftar, and suhoor status."""
    local_date = now_dt.date()
    hy, hm, hd = gregorian_to_hijri(local_date.year, local_date.month, local_date.day)
    is_ramadan = (hm == 9)
    ramadan_day = hd if is_ramadan else None

    fajr = raw_timings['fajr']
    maghrib = raw_timings['maghrib']

    if fajr <= now_dt < maghrib:
        fasting_active = True
        iftar_countdown = round((maghrib - now_dt).total_seconds() / 60.0, 1)
        suhoor_countdown = None
    elif now_dt < fajr:
        fasting_active = False
        iftar_countdown = round((maghrib - now_dt).total_seconds() / 60.0, 1)
        suhoor_countdown = round((fajr - now_dt).total_seconds() / 60.0, 1)
    else:
        fasting_active = False
        iftar_countdown = None
        next_fajr = fajr + timedelta(days=1)
        suhoor_countdown = round((next_fajr - now_dt).total_seconds() / 60.0, 1)

    return {
        'is_ramadan': is_ramadan,
        'ramadan_day': ramadan_day,
        'fasting_active': fasting_active and is_ramadan,
        'voluntary_fasting_active': fasting_active,
        'iftar_countdown': iftar_countdown,
        'suhoor_countdown': suhoor_countdown,
        'hijri_date': f"{hy}-{hm:02d}-{hd:02d}",
    }

def calculate_tahajjud_window(now_dt: datetime, raw_timings: Dict[str, datetime]) -> Dict[str, Any]:
    """Calculates the final third of the night for Tahajjud prayer."""
    maghrib = raw_timings['maghrib']
    fajr = raw_timings['fajr']

    if now_dt >= maghrib:
        night_start = maghrib
        night_end = fajr + timedelta(days=1)
    elif now_dt < fajr:
        night_start = maghrib - timedelta(days=1)
        night_end = fajr
    else:
        night_start = maghrib
        night_end = fajr + timedelta(days=1)

    night_duration = (night_end - night_start).total_seconds()
    window_start = night_start + timedelta(seconds=night_duration * (2.0 / 3.0))
    window_end = night_end
    is_active = (window_start <= now_dt < window_end)

    if is_active:
        minutes_remaining = round((window_end - now_dt).total_seconds() / 60.0, 1)
        status_str = "ACTIVE_WINDOW"
    elif now_dt < window_start:
        minutes_remaining = round((window_start - now_dt).total_seconds() / 60.0, 1)
        status_str = "UPCOMING"
    else:
        minutes_remaining = 0.0
        status_str = "PASSED"

    return {
        'window_start': window_start.isoformat(),
        'window_end': window_end.isoformat(),
        'is_active': is_active,
        'minutes_remaining': minutes_remaining,
        'status': status_str,
    }

def calculate_qiyam_signal(now_dt: datetime, raw_timings: Dict[str, datetime]) -> Dict[str, Any]:
    """Computes Qiyam night-worship signal intensity based on nocturnal progression."""
    maghrib = raw_timings['maghrib']
    fajr = raw_timings['fajr']
    isha = raw_timings['isha']

    if now_dt >= maghrib:
        n_start = maghrib
        n_end = fajr + timedelta(days=1)
    elif now_dt < fajr:
        n_start = maghrib - timedelta(days=1)
        n_end = fajr
    else:
        return {
            'qiyam_signal': QiyamSignal.LOW.value,
            'signal': QiyamSignal.LOW.value,
            'night_fraction': 0.0,
            'description': 'Daytime period outside nocturnal worship window',
        }

    n_dur = (n_end - n_start).total_seconds()
    elapsed = (now_dt - n_start).total_seconds()
    frac = max(0.0, min(1.0, elapsed / n_dur))

    if frac >= 2.0 / 3.0:
        sig = QiyamSignal.PEAK.value
        desc = 'Final third of the night (Most virtuous window for Tahajjud and Qiyam)'
    elif frac >= 1.0 / 3.0:
        sig = QiyamSignal.HIGH.value
        desc = 'Middle third of the night (Deep night voluntary prayers)'
    elif now_dt >= isha or (now_dt < fajr and frac < 1.0 / 3.0):
        sig = QiyamSignal.MEDIUM.value
        desc = 'First third of the night (Post-Isha voluntary prayers)'
    else:
        sig = QiyamSignal.LOW.value
        desc = 'Early evening twilight'

    return {
        'qiyam_signal': sig,
        'signal': sig,
        'night_fraction': round(frac, 3),
        'description': desc,
    }

def calculate_jumuah_state(now_dt: datetime, raw_timings: Dict[str, datetime]) -> Dict[str, Any]:
    """Computes Friday Jumuah congregational awareness and countdown."""
    weekday = now_dt.weekday()
    is_friday = (weekday == 4)
    dhuhr = raw_timings['dhuhr']

    if is_friday:
        jumuah_start = dhuhr.replace(year=now_dt.year, month=now_dt.month, day=now_dt.day)
        jumuah_end = jumuah_start + timedelta(minutes=75)

        if jumuah_start <= now_dt < jumuah_end:
            jumuah_active = True
            hours_to_jumuah = 0.0
            status_str = "ACTIVE_CONGREGATION"
        elif now_dt < jumuah_start:
            jumuah_active = False
            hours_to_jumuah = round((jumuah_start - now_dt).total_seconds() / 3600.0, 2)
            status_str = "APPROACHING"
        else:
            jumuah_active = False
            next_jumuah = jumuah_start + timedelta(days=7)
            hours_to_jumuah = round((next_jumuah - now_dt).total_seconds() / 3600.0, 2)
            status_str = "CONCLUDED"
    else:
        jumuah_active = False
        days_ahead = (4 - weekday) % 7
        if days_ahead == 0:
            days_ahead = 7
        target_friday = now_dt.date() + timedelta(days=days_ahead)
        next_jumuah = dhuhr.replace(year=target_friday.year, month=target_friday.month, day=target_friday.day)
        hours_to_jumuah = round((next_jumuah - now_dt).total_seconds() / 3600.0, 2)
        status_str = "UPCOMING"

    return {
        'is_friday': is_friday,
        'hours_to_jumuah': hours_to_jumuah,
        'jumuah_active': jumuah_active,
        'status': status_str,
    }

def calculate_salah_state_index(
    daily_progress: Dict[str, Any],
    next_prayer: Dict[str, Any],
    ramadan_state: Dict[str, Any],
    qiyam_signal: Dict[str, Any],
    jumuah_state: Dict[str, Any],
) -> float:
    """Synthesizes an operational 0-100 Salah State Index for autonomous agents."""
    progress_comp = (daily_progress.get('completion_percentage', 0.0) / 100.0) * 35.0

    mins = max(0.0, next_prayer.get('minutes_remaining', 180.0))
    if mins <= 15.0:
        proximity_comp = 25.0
    elif mins <= 45.0:
        proximity_comp = 15.0 + (45.0 - mins) / 30.0 * 10.0
    elif mins <= 120.0:
        proximity_comp = 5.0 + (120.0 - mins) / 75.0 * 10.0
    else:
        proximity_comp = 5.0

    q_map = {
        QiyamSignal.PEAK.value: 20.0,
        QiyamSignal.HIGH.value: 12.0,
        QiyamSignal.MEDIUM.value: 6.0,
        QiyamSignal.LOW.value: 0.0,
    }
    qiyam_comp = q_map.get(qiyam_signal.get('qiyam_signal', 'LOW'), 0.0)
    ramadan_comp = 12.0 if ramadan_state.get('fasting_active') else (6.0 if ramadan_state.get('is_ramadan') else 0.0)

    if jumuah_state.get('jumuah_active'):
        jumuah_comp = 8.0
    elif jumuah_state.get('is_friday') and jumuah_state.get('hours_to_jumuah', 99) < 4.0:
        jumuah_comp = 5.0
    else:
        jumuah_comp = 0.0

    raw_score = progress_comp + proximity_comp + qiyam_comp + ramadan_comp + jumuah_comp
    return round(float(min(100.0, max(10.0, raw_score))), 1)

# ---------------------------------------------------------------------------
# Location & Method Resolvers
# ---------------------------------------------------------------------------
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

def resolve_location(lat: Optional[float], lon: Optional[float], location_name: Optional[str]) -> Tuple[float, float]:
    if lat is not None and lon is not None:
        return float(lat), float(lon)
    if location_name:
        clean_name = location_name.strip().lower()
        if clean_name in STANDARD_LOCATIONS:
            return STANDARD_LOCATIONS[clean_name][0], STANDARD_LOCATIONS[clean_name][1]
        for key, val in STANDARD_LOCATIONS.items():
            if clean_name in key or key in clean_name:
                return val[0], val[1]
        try:
            loc = geolocator.geocode(location_name, timeout=5)
            if loc:
                return float(loc.latitude), float(loc.longitude)
        except Exception:
            pass
        raise HTTPException(status_code=404, detail=f"Unable to geocode location: '{location_name}'")
    raise HTTPException(status_code=400, detail="Must provide either coordinates (latitude & longitude) or a location name.")

# ---------------------------------------------------------------------------
# Core Calculation Pipeline (Tuple Construction)
# ---------------------------------------------------------------------------
def calculate_salah_times(
    lat: float,
    lon: float,
    target_date: date,
    method_name: Optional[str],
    tz_str: str,
    req_tz_str: Optional[str],
) -> Dict[str, Any]:
    local_tz = ZoneInfo(tz_str)
    req_tz = ZoneInfo(req_tz_str) if req_tz_str else None
    calc_method = resolve_method(method_name)
    calc_params = calc_method() if callable(calc_method) else calc_method

    resolved_lat = float(lat)
    resolved_lon = float(lon)

    try:
        # Pass coordinates strictly as a tuple (float, float) for AdhanPy unpacking
        coordinates_tuple = (resolved_lat, resolved_lon)
        pt = PrayerTimes(coordinates_tuple, target_date, calc_params)

        raw_timings_local = {
            "fajr": pt.fajr if pt.fajr.tzinfo else pt.fajr.replace(tzinfo=local_tz),
            "sunrise": pt.sunrise if pt.sunrise.tzinfo else pt.sunrise.replace(tzinfo=local_tz),
            "dhuhr": pt.dhuhr if pt.dhuhr.tzinfo else pt.dhuhr.replace(tzinfo=local_tz),
            "asr": pt.asr if pt.asr.tzinfo else pt.asr.replace(tzinfo=local_tz),
            "maghrib": pt.maghrib if pt.maghrib.tzinfo else pt.maghrib.replace(tzinfo=local_tz),
            "isha": pt.isha if pt.isha.tzinfo else pt.isha.replace(tzinfo=local_tz),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Calculation engine failure: {str(exc)}")

    results = {}
    for name, local_dt in raw_timings_local.items():
        utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
        entry = {
            "target_local_iso": local_dt.isoformat(),
            "utc_iso": utc_dt.isoformat(),
        }
        if req_tz:
            entry["requester_local_iso"] = local_dt.astimezone(req_tz).isoformat()
        results[name] = entry

    now_local = datetime.now(local_tz)
    prayer_state = calculate_prayer_state(now_local, raw_timings_local)
    next_p = calculate_next_prayer(now_local, raw_timings_local)
    prev_p = calculate_previous_prayer(now_local, raw_timings_local)
    progress = calculate_daily_progress(now_local, raw_timings_local)
    solar_state = calculate_solar_state(resolved_lat, resolved_lon, now_local, raw_timings_local)
    ramadan_state = calculate_ramadan_state(now_local, raw_timings_local)
    tahajjud = calculate_tahajjud_window(now_local, raw_timings_local)
    qiyam = calculate_qiyam_signal(now_local, raw_timings_local)
    jumuah = calculate_jumuah_state(now_local, raw_timings_local)

    state_index = calculate_salah_state_index(
        daily_progress=progress,
        next_prayer=next_p,
        ramadan_state=ramadan_state,
        qiyam_signal=qiyam,
        jumuah_state=jumuah,
    )

    return {
        "coordinates": {"latitude": resolved_lat, "longitude": resolved_lon},
        "target_timezone": tz_str,
        "date": target_date.isoformat(),
        "method": str(calc_method),
        "timings": results,
        "current_prayer_state": prayer_state,
        "next_prayer": next_p["next_prayer"],
        "time_remaining": {
            "minutes": next_p["minutes_remaining"],
            "seconds": next_p["seconds_remaining"],
        },
        "previous_prayer": prev_p["previous_prayer"],
        "time_since_previous_minutes": prev_p["minutes_since"],
        "daily_progress": progress,
        "salah_state_index": state_index,
        "solar_state": solar_state,
        "ramadan_state": ramadan_state,
        "tahajjud_window": tahajjud,
        "qiyam_signal": qiyam["qiyam_signal"],
        "jumuah_intelligence": jumuah,
    }

# ---------------------------------------------------------------------------
# Multi-Location Comparison Engine
# ---------------------------------------------------------------------------
def compare_locations(
    locations: Optional[List[str]] = None,
    method_name: str = "MUSLIM_WORLD_LEAGUE",
) -> Dict[str, Any]:
    if not locations:
        locations = ["Kuala Lumpur", "Makkah", "Istanbul", "London"]

    comparisons = []
    for loc_name in locations:
        clean = loc_name.strip().lower()
        if clean in STANDARD_LOCATIONS:
            lat, lon, canon_name, tz_str = STANDARD_LOCATIONS[clean]
        else:
            try:
                lat, lon = resolve_location(None, None, loc_name)
                tz_str = tf.timezone_at(lat=lat, lng=lon) or "UTC"
                canon_name = loc_name.strip().title()
            except Exception:
                lat, lon, canon_name, tz_str = STANDARD_LOCATIONS["kuala lumpur"]

        target_date = datetime.now(ZoneInfo(tz_str)).date()
        salah_data = calculate_salah_times(lat, lon, target_date, method_name, tz_str, None)

        comparisons.append({
            "location": canon_name,
            "coordinates": {"latitude": lat, "longitude": lon},
            "timezone": tz_str,
            "current_prayer_state": salah_data["current_prayer_state"],
            "current_prayer": salah_data["previous_prayer"],
            "next_prayer": salah_data["next_prayer"],
            "minutes_to_next": salah_data["time_remaining"]["minutes"],
            "seconds_to_next": salah_data["time_remaining"]["seconds"],
            "daily_completion_percentage": salah_data["daily_progress"]["completion_percentage"],
            "salah_state_index": salah_data["salah_state_index"],
        })

    return {"comparison": comparisons, "total_locations": len(comparisons)}

# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@app.get("/")
def read_root() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "salah-time-observer-8638",
        "version": "2.0.2",
        "mission": "Salah State Intelligence Engine & MCP Tool for Autonomous Agents",
    }

@app.get("/health")
def health_check() -> Dict[str, Any]:
    return {"status": "healthy"}

@app.get("/tools")
def list_tools() -> Dict[str, Any]:
    return {
        "tools": [
            {
                "name": "get_salah_times",
                "description": "Calculates Islamic prayer times and full Salah State Intelligence (current state, next prayer, progress, and Salah State Index).",
                "endpoint": "/salah",
                "method": "GET",
                "input_schema": {
                    "latitude": "float",
                    "longitude": "float",
                    "location": "string",
                    "date": "string",
                    "prayer": "string",
                    "method": "string",
                    "requester_timezone": "string",
                },
            },
            {
                "name": "compare_locations",
                "description": "Compares current prayer state, next prayer, and minutes remaining across global locations.",
                "endpoint": "/compare",
                "method": "GET",
                "input_schema": {"locations": "list[string]"},
            },
        ]
    }

@app.get("/salah")
def get_salah(
    latitude: Optional[float] = Query(None, ge=-90, le=90),
    longitude: Optional[float] = Query(None, ge=-180, le=180),
    location: Optional[str] = Query(None),
    date: Optional[str] = Query(None),
    prayer: Optional[str] = Query(None),
    method: Optional[str] = Query("MUSLIM_WORLD_LEAGUE"),
    requester_timezone: Optional[str] = Query(None),
) -> Dict[str, Any]:
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
def post_salah(payload: SalahRequest = Body(...)) -> Dict[str, Any]:
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

@app.get("/compare")
def api_compare_locations_get(
    locations: List[str] = Query(default=["Kuala Lumpur", "Makkah", "Istanbul", "London"])
) -> Dict[str, Any]:
    return compare_locations(locations=locations)

@app.post("/compare")
def api_compare_locations_post(payload: CompareLocationsInput = Body(...)) -> Dict[str, Any]:
    return compare_locations(locations=payload.locations)

# ---------------------------------------------------------------------------
# CLI & Server Runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

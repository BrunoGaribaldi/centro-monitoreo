import os
import re
import time
import yaml
from typing import Optional

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import Response, JSONResponse

AUTHELIA_AUTHZ_URL = "http://authelia:9091/api/authz/auth-request"

CONFIG_PATH = "/app/companies.yml"

# WeatherFlow Tempest — proxy hacia su API REST. El token viene por env,
# nunca llega al browser.
WEATHERFLOW_TOKEN = os.environ.get("WEATHERFLOW_TOKEN", "")
WEATHERFLOW_BASE  = "https://swd.weatherflow.com/swd/rest"
WEATHER_CACHE_TTL = 30  # segundos — Tempest publica cada minuto aprox

app = FastAPI(docs_url=None, redoc_url=None)

_config_cache: dict = {}
_config_cache_time: float = 0.0
CONFIG_CACHE_TTL = 30  # segundos

# station_id (int) -> (timestamp_fetch, payload_normalizado)
_weather_cache: dict = {}


def load_config() -> dict:
    global _config_cache, _config_cache_time
    now = time.time()
    if _config_cache and now - _config_cache_time < CONFIG_CACHE_TTL:
        return _config_cache
    with open(CONFIG_PATH) as f:
        _config_cache = yaml.safe_load(f)
    _config_cache_time = now
    return _config_cache


def find_user(cfg: dict, username: str) -> Optional[dict]:
    return next((u for u in cfg.get("users", []) if u.get("username") == username), None)


# Devuelve (allowed_paths, sites_response) según el rol del usuario.
# Roles:
#   superadmin    → cross-empresa, ve TODO
#   admin_empresa → todos los sites de su empresa
#   admin_site    → solo los sites listados en user["sites"]
#   viewer_drone  → solo los paths listados en user["allowed_paths"]
def resolve_cameras(user: dict, cfg: dict) -> tuple[list[str], list[dict]]:
    rol = user.get("rol")

    # Superadmin: itera todas las companies, devuelve todo con etiqueta de empresa
    if rol == "superadmin":
        paths: list[str] = []
        sites: list[dict] = []
        for empresa_id, empresa_data in cfg.get("companies", {}).items():
            for sid, sdata in empresa_data.get("sites", {}).items():
                cams = sdata.get("cameras", [])
                paths.extend(c["path"] for c in cams)
                sites.append({
                    "site":    sid,
                    "display": sdata.get("display", sid),
                    "empresa": empresa_data.get("display", empresa_id),
                    "cameras": cams,
                })
        return paths, sites

    empresa_data = cfg.get("companies", {}).get(user.get("empresa"))
    if not empresa_data:
        return [], []

    if rol == "admin_empresa":
        allowed_sites = empresa_data["sites"]

    elif rol == "admin_site":
        allowed_sites = {
            k: empresa_data["sites"][k]
            for k in user.get("sites", [])
            if k in empresa_data["sites"]
        }

    else:  # viewer_drone
        allowed = set(user.get("allowed_paths", []))
        allowed_sites = {}
        for sid, sdata in empresa_data["sites"].items():
            cams = [c for c in sdata.get("cameras", []) if c["path"] in allowed]
            if cams:
                allowed_sites[sid] = {**sdata, "cameras": cams}

    paths: list[str] = []
    sites: list[dict] = []
    for sid, sdata in allowed_sites.items():
        cams = sdata.get("cameras", [])
        paths.extend(c["path"] for c in cams)
        sites.append({
            "site":    sid,
            "display": sdata.get("display", sid),
            "cameras": cams,
        })
    return paths, sites


# ── GET /center-auth/webrtc-authz ─────────────────────────────────────────────
# Llamado por nginx auth_request en cada request a /webrtc/<path>/whep.
# Combina las dos validaciones:
#   1. Llama a Authelia internamente para verificar sesión y obtener username.
#   2. Chequea autorización fina por path contra companies.yml.
# Esto reemplaza el patrón (inválido en nginx) de dos auth_request en serie.
@app.get("/center-auth/webrtc-authz")
async def webrtc_authz(request: Request):
    # Reenviar a Authelia los headers que necesita para validar la sesión
    authelia_headers = {
        "X-Original-Method":  request.headers.get("x-original-method", "GET"),
        "X-Original-URL":     request.headers.get("x-original-url", ""),
        "X-Forwarded-Method": request.headers.get("x-forwarded-method", "GET"),
        "X-Forwarded-Proto":  request.headers.get("x-forwarded-proto", "https"),
        "X-Forwarded-Host":   request.headers.get("x-forwarded-host", ""),
        "X-Forwarded-Uri":    request.headers.get("x-forwarded-uri", ""),
        "X-Forwarded-For":    request.headers.get("x-forwarded-for", ""),
        "Cookie":             request.headers.get("cookie", ""),
    }

    try:
        async with httpx.AsyncClient() as client:
            authelia_resp = await client.get(AUTHELIA_AUTHZ_URL, headers=authelia_headers, timeout=10.0)
    except Exception:
        return Response(status_code=503)

    if authelia_resp.status_code != 200:
        return Response(status_code=authelia_resp.status_code)

    remote_user = authelia_resp.headers.get("Remote-User")
    if not remote_user:
        return Response(status_code=401)

    webrtc_path_header = request.headers.get("x-webrtc-path", "")
    m = re.match(r"^/webrtc/([^/?]+)", webrtc_path_header)
    if not m:
        return Response(status_code=401)
    requested_path = m.group(1)

    cfg = load_config()
    rec = find_user(cfg, remote_user)
    if not rec:
        return Response(status_code=403)

    allowed_paths, _ = resolve_cameras(rec, cfg)
    if requested_path not in allowed_paths:
        print(f"webrtc-authz: user={remote_user} path={requested_path} → 403 (path not allowed)")
        return Response(status_code=403)

    print(f"webrtc-authz: user={remote_user} path={requested_path} → 200")
    return Response(status_code=200, headers={"Remote-User": remote_user})


# ── GET /center-auth/cameras ───────────────────────────────────────────────────
# Lo llama el dashboard tras autenticarse en Authelia. nginx inyecta Remote-User
# (validado por el auth_request a Authelia). Devuelve la lista de sites/cameras
# que el user puede ver según companies.yml.
@app.get("/center-auth/cameras")
def cameras(remote_user: Optional[str] = Header(default=None, alias="Remote-User")):
    if not remote_user:
        return JSONResponse(status_code=401, content={"error": "missing Remote-User"})

    cfg = load_config()
    rec = find_user(cfg, remote_user)
    if not rec:
        return JSONResponse(status_code=403, content={"error": "user not in companies.yml"})

    _, sites = resolve_cameras(rec, cfg)
    return {
        "empresa": rec.get("empresa"),
        "rol":     rec.get("rol"),
        "sites":   sites,
    }


# =====================================================================
# Weather (WeatherFlow Tempest)
# =====================================================================
# Modelo: cada `site` en companies.yml puede tener una lista
# `weather_stations: [{station_id, display, lat?, lon?, limits?}, ...]`.
# El backend proxea las llamadas a Tempest para no exponer el token y
# para tener un cache compartido (30s).
#
# Permisos: la lista de estaciones que ve un user se deriva con la misma
# lógica que las cámaras (resolve_cameras) — si el user puede ver el site,
# puede ver sus estaciones meteo.


def resolve_weather_stations(user: dict, cfg: dict) -> list[dict]:
    """Devuelve la lista de estaciones meteo visibles para el user.

    Cada item incluye station_id, display, site/empresa de pertenencia,
    y opcionalmente limits custom del site. El frontend usa esto para
    pintar el widget y saber qué pedir.
    """
    rol = user.get("rol")
    result: list[dict] = []

    def emit(empresa_id, empresa_display, site_id, site_display, stations):
        for st in stations or []:
            result.append({
                "station_id": st.get("station_id"),
                "display":    st.get("display") or f"Estación {st.get('station_id')}",
                "site":       site_id,
                "site_display": site_display,
                "empresa":    empresa_display,
                "empresa_id": empresa_id,
                "limits":     st.get("limits"),
                "lat":        st.get("lat"),
                "lon":        st.get("lon"),
            })

    if rol == "superadmin":
        for empresa_id, empresa_data in cfg.get("companies", {}).items():
            for sid, sdata in empresa_data.get("sites", {}).items():
                emit(empresa_id, empresa_data.get("display", empresa_id),
                     sid, sdata.get("display", sid),
                     sdata.get("weather_stations"))
        return result

    empresa_data = cfg.get("companies", {}).get(user.get("empresa"))
    if not empresa_data:
        return []
    empresa_id      = user.get("empresa")
    empresa_display = empresa_data.get("display", empresa_id)

    if rol == "admin_empresa":
        sites_visibles = empresa_data.get("sites", {}).items()
    elif rol == "admin_site":
        allowed = set(user.get("sites", []))
        sites_visibles = [(k, v) for k, v in empresa_data.get("sites", {}).items() if k in allowed]
    else:  # viewer_drone — accede al weather del site donde están sus paths
        allowed_paths = set(user.get("allowed_paths", []))
        sites_visibles = []
        for sid, sdata in empresa_data.get("sites", {}).items():
            if any(c.get("path") in allowed_paths for c in sdata.get("cameras", [])):
                sites_visibles.append((sid, sdata))

    for sid, sdata in sites_visibles:
        emit(empresa_id, empresa_display, sid, sdata.get("display", sid),
             sdata.get("weather_stations"))
    return result


def normalizar_obs_station(payload: dict) -> dict:
    """Toma la respuesta de Tempest /observations/station/ y devuelve un
    dict plano con las métricas que usa el frontend (más timestamp).

    Si Tempest devuelve algo inesperado (lista vacía, campos faltantes),
    los campos vienen como None.
    """
    obs_list = payload.get("obs") or []
    obs = obs_list[0] if obs_list else {}
    wind_direction = obs.get("wind_direction")
    if wind_direction is not None:
        # La veleta física quedó instalada al revés (180° invertida) en el
        # sitio, así que se corrige acá antes de exponerla al frontend.
        wind_direction = (wind_direction + 180) % 360
    return {
        "fetched_at":            int(time.time()),
        "station_observed_at":   obs.get("timestamp"),
        "air_temperature":       obs.get("air_temperature"),
        "relative_humidity":     obs.get("relative_humidity"),
        "station_pressure":      obs.get("station_pressure"),
        "wind_avg":              obs.get("wind_avg"),
        "wind_gust":             obs.get("wind_gust"),
        "wind_lull":             obs.get("wind_lull"),
        "wind_direction":        wind_direction,
        "precip_accum_last_1hr": obs.get("precip_accum_last_1hr"),
        "uv":                    obs.get("uv"),
        "brightness":            obs.get("brightness"),
        "solar_radiation":       obs.get("solar_radiation"),
        "lightning_strike_count_last_3hr":  obs.get("lightning_strike_count_last_3hr"),
        "lightning_strike_last_distance":   obs.get("lightning_strike_last_distance"),
        "lightning_strike_last_epoch":      obs.get("lightning_strike_last_epoch"),
        "battery":               obs.get("battery"),
    }


async def fetch_tempest_observations(station_id: int) -> Optional[dict]:
    """Llama a Tempest /observations/station/{id}. Devuelve dict normalizado
    o None si la llamada falla. Usa el cache de 30s por station_id."""
    now = time.time()
    cached = _weather_cache.get(station_id)
    if cached and now - cached[0] < WEATHER_CACHE_TTL:
        return cached[1]

    if not WEATHERFLOW_TOKEN:
        return None

    url = f"{WEATHERFLOW_BASE}/observations/station/{station_id}"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, params={"token": WEATHERFLOW_TOKEN})
        if resp.status_code != 200:
            print(f"weather: tempest {station_id} → HTTP {resp.status_code}")
            return None
        data = normalizar_obs_station(resp.json())
    except Exception as e:
        print(f"weather: error consultando tempest {station_id}: {e}")
        return None

    _weather_cache[station_id] = (now, data)
    return data


# ── GET /center-auth/weather/stations ────────────────────────────────────────
# Lista de estaciones meteo visibles para el user logueado. NO devuelve datos,
# solo el catálogo. El frontend usa esto para pintar el selector y saber qué
# station_id pedir.
@app.get("/center-auth/weather/stations")
def weather_stations(remote_user: Optional[str] = Header(default=None, alias="Remote-User")):
    if not remote_user:
        return JSONResponse(status_code=401, content={"error": "missing Remote-User"})
    cfg = load_config()
    rec = find_user(cfg, remote_user)
    if not rec:
        return JSONResponse(status_code=403, content={"error": "user not in companies.yml"})
    return {"stations": resolve_weather_stations(rec, cfg)}


# ── GET /center-auth/weather/{station_id} ────────────────────────────────────
# Observaciones actuales de UNA estación. Valida que el user tenga acceso a
# esa station (debe estar en su lista resuelta). Cache compartido de 30s.
@app.get("/center-auth/weather/{station_id}")
async def weather_observation(
    station_id: int,
    remote_user: Optional[str] = Header(default=None, alias="Remote-User"),
):
    if not remote_user:
        return JSONResponse(status_code=401, content={"error": "missing Remote-User"})
    cfg = load_config()
    rec = find_user(cfg, remote_user)
    if not rec:
        return JSONResponse(status_code=403, content={"error": "user not in companies.yml"})

    # Validar permisos: la estación tiene que estar en la lista visible.
    visibles = resolve_weather_stations(rec, cfg)
    info = next((s for s in visibles if s["station_id"] == station_id), None)
    if not info:
        return JSONResponse(status_code=403, content={"error": "station not accessible"})

    data = await fetch_tempest_observations(station_id)
    if not data:
        return JSONResponse(status_code=502, content={"error": "weather upstream unavailable"})

    # Devolver también la info del catálogo (display, site, limits) para que
    # el frontend pueda renderizar sin un segundo fetch.
    return {
        "station": {
            "station_id":   info["station_id"],
            "display":      info["display"],
            "site_display": info["site_display"],
            "empresa":      info["empresa"],
            "limits":       info["limits"],
        },
        "observation": data,
    }

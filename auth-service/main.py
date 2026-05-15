import re
import yaml
from typing import Optional

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import Response, JSONResponse

AUTHELIA_AUTHZ_URL = "http://authelia:9091/api/authz/auth-request"

CONFIG_PATH = "/app/companies.yml"

app = FastAPI(docs_url=None, redoc_url=None)


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


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

import re
import yaml
from typing import Optional

from fastapi import FastAPI, Header
from fastapi.responses import Response, JSONResponse

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


# ── GET /center-auth/authz-path ────────────────────────────────────────────────
# Llamado por nginx auth_request en cada request a /webrtc/<path>/whep.
# Authelia ya validó la sesión; este endpoint chequea autorización fina por path.
# Devuelve 200 si el user puede ver ese path, 403 si no.
@app.get("/center-auth/authz-path")
def authz_path(
    remote_user:    Optional[str] = Header(default=None, alias="Remote-User"),
    x_original_uri: Optional[str] = Header(default=None),
):
    if not remote_user or not x_original_uri:
        return Response(status_code=401)

    m = re.match(r"^/webrtc/([^/?]+)", x_original_uri)
    if not m:
        return Response(status_code=401)
    requested_path = m.group(1)

    cfg = load_config()
    rec = find_user(cfg, remote_user)
    if not rec:
        return Response(status_code=403)

    allowed_paths, _ = resolve_cameras(rec, cfg)
    if requested_path not in allowed_paths:
        return Response(status_code=403)

    return Response(status_code=200)

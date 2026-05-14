import os
import re
import yaml
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

SECRET_KEY  = os.environ["AUTH_SECRET_KEY"]  # KeyError en startup si falta — intencional
ALGORITHM   = "HS256"
TOKEN_TTL_H = 8
CONFIG_PATH = "/app/companies.yml"

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto") #MOTOR DE BCRYPT.
app     = FastAPI(docs_url=None, redoc_url=None)  # sin docs en producción


def load_config() -> dict: #leemos el companies.yml y lo converite a un diccionario de python.
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


#Esto devuelve la lista completa de los paths MediaMTX que puede ver el usuario y devuelve sites --> lista de diccionarios con la estructura para el dashboard.
def resolve_cameras(user: dict, cfg: dict) -> tuple[list[str], list[dict]]:
    """Devuelve (allowed_paths, sites_response) según el rol del usuario."""
    empresa_data = cfg["companies"].get(user["empresa"])
    if not empresa_data:
        return [], []

    rol = user["rol"]

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


class LoginRequest(BaseModel):
    username: str
    password: str


# ── POST /center-auth/login ────────────────────────────────────────────────────
#flujo: leer companies.yml --> buscar el username en la lista de users, verificar la contra con bcrypt, llamar a resolve_cameras() para ver que puede ver, crear jwt con los datos adentro, devolver token + estructura de sites.
@app.post("/center-auth/login")
def login(body: LoginRequest):
    cfg   = load_config()
    users = cfg.get("users", [])
    rec   = next((u for u in users if u["username"] == body.username), None)

    if not rec or not pwd_ctx.verify(body.password, rec["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    allowed_paths, sites = resolve_cameras(rec, cfg)

    token = jwt.encode(
        {
            "sub":           rec["username"],
            "empresa":       rec["empresa"],
            "allowed_paths": allowed_paths,
            "exp":           datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_H),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )

    return {
        "token":   token,
        "empresa": rec["empresa"],
        "rol":     rec["rol"],
        "sites":   sites,
    }


# ── GET /center-auth/cameras ───────────────────────────────────────────────────
# Lo llama el dashboard cuando el user ya tiene el token. Lee el header con el bearer, valida el jwt, extrae el usuario, busca el usuario, llama a resolve_cameras(), devuelve la estructura de sites.
@app.get("/center-auth/cameras")
def cameras(authorization: Optional[str] = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")

    try:
        payload = jwt.decode(
            authorization.split(" ", 1)[1], SECRET_KEY, algorithms=[ALGORITHM]
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

    cfg   = load_config()
    users = cfg.get("users", [])
    rec   = next((u for u in users if u["username"] == payload["sub"]), None)
    if not rec:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")

    _, sites = resolve_cameras(rec, cfg)
    return {"sites": sites}


# ── GET /center-auth/verify ────────────────────────────────────────────────────
# Llamado internamente por nginx auth_request en cada request a /webrtc/.
# Debe responder rápido: solo valida JWT y verifica el path en allowed_paths.
# lee el header Authorization con el bearer,  Leer el header X-Original-URI (lo pone nginx: "/webrtc/dron-cam-q1-angular/whep"), valida jwt, extrae el path, verifica que el path este en allowed path  del jwt y devuelve 200 o 403.
@app.get("/center-auth/verify")
def verify(
    authorization:  Optional[str] = Header(default=None),
    x_original_uri: Optional[str] = Header(default=None),
):
    if not authorization or not authorization.startswith("Bearer "):
        return Response(status_code=401)

    try:
        payload = jwt.decode(
            authorization.split(" ", 1)[1], SECRET_KEY, algorithms=[ALGORITHM]
        )
    except JWTError:
        return Response(status_code=401)

    if not x_original_uri:
        return Response(status_code=401)

    # Extraer el drone path de /webrtc/<path>/... (ej: /webrtc/cam-q1-angular/whep)
    m = re.match(r"^/webrtc/([^/?]+)", x_original_uri)
    if not m:
        return Response(status_code=401)

    if m.group(1) not in payload.get("allowed_paths", []):
        return Response(status_code=403)

    return Response(status_code=200)

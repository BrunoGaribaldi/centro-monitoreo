# Centro de Monitoreo - Drones

Dashboard web para visualizar streams de video de drones publicados desde
DJI FlightHub 2.

**Dominio:** `panel.dronefieldoperation.cloud`

## Arquitectura

```
FlightHub 2  --RTMP-->  [ MediaMTX (Docker) ]  --WebRTC/WHEP-->  Navegador
                                ^                     ^
                                |                     |
                         puerto 1935 TCP        puerto 443 HTTPS
                         puerto 8189 UDP        (nginx host + basic auth)
                                                      |
                                              [ dashboard_web (Docker) ]
                                                  HTML estático
```

## Archivos

- [docker-compose.yml](docker-compose.yml) — define los dos containers (mediamtx + dashboard_web)
- [mediamtx.yml](mediamtx.yml) — config de MediaMTX (paths, auth, WebRTC)
- [html/index.html](html/index.html) — dashboard estático con grilla 2x2
- [nginxconfig.txt](nginxconfig.txt) — pasos para configurar el nginx del host
- [.env.example](.env.example) — plantilla de variables sensibles

## Deploy local (prueba)

```bash
cp .env.example .env
# editar .env y poner una clave fuerte
docker compose up -d
```

Después configurar el nginx del host siguiendo [nginxconfig.txt](nginxconfig.txt).

## Puertos

| Puerto       | Protocolo | Acceso   | Uso                         |
| ------------ | --------- | -------- | --------------------------- |
| 80           | TCP       | público  | Redirect a HTTPS + certbot  |
| 443          | TCP       | público  | Dashboard HTTPS             |
| 1935         | TCP       | público  | RTMP ingesta (FlightHub 2)  |
| 8189         | UDP       | público  | WebRTC ICE (video al cliente) |
| 8080, 8889   | TCP       | loopback | Internos, proxy del host    |

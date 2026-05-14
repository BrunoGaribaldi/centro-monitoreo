# Centro de Monitoreo de Drones

Dashboard web para visualizar, en vivo y con baja latencia, los streams
de video que publican los drones desde **DJI FlightHub 2**. Pensado para
desplegarse en un único servidor en la nube y atender a varios
operadores conectados desde un navegador.

- **Dominio de producción:** `panel.dronefieldoperation.cloud`
- **Versión de MediaMTX en uso:** `v1.18.1`
- **Cámaras activas:** Dock 1, Q1 Angular, Q1 Infrarojo, Q1 Zoom

---

## 1. El problema que resuelve

FlightHub 2 puede *retransmitir* el video de un dron a un servidor
externo, pero solo por **RTMP** — un protocolo viejo (Flash, 2002) que
los navegadores modernos **ya no pueden reproducir directamente**: no
hay forma de poner `<video src="rtmp://...">` y que ande.

Necesitamos entonces un componente intermedio que:

1. Reciba el RTMP que envía FlightHub 2.
2. Lo traduzca a un protocolo que sí entiendan los navegadores.
3. Mantenga la latencia baja (un operador que ve un dron volando no
   puede esperar 10 segundos como en YouTube).

La traducción es a **WebRTC** — el mismo protocolo que usan Google
Meet, Zoom Web, Discord. Latencia ≈ 0.5–2 segundos, contra 5–20 de
HLS o DASH.

---

## 2. La cadena completa

```
   ┌──────────────┐    RTMP    ┌────────────────┐   WebRTC   ┌─────────┐
   │ FlightHub 2  │ ─────────► │    MediaMTX    │ ─────────► │ Browser │
   │ (drone feed) │  push 1935 │ (relay server) │  UDP 8189  │ <video> │
   └──────────────┘            └────────────────┘            └─────────┘
                                       ▲                          ▲
                                       │ señalización             │ HTML
                                       │ HTTP/WHEP 8889           │ estático
                                       │                          │
                                ┌───────────────┐         ┌──────────────┐
                                │  nginx host   │ ◄────── │  dashboard   │
                                │ TLS + proxy   │ proxy   │  (nginx)     │
                                └───────────────┘         └──────────────┘
                                       ▲
                                       │ HTTPS 443
                                       │
                                  operador en
                                  un navegador
```

Lo que pasa en orden:

1. El dron emite video. FlightHub 2 lo levanta y lo **publica** al
   servidor cloud por RTMP al puerto `1935`.
2. **MediaMTX** acepta el RTMP y lo guarda en un *path* identificado
   por el nombre que configuramos (`dock-cam-dock-1`, etc.).
3. Un operador abre `https://panel.dronefieldoperation.cloud` en el
   navegador.
4. El **nginx del host** termina el TLS y proxea al container del
   dashboard, que sirve el HTML.
5. El HTML genera dinámicamente un `<iframe src="/webrtc/<path>/">`
   por cada cámara. El navegador pide esos iframes al mismo nginx, que
   los redirige a MediaMTX.
6. MediaMTX devuelve su reproductor mínimo, que negocia una sesión
   WebRTC con el navegador usando **WHEP**.
7. Una vez negociada, el video fluye **directo del server al browser
   por UDP/8189**, sin pasar por nginx. Esa es la magia de WebRTC: usa
   el transporte más eficiente.

---

## 3. Glosario rápido de protocolos

| Sigla     | Qué es                                                                                                | Quién lo habla              |
| --------- | ----------------------------------------------------------------------------------------------------- | --------------------------- |
| **RTMP**  | Protocolo viejo, estándar de facto para *publicar* video a un servidor desde drones, OBS, cámaras IP. | FlightHub 2 → MediaMTX      |
| **WebRTC**| Protocolo moderno P2P para audio/video en tiempo real, soportado por todos los navegadores.           | MediaMTX → Browser          |
| **WHEP**  | "WebRTC-HTTP Egress Protocol". Forma estandarizada de pedir un stream WebRTC con HTTP.                | Browser ↔ MediaMTX          |
| **ICE**   | "Interactive Connectivity Establishment". WebRTC lo usa para descubrir por dónde pueden viajar los paquetes (UDP, TCP, vía qué IP). | Interno WebRTC |
| **DTLS-SRTP** | Cifrado obligatorio del payload WebRTC. Por eso WebRTC no tiene "modo sin cifrar".               | Internamente browser-server |
| **TLS**   | Cifrado HTTP (el candadito). Lo terminamos en el nginx del host con certs de Let's Encrypt.            | Browser ↔ nginx host        |

---

## 4. Por qué cada tecnología

### MediaMTX

Servidor de medios open source en Go, ligero, sin dependencias.
Soporta RTMP, RTSP, HLS, SRT, **WebRTC** y más, todo "out of the box".

**Por qué no otra opción:**
- *Nginx-rtmp-module*: solo recibe RTMP y emite HLS (alta latencia),
  no habla WebRTC.
- *OvenMediaEngine*: muy bueno pero más pesado y con curva de
  aprendizaje más alta.
- *Janus / mediasoup*: SDKs WebRTC potentes, pero requieren escribir
  código de servidor.

### Docker + Docker Compose

- **Reproducibilidad**: el `docker-compose.yml` describe la
  infraestructura entera, cualquier máquina la levanta igual.
- **Aislamiento**: si MediaMTX crashea, no afecta al sistema.
- **Versionado**: cuando vayamos a producción, pinear la imagen a un
  tag específico (hoy usamos `latest`, ver deuda técnica).

### Dos nginx (uno en host, otro en container)

- **Nginx del host**: ya estaba instalado en el server, tiene TLS
  (certbot), redirect HTTP→HTTPS. Es el "borde" público.
- **Nginx en container** (`dashboard_web`): solo sirve los archivos
  estáticos del dashboard.

Alternativa que descartamos por simplicidad: mover el nginx-borde
adentro de Docker. Es más limpio pero rompía la integración con el
certbot que el server ya tenía. Ver deuda técnica.

### WebRTC en vez de HLS

| Tecnología | Latencia       | Complejidad |
| ---------- | -------------- | ----------- |
| HLS        | 5–20 segundos  | Muy baja    |
| LL-HLS     | 2–6 segundos   | Media       |
| **WebRTC** | **0.5–2 seg**  | Media       |

Para monitorear drones en operación la latencia importa: un evento
con 10s de delay es un evento sobre el que ya no se puede reaccionar.

---

## 5. Estructura del repo

```
.
├── docker-compose.yml      # Define los dos containers
├── mediamtx.yml            # Config de MediaMTX
├── nginxconfig.txt         # Instrucciones para el nginx del host
├── html/
│   └── index.html          # Dashboard estático (grid dinámico vía JS)
├── .env.example            # Plantilla (hoy residual, no se usa)
├── .gitignore
└── Readme.md               # Este archivo
```

> **Nota sobre el server**: el servidor cloud (`nqnpetrol`) hospeda
> otros sitios además de este (`qntdrones.com`, `app`, `n8n`, etc.).
> Por eso usamos puertos altos como `18082` para evitar choques, y
> evitamos modificar config global de nginx que podría afectarlos.

---

## 6. Cada archivo, línea por línea

### 6.1. `docker-compose.yml`

```yaml
services:
  mediamtx:
    image: bluenviron/mediamtx:latest
    container_name: mediamtx_drones
    restart: unless-stopped
```
Container de MediaMTX. Nombre fijo para inspección fácil con
`docker logs mediamtx_drones`. `restart: unless-stopped` = Docker lo
re-arranca solo, salvo que lo paremos a mano.

```yaml
    volumes:
      - ./mediamtx.yml:/mediamtx.yml:ro
```
Montamos nuestro `mediamtx.yml` adentro del container en la ruta donde
MediaMTX lo espera. `:ro` = read-only (defensa: aunque el proceso se
comprometa, no puede modificar su propia config).

```yaml
    ports:
      - "1935:1935/tcp"
```
**RTMP de ingesta, público.** FlightHub 2 vive afuera y necesita
poder conectarse. La seguridad debería estar acá (auth de publish), pero
FlightHub no manda credenciales, así que en su lugar restringimos por
*paths conocidos* (ver `mediamtx.yml`).

```yaml
      - "8189:8189/udp"
```
**WebRTC ICE/UDP, público.** Los paquetes de video viajan acá. Sin
este puerto abierto, la negociación ICE falla y el navegador nunca
recibe video.

```yaml
      - "127.0.0.1:8889:8889/tcp"
```
HTTP de **señalización** WebRTC (WHEP). Solo `127.0.0.1` para que **no
sea accesible desde internet**; lo único que le habla es el nginx del
host (mismo localhost).

```yaml
    networks:
      - monitoring_net

  dashboard_web:
    image: nginx:alpine
    container_name: dashboard_drones
    restart: unless-stopped
    volumes:
      - ./html:/usr/share/nginx/html:ro
```
Container del dashboard. `nginx:alpine` para imagen liviana (~25 MB).
Montamos `html/` en la ruta donde nginx busca archivos a servir.

```yaml
    ports:
      - "127.0.0.1:18082:80/tcp"
```
Solo `127.0.0.1`. Usamos puerto `18082` (alto, fuera del rango
habitual) porque el server hospeda otros servicios y `8080`/`8081`
suelen estar tomados.

```yaml
networks:
  monitoring_net:
    driver: bridge
```
Red Docker propia. Por ahora los dos containers no se hablan entre sí
(cada uno habla con el nginx del host vía loopback), pero queda
preparada para futuras adiciones.

### 6.2. `mediamtx.yml`

> **YAML para Go**: MediaMTX parsea el YAML con el unmarshaler de Go,
> que es estricto con tipos. Usamos `true`/`false` explícitos para los
> booleans (no `yes`/`no`) y comillas en los strings, para evitar
> errores como `cannot unmarshal string into Go value of type bool`.

```yaml
logLevel: info
logDestinations: [stdout]
```
Log estándar por stdout para que `docker logs` lo muestre.

```yaml
api: false
metrics: false
playback: false
```
Endpoints administrativos apagados — menos superficie de ataque.

```yaml
authMethod: internal
authInternalUsers:
  - user: any
    pass: ""
    ips: [127.0.0.1, ::1, 172.16.0.0/12, 192.168.0.0/16, 10.0.0.0/8]
    permissions:
      - action: read
      - action: playback
```
**Regla 1**: lectura libre desde IPs privadas. El nginx del host le
habla a MediaMTX desde `127.0.0.1`, así que entra sin auth. Los
operadores reales llegan vía nginx, no directo a MediaMTX, así que
no hay forma de eludir esto desde internet.

```yaml
  - user: any
    pass: ""
    ips: []
    permissions:
      - action: publish
        path: dock-cam-dock-1
      - action: publish
        path: dron-cam-q1-angular
      - action: publish
        path: dron-cam-q1-infrarojo
      - action: publish
        path: dron-cam-q1-zoom
```
**Regla 2**: publicación SIN auth, restringida a paths conocidos.
DJI FlightHub 2 ignora las credenciales que uno embeba en la URL
RTMP — aunque pongas `rtmp://user:pass@host/...`, no las manda. Por
eso dejamos publish abierto, pero solo para paths declarados (cualquier
intento a `/loquesea` es rechazado). Ver deuda técnica para la
mitigación con whitelist por IP.

```yaml
rtmp: true
rtmpAddress: :1935
rtmpEncryption: "no"
```
Listener RTMP en `:1935`. `rtmpEncryption` es STRING ("no" /
"optional" / "strict"), por eso va entre comillas. Dejamos "no"
porque FlightHub 2 publica RTMP plano.

```yaml
webrtc: true
webrtcAddress: :8889
```
Señalización WebRTC (WHEP) en HTTP/8889.

```yaml
webrtcAllowOrigins: ["https://panel.dronefieldoperation.cloud"]
```
CORS: solo aceptamos peticiones WHEP que vengan de nuestro dominio.
Evita que otra página externa embeba nuestros streams. **Ojo**: en
versiones < 1.18 se llamaba `webrtcAllowOrigin` (singular, string).
Desde 1.18 es array.

```yaml
webrtcTrustedProxies: []
```
**Vacío a propósito**. Si lo poblamos con `127.0.0.1`, MediaMTX
confiaría en `X-Forwarded-For` de nginx y tomaría la IP pública del
cliente como "IP origen". Eso rompe la Regla 1 (que solo permite
read desde IPs privadas) y devuelve `authentication failed`. Vacío,
MediaMTX siempre usa la IP del socket TCP (= 127.0.0.1 vía nginx) y
matchea la regla. La trazabilidad de IPs queda en los logs de nginx.

```yaml
webrtcLocalUDPAddress: :8189
webrtcIPsFromInterfaces: true
```
**La línea más importante para que WebRTC funcione**.
`webrtcLocalUDPAddress` fija el puerto UDP (sin esto sería aleatorio,
imposible de abrir en el firewall). `webrtcIPsFromInterfaces` deja
que MediaMTX descubra las IPs locales del container.

```yaml
webrtcAdditionalHosts: [panel.dronefieldoperation.cloud]
```
Cuando MediaMTX ofrece "candidatos ICE" al navegador (direcciones por
las que puede recibir video), por defecto manda la IP interna del
container (`172.x.x.x`), inútil desde internet. Esta línea dice
"además, anuncia este hostname público".

```yaml
hls: false
rtsp: false
srt: false
```
Protocolos no usados, apagados.

```yaml
paths:
  dock-cam-dock-1:
    source: publisher
    record: false
  dron-cam-q1-angular:
    source: publisher
    record: false
  dron-cam-q1-infrarojo:
    source: publisher
    record: false
  dron-cam-q1-zoom:
    source: publisher
    record: false
```
Un path por cámara. `source: publisher` = "esperá a que alguien
publique acá". `record: false` = no guardamos al disco. Los nombres
deben coincidir **exactamente** con:
1. Los paths declarados en `authInternalUsers` arriba.
2. La parte final de la URL RTMP configurada en FlightHub.
3. El campo `path` del array `camaras` en `html/index.html`.

### 6.3. `html/index.html`

Dashboard que genera la grilla dinámicamente desde un array JS. Para
sumar/sacar/renombrar cámaras, solo se toca este array:

```js
const camaras = [
    { path: "dock-cam-dock-1",       titulo: "Dock 1" },
    { path: "dron-cam-q1-angular",   titulo: "Q1 - Angular" },
    { path: "dron-cam-q1-infrarojo", titulo: "Q1 - Infrarojo" },
    { path: "dron-cam-q1-zoom",      titulo: "Q1 - Zoom" },
];
```

El CSS usa `grid-template-columns: repeat(auto-fit, minmax(400px, 1fr))`
para que el grid se acomode solo según cuántas cámaras haya (2 cám =
2 columnas, 6 cám = 3 columnas en 2 filas, etc.).

Cada celda crea un `<iframe src="/webrtc/<path>/">` que carga el
reproductor mínimo de MediaMTX.

### 6.4. `nginxconfig.txt` (el nginx del host)

No es un archivo de config, son **las instrucciones** para configurar
el nginx que ya tiene el server cloud. Va en dos fases para resolver
el huevo-y-gallina del SSL (no se puede declarar `listen 443 ssl` sin
certs, y para tener certs hace falta nginx funcionando):

1. **FASE 1** — Config solo HTTP en
   `/etc/nginx/sites-available/drones.conf` con dos `location`:

   - `location /` → proxy al container del dashboard
     (`127.0.0.1:18082`).
   - `location /webrtc/` → proxy a MediaMTX (`127.0.0.1:8889`) con
     varias directivas críticas:
     - `rewrite ^/webrtc/(.*)$ /$1 break;` — quita el prefijo
       `/webrtc/` antes de mandarlo a MediaMTX (MediaMTX espera
       `/dronN/whep`, no `/webrtc/dronN/whep`).
     - `proxy_redirect ~^/(.*)$ /webrtc/$1;` — reescribe el header
       `Location` que devuelve MediaMTX, agregando el prefijo de
       vuelta. Sin esto, el flujo WHEP (POST → 201 + Location → PATCH)
       se rompe en el PATCH con 405 (ver Gotchas §11).
     - `proxy_set_header Authorization "";` — limpia el header si por
       alguna razón el browser lo enviara (defensa: si algún día se
       mete basic auth, no se confunde con MediaMTX).
     - `proxy_http_version 1.1` + `Upgrade` + `Connection "upgrade"` —
       habilita WebSockets, que WHEP usa en algunas implementaciones.
     - `proxy_read_timeout 86400` — 24 horas. WebRTC mantiene una
       conexión HTTP larga; sin esto, nginx la cortaría a los 60s.
     - `proxy_buffering off` — streaming, no podemos esperar a llenar
       buffers.

2. **Firewall** (UFW): abrir 80, 443, 1935/tcp y 8189/udp.

3. **FASE 2** — `certbot --nginx -d panel.dronefieldoperation.cloud`.
   Elegir opción "2: Redirect" cuando pregunte. Certbot agrega
   automáticamente el bloque `:443` con los certs llenos y convierte
   el `:80` en redirect 301.

4. **Renovación automática**: certbot instala un timer systemd que
   renueva cada 60 días. Verificable con `systemctl list-timers | grep
   certbot` y `sudo certbot renew --dry-run`.

> El dashboard está **público** en esta etapa (sin auth a nivel HTTP).
> Ver deuda técnica.

### 6.5. `.env` (residual)

En el ciclo de desarrollo este archivo guardaba la contraseña de
publish (`MEDIAMTX_PUBLISH_PASS`). Como FlightHub no soporta auth
embebida, ya no se usa. El archivo queda como plantilla por si en el
futuro se agrega un mecanismo de auth distinto (ver deuda técnica).

---

## 7. Despliegue paso a paso

En el server cloud:

```bash
# 1. Clonar el repo
git clone <url-del-repo>
cd centro-monitoreo

# 2. Levantar los containers
docker compose up -d
docker compose logs -f         # verificar que arranquen bien

# 3. Configurar el nginx del host
#    Seguir nginxconfig.txt fase por fase

# 4. Asegurar DNS apuntando a la IP del server
dig +short panel.dronefieldoperation.cloud

# 5. Emitir SSL
sudo certbot --nginx -d panel.dronefieldoperation.cloud

# 6. Probar en https://panel.dronefieldoperation.cloud
```

---

## 8. Configurar FlightHub 2 para publicar

Por cada cámara, en FlightHub 2 → *Canal de reenvío* → tipo **RTMP**.
En "Dirección del servidor":

```
rtmp://panel.dronefieldoperation.cloud:1935/dock-cam-dock-1
rtmp://panel.dronefieldoperation.cloud:1935/dron-cam-q1-angular
rtmp://panel.dronefieldoperation.cloud:1935/dron-cam-q1-infrarojo
rtmp://panel.dronefieldoperation.cloud:1935/dron-cam-q1-zoom
```

> ⚠ Sin user/pass: FlightHub 2 ignora las credenciales embebidas en
> la URL. La seguridad la da hoy la restricción de paths conocidos.

### Test sin dron, con ffmpeg

Para validar la cadena sin un dron real, desde tu compu local:

```bash
ffmpeg -re -f lavfi -i testsrc=size=1280x720:rate=30 \
       -c:v libx264 -preset veryfast -tune zerolatency \
       -f flv "rtmp://panel.dronefieldoperation.cloud:1935/dock-cam-dock-1"
```

Abrí el dashboard → en el cuadro "Dock 1" tiene que aparecer la
grilla de colores moviéndose.

---

## 9. Cómo agregar / quitar / renombrar cámaras

Hay que tocar **3 lugares** sincronizados:

1. **`mediamtx.yml`**:
   - Agregar `- action: publish, path: <nuevo>` en la regla 2 de
     `authInternalUsers`.
   - Agregar `<nuevo>: { source: publisher, record: false }` en la
     sección `paths:`.

2. **FlightHub 2**: editar el canal de reenvío y poner
   `rtmp://panel.dronefieldoperation.cloud:1935/<nuevo>` en "Dirección
   del servidor".

3. **`html/index.html`**: agregar al array `camaras` un objeto
   `{ path: "<nuevo>", titulo: "Nombre legible" }`.

Para aplicar:

```bash
git pull             # en el server
docker compose restart mediamtx
# El HTML se sirve directo del filesystem montado:
# basta Ctrl+F5 en el browser, sin restart del dashboard.
```

---

## 10. Cómo saber que algo funciona

| Qué probar              | Cómo                                                                 |
| ----------------------- | -------------------------------------------------------------------- |
| Containers arriba       | `docker compose ps` → ambos en `Up`                                  |
| Logs MediaMTX           | `docker compose logs -f mediamtx`                                    |
| Publicación llegó       | En los logs: `[RTMP] [conn] opened` + `is publishing to path '<nombre>', 1 track (H264)` |
| HTTPS válido            | Candadito en el navegador, sin warning                               |
| Dashboard accesible     | Abre directo, sin pedir credenciales                                 |
| Dashboard carga         | Se ve la grilla con los iframes                                      |
| Video llega al navegador| Se ve la imagen del dron. Si dice "stream not found" → revisar path. Si "Error 405" → revisar `proxy_redirect`. |

---

## 11. Gotchas que ya nos pegaron

Cosas concretas que rompieron el deploy durante el armado, documentadas
para que no vuelvan a pasar.

### Puertos de loopback ya ocupados

`127.0.0.1:8080–8082` suelen estar tomados por otros servicios del
server (paneles admin, Tomcat, etc.). Por eso el dashboard interno usa
`18082`. Si pifia el puerto, error típico:

```
Error response from daemon: failed to bind host port for
127.0.0.1:XXXX: address already in use
```

Para diagnosticar: `sudo ss -tlnp | grep :XXXX`. Si aparece
`docker-proxy`, suele ser un fantasma de un `up` anterior que falló;
se limpia con `docker compose down` o `sudo systemctl restart docker`.

### YAML 1.1 vs unmarshaler estricto de Go

MediaMTX parsea el YAML con el unmarshaler de Go:

- `yes`/`no` sin comillas son booleans en YAML 1.1.
- Si un campo declara `string` (como `rtmpEncryption`), hay que ponerlo
  entre comillas: `rtmpEncryption: "no"`.
- Si declara `bool` (como `record`), va sin comillas: `record: false`.
- Mezclar las dos cosas → `cannot unmarshal string into Go value of
  type bool` y crash en loop.

**Solución preventiva**: usar `true`/`false` para booleans y comillas
siempre en strings.

### Campos renombrados / removidos entre versiones de MediaMTX

- **`webrtcEncryption`**: existió en versiones viejas, ahora **no
  existe** (WebRTC siempre cifra con DTLS-SRTP). Algunas versiones
  crashean si está presente.
- **`webrtcAllowOrigin` → `webrtcAllowOrigins`** (singular string →
  plural array) desde 1.18. El viejo emite warning de deprecación.

**Solución preventiva**: pinear la versión a un tag específico (p.ej.
`bluenviron/mediamtx:1.18.1`) en vez de `latest`.

### El `Location` de WHEP no incluye el prefijo del proxy

Síntoma: el video carga un instante y después `Error: bad status code
405, retrying in some seconds`. En DevTools se ve un PATCH a
`https://.../<path>/whep/<id>` **sin** el prefijo `/webrtc/` que
devuelve 405.

Causa: el flujo WHEP es POST → 201 con `Location:` → PATCH a esa
Location para mandar candidatos ICE. MediaMTX genera el Location con
SU path interno (`/<path>/whep/<id>`), ignorando que está detrás de
un proxy con prefijo. El browser sigue ese Location absoluto, pega a
`/<path>/whep/...` (no matchea `location /webrtc/`), cae en
`location /` del dashboard, y devuelve 405.

**Solución**: `proxy_redirect ~^/(.*)$ /webrtc/$1;` en el `location
/webrtc/` de nginx, reescribiendo el header `Location`.

### FlightHub 2 ignora `user:pass@` en URLs RTMP

DJI no manda las credenciales aunque las pongas en la URL. RTMP llega
al server pero anónimo, y MediaMTX rechaza con `authentication failed`.

**Solución aplicada**: publish sin auth, restringido por paths
conocidos. Mitigación pendiente: whitelist por IP origen (rangos DJI
vistos: `121.30.0.0/16`, `183.201.0.0/16` aproximadamente).

### MediaMTX rechazando reads con "authentication failed"

Síntoma: iframes muestran `{"status":"error","error":"authentication
error"}` y logs de MediaMTX dicen:

```
INF [WebRTC] connection <ip-pública>:<port> failed to authenticate
```

Causa: `webrtcTrustedProxies` incluía `127.0.0.1`, así que MediaMTX
confiaba en el `X-Forwarded-For` de nginx y tomaba la IP pública como
"IP origen". La regla `user: any` con `ips: [privadas]` no matchea IP
pública.

**Solución**: `webrtcTrustedProxies: []`.

### El header `Authorization` se propaga al backend

Si alguna vez metés basic auth (o cualquier mecanismo que setee el
header `Authorization`) en nginx, el navegador lo manda en TODOS los
requests al mismo origin, incluyendo los proxeados a MediaMTX, que
intenta validarlos contra `authInternalUsers`, falla y devuelve auth
error. Además dispara un loop de re-prompt en el browser.

**Solución preventiva** (aplicada): `proxy_set_header Authorization
"";` en `location /webrtc/`.

### Pista de audio MPEG-4 AAC del dron descartada

MediaMTX loguea `WAR [WebRTC] [session ...] skipping track 2 (MPEG-4
Audio)` y el dashboard reproduce solo video. WebRTC no soporta MPEG-4
AAC nativamente; necesitaríamos transcodificar a Opus. Como el audio
de un dron no nos sirve para nada, lo dejamos así.

### `${VAR}` en el YAML solo se expande si la variable existe

MediaMTX expande `${VAR}` dentro del YAML, pero **solo si la variable
está en el env del proceso**. Si te olvidás el `.env` o la variable
está vacía, el string literal `${MEDIAMTX_PUBLISH_PASS}` queda como
"contraseña" — el publish parece "funcionar" pero con auth rota.
Confirmá con `docker compose config` que la variable se expande antes
de levantar.

---

## 12. Deuda técnica conocida

- **Auth de publish abierta**: cualquiera que sepa el dominio y los
  paths puede publicar. Mitigación inmediata: whitelist por IP origen
  en `authInternalUsers` con los rangos DJI. Para producción seria,
  mTLS o token rotativo.

- **Dashboard público**: cualquiera con el dominio entra. Para
  producción: OAuth (Auth0/Keycloak), basic auth con `htpasswd`, o
  login dentro de una app real.

- **Sin multi-tenancy**: todo el mundo ve todos los drones. No hay
  concepto de "esta empresa solo ve sus equipos".

- **Sin monitoreo de salud**: no detectamos automáticamente cuando un
  stream se cae. Conviene un healthcheck que pingue WHEP cada N
  segundos y avise por algún canal.

- **Sin grabación**: si en algún momento se necesita guardar vuelos,
  cambiar `record: false` por `record: true` en los paths y dimensionar
  disco.

- **Audio descartado**: ver Gotcha §11. Si en algún momento se
  necesita audio, configurar transcoder externo (ffmpeg) o un path con
  `runOnDemand` que transcodifique AAC → Opus.

- **`latest` en MediaMTX**: la imagen no está pineada. Un update
  remoto puede traer cambios incompatibles (ver §11, "campos
  renombrados"). Pinear a `bluenviron/mediamtx:1.18.1` o el tag
  estable que se valide.

- **Sin tests automatizados**: el deploy es manual. Conviene un
  pipeline mínimo que valide `nginx -t`, `docker compose config`,
  y un curl al WHEP de un path conocido.

- **Convivencia con otros sitios**: el server hospeda varios sitios.
  Cambios a `nginx.conf` global o reinicios de Docker afectan a todos.
  Revisar `nginx -t` (mira warnings de `conflicting server name`) antes
  de aplicar.

---

## 13. Mapa de puertos

| Puerto | Proto | Acceso   | Quién lo usa                           |
| ------ | ----- | -------- | -------------------------------------- |
| 80     | TCP   | público  | Redirect a HTTPS + ACME challenges     |
| 443    | TCP   | público  | HTTPS del dashboard                    |
| 1935   | TCP   | público  | RTMP ingesta de FlightHub 2            |
| 8189   | UDP   | público  | WebRTC ICE (paquetes de video)         |
| 18082  | TCP   | loopback | Container dashboard → nginx host       |
| 8889   | TCP   | loopback | Container MediaMTX (WHEP) → nginx host |

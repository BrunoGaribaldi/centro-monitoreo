# Centro de Monitoreo de Drones

Dashboard web para visualizar, en vivo y con baja latencia, los streams
de video que publican los drones desde **DJI FlightHub 2**. Pensado para
desplegarse en un único servidor en la nube y atender a varios
operadores conectados desde un navegador.

**Dominio de producción:** `panel.dronefieldoperation.cloud`

---

## 1. El problema que resuelve este sistema

FlightHub 2 puede *retransmitir* el video de un dron a un servidor
externo usando el protocolo **RTMP**. Pero RTMP es un protocolo viejo
(Flash, 2002) que los navegadores modernos **ya no pueden reproducir
directamente**: no hay forma de poner un `<video src="rtmp://...">` y
que ande.

Necesitamos entonces un componente intermedio que:

1. Reciba el RTMP que envía FlightHub 2.
2. Lo "traduzca" a un protocolo que sí entiendan los navegadores.
3. Mantenga la latencia baja (un operador que ve un dron volando no
   puede esperar 10 segundos como en YouTube).

La traducción que elegimos es a **WebRTC**, el mismo protocolo que
usan Google Meet, Zoom Web y similares. Es el más rápido disponible en
navegador (≈ 0.5–2 segundos de latencia, frente a 5–20 segundos de HLS
o DASH).

---

## 2. La cadena completa, paso a paso

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
                                │ TLS + auth    │ proxy   │  (nginx)     │
                                └───────────────┘         └──────────────┘
                                       ▲
                                       │ HTTPS 443
                                       │
                                  operador en
                                  un navegador
```

Lo que pasa en orden:

1. El dron emite video. FlightHub 2 lo levanta y lo **publica** al
   servidor cloud usando RTMP push al puerto `1935`.
2. **MediaMTX** corre en ese servidor y acepta el RTMP. Lo guarda en un
   "path" identificado por un nombre (`dron1`, `dron2`).
3. Un operador abre `https://panel.dronefieldoperation.cloud` en el
   navegador.
4. El **nginx del host** termina el TLS y pasa la petición al container
   del dashboard, que devuelve el HTML. **(El dashboard está público en
   etapa de pruebas — sin auth a nivel HTTP. Ver §12 para la deuda
   técnica.)**
5. El HTML contiene `<iframe src="/webrtc/dron1/">`. El navegador
   pide ese iframe al mismo nginx, que lo redirige a MediaMTX.
6. MediaMTX sirve un reproductor mínimo que negocia una sesión WebRTC
   con el navegador usando el protocolo **WHEP**.
7. Una vez negociada, el video fluye **directo del servidor al navegador
   por UDP/8189** (no pasa por nginx). Esa es la magia de WebRTC: usa el
   transporte más eficiente.

---

## 3. Glosario rápido de protocolos

| Sigla     | Qué es                                                           | Quién lo habla                |
| --------- | ---------------------------------------------------------------- | ----------------------------- |
| **RTMP**  | Protocolo viejo de Adobe Flash, todavía estándar de facto para *publicar* video a un servidor desde drones, OBS, cámaras IP, etc. | FlightHub 2 → MediaMTX        |
| **WebRTC**| Protocolo moderno P2P para audio/video en tiempo real. Lo soportan todos los navegadores. Latencia baja. | MediaMTX → Browser           |
| **WHEP**  | "WebRTC-HTTP Egress Protocol". Una forma estandarizada de pedir un stream WebRTC con una sola petición HTTP. Lo usa el reproductor de MediaMTX. | Browser ↔ MediaMTX            |
| **ICE**   | "Interactive Connectivity Establishment". Mecanismo que WebRTC usa para descubrir por dónde puede mandar los paquetes de video (UDP, TCP, vía qué IP, etc.). | Negociación interna WebRTC    |
| **TLS**   | Cifrado de tráfico HTTP (lo que pone el candadito en el navegador). Lo terminamos en el nginx del host con certificados de Let's Encrypt. | Browser ↔ nginx host          |

---

## 4. Tecnologías que elegimos y por qué

### MediaMTX

Es un servidor de medios open source escrito en Go, ligero, sin
dependencias, que recibe video por un protocolo y lo re-emite por otro.
Soporta RTMP, RTSP, HLS, SRT, **WebRTC** y más.

**Por qué no otra opción:**
- *Nginx-rtmp-module*: solo recibe RTMP y emite HLS (alta latencia). No
  habla WebRTC.
- *OvenMediaEngine*: muy bueno, pero más pesado y con curva de
  aprendizaje más alta.
- *Janus / mediasoup*: SDKs WebRTC potentes pero requieren escribir
  código de servidor. MediaMTX viene listo "out of the box".

### Docker + Docker Compose

Empaquetamos MediaMTX y el server de HTML estático como containers.
Razones:

- **Reproducibilidad**: el `docker-compose.yml` describe la
  infraestructura entera. Cualquier máquina con Docker la levanta igual.
- **Aislamiento**: si MediaMTX crashea, no se lleva al sistema con él.
- **Versiones fijas**: la imagen `bluenviron/mediamtx:latest` se puede
  pinear a un tag específico cuando vayamos a producción para evitar
  upgrades inesperados.

### Dos nginx (uno en host, otro en container)

Es una decisión deliberada:

- **Nginx del host**: ya viene instalado en el servidor, tiene TLS
  (certbot), basic auth, redirect HTTP→HTTPS. Es el "borde" público.
- **Nginx en container** (`dashboard_web`): solo sirve los HTML/CSS/JS
  estáticos del dashboard, separado del resto.

Alternativa que descartamos por ahora: mover el nginx-borde adentro de
Docker. Es más limpio pero requiere rearmar la cadena de certbot y
abandonar el nginx que el server ya tenía.

### WebRTC en vez de HLS

| Tecnología | Latencia       | Complejidad |
| ---------- | -------------- | ----------- |
| HLS        | 5–20 segundos  | Muy baja    |
| LL-HLS     | 2–6 segundos   | Media       |
| **WebRTC** | **0.5–2 seg**  | Media       |

Para monitorear drones en operación, la latencia importa: un evento
que vemos con 10 segundos de delay es un evento sobre el que ya no
podemos reaccionar. Por eso pagamos la complejidad extra de WebRTC
(necesita puerto UDP abierto, ICE, anuncio de host).

---

## 5. Estructura del repo

```
.
├── docker-compose.yml      # Define los dos containers
├── mediamtx.yml            # Config de MediaMTX
├── nginxconfig.txt         # Instrucciones para el nginx del host
├── html/
│   └── index.html          # Dashboard estático
├── .env.example            # Plantilla de variables sensibles
├── .gitignore
└── Readme.md               # Este archivo
```

---

## 6. Cada archivo, línea por línea

### 6.1. `docker-compose.yml`

```yaml
services:
  mediamtx:
    image: bluenviron/mediamtx:latest
```
Container que corre MediaMTX. Usamos la imagen oficial de Docker Hub.

```yaml
    container_name: mediamtx_drones
    restart: unless-stopped
```
Le damos un nombre fijo (más fácil de inspeccionar con
`docker logs mediamtx_drones`) y le decimos a Docker que lo
re-arranque siempre, salvo que lo paremos a mano.

```yaml
    environment:
      MEDIAMTX_PUBLISH_PASS: ${MEDIAMTX_PUBLISH_PASS}
```
Le pasamos la contraseña del publisher como variable de entorno.
`${MEDIAMTX_PUBLISH_PASS}` se reemplaza por lo que esté en el archivo
`.env`, así la clave **no queda en el repo**.

```yaml
    volumes:
      - ./mediamtx.yml:/mediamtx.yml:ro
```
Montamos nuestro `mediamtx.yml` adentro del container, en la ruta donde
MediaMTX espera encontrar su config. `:ro` = read-only (defensa en
profundidad: aunque el proceso se comprometa, no puede modificar su
propia config).

```yaml
    ports:
      - "1935:1935/tcp"
```
RTMP de ingesta. Mapeo `0.0.0.0:1935 (host) → 1935 (container)`. Es
**público** porque FlightHub 2 vive afuera y necesita poder conectarse.
La seguridad la maneja MediaMTX con `publishUser`/`publishPass`.

```yaml
      - "8189:8189/udp"
```
Puerto UDP para los paquetes de video de WebRTC (los "candidatos
ICE"). **También público**. Sin este puerto abierto, la negociación
ICE falla y el navegador nunca recibe video.

```yaml
      - "127.0.0.1:8889:8889/tcp"
```
Puerto HTTP de **señalización** WebRTC (WHEP). Lo bindeamos solo a
`127.0.0.1` para que **no sea accesible desde internet**; lo único que
le habla es el nginx del host (mismo localhost), que sí está expuesto.

```yaml
    networks:
      - monitoring_net
```
Lo metemos en una red Docker propia. Por ahora los dos containers no
se hablan entre sí, pero queda preparada para futuras adiciones.

```yaml
  dashboard_web:
    image: nginx:alpine
```
Container del dashboard. Usamos nginx con base Alpine porque es la
imagen más liviana (≈ 25 MB).

```yaml
    volumes:
      - ./html:/usr/share/nginx/html:ro
```
Montamos la carpeta `html/` local en el path donde nginx busca los
archivos a servir.

```yaml
    ports:
      - "127.0.0.1:18082:80/tcp"
```
Solo accesible desde localhost. El que lo expone al mundo (con TLS y
auth) es el nginx del host. Usamos 18082 (puerto alto, fuera del
rango habitual) para evitar choques con otros servicios del servidor.

```yaml
networks:
  monitoring_net:
    driver: bridge
```
Define la red bridge usada por ambos containers.

### 6.2. `mediamtx.yml`

> **Notas sobre el YAML**: en este archivo usamos `true`/`false` para
> los booleans (en vez de `yes`/`no`) y comillas explícitas en los
> strings. MediaMTX parsea el YAML con el unmarshaler de Go, que es
> estricto: si declara un campo como `bool` y le llega `"no"` con
> comillas, falla con `cannot unmarshal string into Go value of type
> bool`. El estilo `true`/`false` evita por completo esa ambigüedad.

```yaml
logLevel: info
logDestinations: [stdout]
```
Nivel de log estándar; salida por stdout para que `docker logs` la
muestre.

```yaml
api: false
metrics: false
playback: false
```
Apagamos endpoints administrativos que no usamos. Defensa en
profundidad: menos superficie de ataque.

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
Primera regla de autenticación: **lectura libre desde IPs privadas**.
El nginx del host le habla a MediaMTX desde `127.0.0.1`, así que entra
sin auth. La autenticación de los usuarios reales la hace el basic
auth en nginx, no acá.

```yaml
  - user: publisher
    pass: ${MEDIAMTX_PUBLISH_PASS}
    ips: []
    permissions:
      - action: publish
        path: dron1
      - action: publish
        path: dron2
```
Segunda regla: para **publicar** hace falta el usuario `publisher` con
la contraseña del `.env`. Y solo puede publicar en los paths que
declaramos. Cualquier otro intento (publish en `/loquesea`) es
rechazado.

```yaml
rtmp: true
rtmpAddress: :1935
rtmpEncryption: "no"
```
Activa el listener RTMP en todas las interfaces del container, puerto
1935. `rtmpEncryption` es un string (no un bool): valores válidos
`"no"`, `"optional"`, `"strict"`. Dejamos `"no"` porque FlightHub 2
publica RTMP plano.

```yaml
webrtc: true
webrtcAddress: :8889
```
Activa la señalización WebRTC (WHEP) en HTTP/8889.

```yaml
webrtcAllowOrigins: ["https://panel.dronefieldoperation.cloud"]
```
CORS: solo aceptamos peticiones WHEP que vengan desde nuestro dominio.
Evita que otra página externa embeba nuestros streams. **Ojo**: en
versiones de MediaMTX < 1.18 este campo se llamaba `webrtcAllowOrigin`
(singular, string). A partir de 1.18 se llama `webrtcAllowOrigins`
(plural, array). Si hacés downgrade, hay que volver al nombre viejo.

```yaml
webrtcTrustedProxies: []
```
**Vacío a propósito**. Si lo poblamos con `127.0.0.1`, MediaMTX
confiaría en el `X-Forwarded-For` que envía nginx y tomaría la IP
pública del cliente como la "IP origen". Pero eso rompe la regla
`user: any` con `ips: [127.0.0.1, redes privadas]`: una IP pública
no matchea, y MediaMTX responde "authentication failed". Dejándolo
vacío, MediaMTX siempre usa la IP del socket TCP (que es 127.0.0.1
cuando viene por nginx) y matchea la regla. La trazabilidad de IPs
reales queda en los logs de nginx, que sí ven al cliente.

```yaml
webrtcLocalUDPAddress: :8189
webrtcIPsFromInterfaces: true
```
**`webrtcLocalUDPAddress` es la línea más importante para que WebRTC
funcione**. Le dice a MediaMTX que use UDP/8189 como puerto fijo para
todos los paquetes de video. Sin este setting, MediaMTX usaría un
puerto UDP aleatorio cada vez, imposible de abrir en el firewall.
`webrtcIPsFromInterfaces: true` deja que MediaMTX descubra las IPs
locales del container automáticamente (sumado a `webrtcAdditionalHosts`
de abajo, son los candidatos ICE que se le ofrecen al navegador).

```yaml
webrtcAdditionalHosts: [panel.dronefieldoperation.cloud]
```
Cuando MediaMTX le ofrece "candidatos ICE" al navegador (las
direcciones por las que puede recibir el video), por defecto le
manda la IP interna del container (algo tipo `172.x.x.x`), que es
inútil desde internet. Esta línea le dice "además, anuncia este
hostname". El navegador resuelve el dominio a la IP pública y se
conecta ahí.

```yaml
hls: false
rtsp: false
srt: false
```
Apagamos los protocolos que no usamos.

```yaml
paths:
  dron1:
    source: publisher
    record: false
  dron2:
    source: publisher
    record: false
```
Declaramos dos paths. `source: publisher` = "esperá a que alguien
publique en esta ruta". `record: false` = no guardamos en disco.

### 6.3. `nginxconfig.txt` (el nginx del host)

No es un archivo de config, son **las instrucciones** para configurar
el nginx que ya tiene el servidor cloud. El despliegue va en dos
fases para resolver el huevo-y-gallina del SSL (no se puede declarar
`listen 443 ssl` sin tener certificados, y para tener certificados
hace falta nginx funcionando):

1. **FASE 1** — Sitio solo HTTP en `/etc/nginx/sites-available/drones.conf`
   con dos `location`:

   - `location /` → proxy al container del dashboard (`127.0.0.1:18082`).
   - `location /webrtc/` → proxy a MediaMTX (`127.0.0.1:8889`) para la
     señalización WebRTC. Detalles:
     - `rewrite ^/webrtc/(.*)$ /$1 break;` quita el prefijo `/webrtc/`
       antes de mandarlo a MediaMTX (MediaMTX espera URLs como
       `/dron1/whep`, no `/webrtc/dron1/whep`).
     - `proxy_set_header Authorization "";` — limpia el header de auth
       que pudiera venir del browser (ver §11 "Gotchas").
     - `proxy_http_version 1.1` + `Upgrade` + `Connection "upgrade"`:
       habilita WebSockets, que WHEP usa en algunas implementaciones.
     - `proxy_read_timeout 86400`: 24 horas. WebRTC mantiene una
       conexión HTTP larga; sin esto, nginx la cortaría a los 60s.
     - `proxy_buffering off`: streaming, no podemos esperar a llenar
       buffers.

2. **Firewall** (UFW): abrir 80, 443, 1935 TCP y 8189 UDP.

3. **FASE 2** — `certbot --nginx -d panel.dronefieldoperation.cloud`.
   Elegir opción 2 cuando pregunte por redirect HTTP→HTTPS. Certbot
   agrega automáticamente el bloque `:443` con los certificados llenos
   y convierte el `:80` en redirect 301.

4. **Renovación automática**: el paquete certbot deja instalado un
   timer systemd. Verificable con `systemctl list-timers | grep certbot`
   y `sudo certbot renew --dry-run`.

> El dashboard queda **público** (sin auth a nivel HTTP) en esta etapa
> de pruebas. Ver §12 para la deuda técnica de auth real.

### 6.4. `html/index.html`

Dashboard mínimo: sidebar a la izquierda con la lista de dispositivos
y una grilla 2x2 con `<iframe>`s. Cada iframe apunta a
`/webrtc/dronN/`, que MediaMTX sirve con su reproductor WebRTC
embebido.

Es intencionalmente simple: para esta etapa no necesitamos un frontend
con framework. Cuando crezca, este archivo se reemplaza por un build
de React/Vue/lo-que-sea.

### 6.5. `.env` (no commiteado, basado en `.env.example`)

Único archivo con secretos. Define `MEDIAMTX_PUBLISH_PASS`. Generala
con `openssl rand -base64 24` o similar.

---

## 7. Despliegue paso a paso

En el servidor cloud:

```bash
# 1. Clonar el repo
git clone <url-del-repo>
cd "Centro Monitoreo"

# 2. Crear .env con la clave de publicación
cp .env.example .env
nano .env   # poner una clave fuerte

# 3. Levantar los containers
docker compose up -d
docker compose logs -f         # ver que arranquen bien

# 4. Configurar el nginx del host
#    Seguir los pasos de nginxconfig.txt en orden

# 5. Apuntar el DNS de panel.dronefieldoperation.cloud
#    a la IP pública del servidor (registro A)

# 6. Emitir SSL
sudo certbot --nginx -d panel.dronefieldoperation.cloud

# 7. Probar
#    Abrir https://panel.dronefieldoperation.cloud
```

---

## 8. Cómo empezar a publicar video

### Desde FlightHub 2

En FlightHub 2 → *Canal de reenvío* → tipo **RTMP**:

```
rtmp://publisher:<MEDIAMTX_PUBLISH_PASS>@panel.dronefieldoperation.cloud:1935/dron1
```

Mismo formato con `/dron2` para el segundo dron.

Si la UI de FlightHub no acepta `user:pass@` en la URL, alternativa:

```
rtmp://panel.dronefieldoperation.cloud:1935/dron1?user=publisher&pass=<PASS>
```

### Desde ffmpeg (test de humo)

Para validar la cadena sin un dron, podés mandar un patrón de prueba:

```bash
ffmpeg -re -f lavfi -i testsrc=size=1280x720:rate=30 \
       -f lavfi -i sine=frequency=440 \
       -c:v libx264 -preset veryfast -tune zerolatency \
       -c:a aac -ar 44100 -b:a 128k \
       -f flv "rtmp://publisher:<PASS>@panel.dronefieldoperation.cloud:1935/dron1"
```

Abrí el dashboard y deberías ver una grilla de colores moviéndose.

---

## 9. Cómo saber que algo funciona

| Qué probar              | Cómo                                                                 |
| ----------------------- | -------------------------------------------------------------------- |
| Containers arriba       | `docker compose ps` → ambos en `Up`                                  |
| Logs MediaMTX           | `docker logs -f mediamtx_drones`                                     |
| Publicación llegó       | En los logs: `[RTMP] [conn] opened` + `[path dron1] [publisher] ...` |
| HTTPS válido            | Candadito en el navegador, sin warning                               |
| Dashboard accesible     | Abre directo, sin pedir credenciales (modo prueba sin auth)          |
| Dashboard carga         | Ves la grilla con los iframes                                        |
| Video llega al navegador| Ves la imagen del dron en `<iframe>`. Si ves "loading" eterno → ICE  |

---

## 10. Problemas comunes y dónde mirar

**"Página carga pero los iframes no muestran nada."**
WebRTC no está negociando. Verificá:
- `8189/udp` abierto en el firewall del servidor.
- `webrtcAdditionalHosts` apunta al dominio correcto.
- Logs de MediaMTX: buscar mensajes de ICE.
- En el navegador, DevTools → Console: buscar errores WebRTC.

**"FlightHub dice 'no se puede conectar'."**
- ¿DNS resuelve? `dig panel.dronefieldoperation.cloud`.
- ¿`1935/tcp` abierto? `nc -zv panel.dronefieldoperation.cloud 1935`.
- ¿Contraseña correcta en la URL?

**"`nginx -t` falla con 'unknown directive auth_basic'."**
Faltaría el módulo `http_auth_basic_module`. Las builds estándar de
Debian/Ubuntu lo traen, pero si compilaste vos, hay que recompilar.

**"Certbot dice que no puede validar el dominio."**
- ¿Puerto 80 abierto en firewall?
- ¿DNS propagado? Esperá unos minutos.

---

## 11. Gotchas que ya nos pegaron (y cómo evitarlos)

Cosas concretas que rompieron el deploy durante el armado inicial,
documentadas para que no vuelvan a pasar:

### Puertos de loopback ya ocupados

Los rangos `127.0.0.1:8080-8082` son los primeros que la gente prueba
y suelen estar tomados por otros servicios (paneles admin, Tomcat,
viejos containers olvidados). Por eso usamos `18082` para el dashboard
interno. Si pifia el puerto, el síntoma típico es:

```
Error response from daemon: failed to bind host port for
127.0.0.1:XXXX: address already in use
```

Para diagnosticar: `sudo ss -tlnp | grep :XXXX`. Si aparece
`docker-proxy`, suele ser un fantasma de un `docker compose up`
anterior que falló a mitad — se limpia con `docker compose down` o
`sudo systemctl restart docker` si están realmente trabados.

### YAML 1.1 vs unmarshaler estricto de Go

MediaMTX (escrito en Go) parsea su YAML con tipos estrictos. Los
gotchas:

- **`yes`/`no` sin comillas son booleans** (true/false) en YAML 1.1.
  Si un campo declara tipo `string` (como `rtmpEncryption`), hay que
  ponerlo entre comillas: `rtmpEncryption: "no"`. Si declara `bool`
  (como `record`), va sin comillas: `record: false`.
- **Mezclar las dos cosas explota**. Síntoma: `ERR: json: cannot
  unmarshal string into Go value of type bool` en loop infinito.
- **Solución preventiva**: usar `true`/`false` para todos los booleans
  (no `yes`/`no`), y comillas siempre en strings. Más verboso pero
  inequívoco.

### Campos renombrados / removidos entre versiones de MediaMTX

MediaMTX cambia nombres de campos entre versiones con relativa
frecuencia. Concretamente nos pasó con:

- **`webrtcEncryption`**: existió en versiones viejas, ahora **no
  existe** (WebRTC siempre va cifrado por diseño con DTLS-SRTP). Si
  está en el YAML, en algunas versiones lo ignora con un warning, en
  otras crashea el unmarshal.
- **`webrtcAllowOrigin` → `webrtcAllowOrigins`** (singular string →
  plural array) a partir de v1.18. El campo viejo sigue funcionando
  con un warning de deprecación.

**Solución preventiva**: pinear la versión de MediaMTX a un tag
específico (p.ej. `bluenviron/mediamtx:1.18.1` en el
`docker-compose.yml`) en vez de `latest`, para que un upgrade no
sorpresivo no rompa la config un día random.

### El `Location` de WHEP no incluye el prefijo del proxy

Síntoma: el video carga un instante y después tira `Error: bad status
code 405, retrying in some seconds`. En DevTools se ve un PATCH a
`https://panel.dronefieldoperation.cloud/dron1/whep/<id>` (**sin** el
prefijo `/webrtc/`) que devuelve 405.

Causa: el flujo WHEP es POST → 201 con `Location:` → PATCH a esa
Location para mandar candidatos ICE. MediaMTX genera el `Location`
con SU path interno (`/dron1/whep/<id>`), ignorando que está detrás
de un proxy con prefijo. El browser sigue ese Location absoluto al
dominio, pega a `/dron1/whep/...` (que no matchea `location /webrtc/`),
cae en `location /` del dashboard, y devuelve 405. Sin trickle ICE,
la sesión nunca completa y termina en `deadline exceeded`.

**Solución**: en el `location /webrtc/` de nginx,
`proxy_redirect ~^/(.*)$ /webrtc/$1;` reescribe el Location agregando
el prefijo de vuelta.

### FlightHub 2 no manda credenciales RTMP en formato `user:pass@`

Síntoma: en logs de MediaMTX, `[RTMP] [conn ...] closed: authentication
failed`, aunque la URL del canal de reenvío en FlightHub 2 está
escrita correctamente como `rtmp://publisher:admin@host:1935/dron1`.

Causa: DJI FlightHub 2 ignora las credenciales embebidas en la URL.
El RTMP llega al server, pero anónimo.

**Solución de prueba**: dejar publish sin auth en MediaMTX, restringido
a los paths conocidos (`dron1`, `dron2`). Para producción, agregar
whitelist por IP de origen (rangos DJI vistos en logs: `121.30.0.0/16`,
`183.201.0.0/16` aproximadamente).

### MediaMTX rechazando reads desde el navegador con "authentication failed"

Síntoma: los iframes muestran `{"status":"error","error":"authentication error"}`
y en los logs de MediaMTX aparece:

```
INF [WebRTC] connection <ip-publica-cliente>:<port> failed to authenticate: authentication failed
```

Causa: `webrtcTrustedProxies` en `mediamtx.yml` incluía `127.0.0.1`
(la IP de nginx). MediaMTX por eso confiaba en el `X-Forwarded-For`
que nginx le mandaba y tomaba la IP pública del cliente como "IP del
request". La regla `user: any` con `ips: [127.0.0.1, redes privadas]`
no matchea una IP pública → rechaza la lectura.

**Solución**: `webrtcTrustedProxies: []`. Ver §6.2 para la explicación
completa.

### El header `Authorization` se propaga al backend

Si alguna vez metés basic auth (o cualquier mecanismo que setee el
header `Authorization`) en nginx, el navegador lo va a mandar en
**todos** los requests al mismo origin, incluyendo los que nginx
proxea a MediaMTX. MediaMTX intenta validar esas credenciales contra
`authInternalUsers`, no encuentra match y devuelve:

```json
{"status":"error","error":"authentication error"}
```

**Solución preventiva** (la dejamos aplicada aunque hoy no haya basic
auth): en el `location /webrtc/` de nginx, `proxy_set_header
Authorization "";` stripea el header antes de pasarlo a MediaMTX. Así
MediaMTX ve un request sin auth desde 127.0.0.1 y matchea la regla
`user: any` que permite lectura desde IPs privadas.

### `${VAR}` en el YAML

MediaMTX expande variables de entorno escritas como `${VAR}` dentro
del YAML, pero **solo si la variable existe en el env del proceso**.
Si te olvidás el `.env` o la `MEDIAMTX_PUBLISH_PASS` está vacía,
literalmente el string `${MEDIAMTX_PUBLISH_PASS}` queda como
contraseña, y el publish va a parecer "que funciona" pero con auth
rota. Confirmá con `docker compose config` que la variable se expande
antes de levantar.

## 12. Lo que falta (deuda técnica conocida)

- **Auth de publish abierta**: hoy cualquiera que sepa el dominio
  puede publicar a `dron1`/`dron2`. Mitigación inmediata: whitelist por
  IP origen en `authInternalUsers` con los rangos DJI. Para producción
  conviene un mecanismo más sólido (token rotativo, mTLS).

- **Auth real**: el basic auth alcanza para pruebas. Para producción
  conviene un OAuth (Auth0, Keycloak) o al menos un login en la app.
- **Auth de viewers**: el dashboard está público (cualquiera con el
  dominio entra). Para producción, basic auth, OAuth, o login en la app.
- **Audio**: MediaMTX descarta la pista de audio del dron porque viene
  en MPEG-4 AAC y WebRTC no lo soporta directamente. Si en algún
  momento se necesita audio, configurar un transcoder (ffmpeg externo o
  un path con `runOnDemand`).
- **Multi-tenant**: hoy todo el mundo ve todos los drones. No hay
  concepto de "esta empresa solo ve sus equipos".
- **Monitoreo de salud**: no detectamos automáticamente cuando un
  stream se cae. Conviene un healthcheck que pingue WHEP cada N
  segundos.
- **Grabación**: está apagada explícitamente. Si se requiere después,
  cambiar `record: no` y dimensionar disco.
- **Hardening**: pinear versión de MediaMTX, limitar `restart` con
  tope, agregar resource limits a los containers.

---

## 13. Mapa de puertos (resumen)

| Puerto       | Proto | Acceso   | Quién lo usa                       |
| ------------ | ----- | -------- | ---------------------------------- |
| 80           | TCP   | público  | Redirect a HTTPS + ACME challenges |
| 443          | TCP   | público  | HTTPS del dashboard                |
| 1935         | TCP   | público  | RTMP ingesta de FlightHub 2        |
| 8189         | UDP   | público  | WebRTC ICE (paquetes de video)     |
| 18082        | TCP   | loopback | Container dashboard → nginx host   |
| 8889         | TCP   | loopback | Container MediaMTX → nginx host    |

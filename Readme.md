# Centro de Monitoreo de Drones · NQN Petrol

Sistema web que permite, en vivo y con baja latencia, ver desde un
navegador las cámaras de drones que publican video al servidor a través
de DJI FlightHub 2. Pensado para desplegarse en una sola VM en la nube,
con autenticación, permisos por usuario y soporte para múltiples
empresas clientes.

- **Dominio de producción:** `panel.dronefieldoperation.cloud`
- **Versión de MediaMTX en uso:** `v1.18.1`
- **Cámaras activas:** Dock 1, Q1 Angular, Q1 Infrarrojo, Q1 Zoom
- **Empresas activas:** Quintana Energy

---

## Tabla de contenidos

1. [Qué hace y por qué existe](#1-qué-hace-y-por-qué-existe)
2. [Glosario de tecnologías](#2-glosario-de-tecnologías-para-leer-antes)
3. [Arquitectura completa](#3-arquitectura-completa)
4. [Componentes del sistema](#4-componentes-del-sistema-uno-por-uno)
5. [Roles, permisos y usuarios](#5-roles-permisos-y-usuarios)
6. [Estructura del repo](#6-estructura-del-repo)
7. [Despliegue paso a paso en una VM nueva](#7-despliegue-paso-a-paso-en-una-vm-nueva)
8. [Operación diaria](#8-operación-diaria)
9. [Cómo verificar que todo funciona](#9-cómo-verificar-que-todo-funciona)
10. [Troubleshooting](#10-troubleshooting)
11. [Deuda técnica conocida](#11-deuda-técnica-conocida)
12. [Mapa de puertos](#12-mapa-de-puertos)
13. [Backups y mantenimiento](#13-backups-y-mantenimiento)

---

## 1. Qué hace y por qué existe

### El problema

Los drones DJI no transmiten directamente a un navegador. La nube de DJI
(FlightHub 2) puede *retransmitir* el video del dron a un servidor que
nosotros controlemos, pero solo por **RTMP** — un protocolo viejo
(Adobe Flash, 2002) que los navegadores modernos **no saben reproducir
directamente**.

Si ponés `<video src="rtmp://...">` en una página web, no funciona. RTMP
fue diseñado para reproductores Flash, que dejaron de existir.

Además, queremos que múltiples operadores (varios técnicos, supervisores,
clientes) puedan ver los drones desde cualquier navegador, con permisos
diferenciados: algunos ven todo, otros solo su empresa, otros solo un
dron específico.

### La solución

Un servidor cloud que:

1. **Recibe** el RTMP que envía FlightHub 2 (puerto 1935, público).
2. **Traduce** ese video a un protocolo moderno: **WebRTC**, el mismo
   que usan Google Meet, Zoom Web, Discord. Latencia de 0.5–2 segundos
   (HLS sería 5–20 segundos).
3. **Sirve** un dashboard HTML que se ve en cualquier navegador.
4. **Autentica** a cada usuario con un sistema profesional (Authelia,
   con TOTP / 2FA / brute force protection / auditoría).
5. **Autoriza** finamente qué cámaras ve cada usuario, en base a un
   archivo YAML con el organigrama de empresas, sitios y permisos.

Todo el sistema corre en **una sola VM** con **Docker**. Cuatro
containers convivientes detrás de un nginx público con HTTPS.

---

## 2. Glosario de tecnologías (para leer antes)

Si alguno de estos términos no te suena, vale la pena leerlo: el resto
del documento los usa todo el tiempo.

| Sigla / Concepto | Qué es | Para qué lo usamos |
|---|---|---|
| **RTMP** | Real-Time Messaging Protocol. Protocolo viejo (2002) para *publicar* video a un servidor. FlightHub 2 solo sabe hablar esto. | FlightHub 2 → MediaMTX (puerto 1935) |
| **WebRTC** | Web Real-Time Communications. Estándar moderno P2P para audio/video, soportado nativamente por todos los navegadores. Latencia muy baja. | MediaMTX → Browser (puerto 8189/UDP para los paquetes, 8889/TCP para señalización) |
| **WHEP** | WebRTC-HTTP Egress Protocol. Forma estándar de *pedir* un stream WebRTC con HTTP normal. | El navegador le pide al servidor "quiero ver tal cámara" |
| **ICE** | Interactive Connectivity Establishment. Subprotocolo de WebRTC que descubre por qué IP/puerto puede viajar el video (UDP directo, vía NAT, etc.). | Negociación entre browser y MediaMTX al inicio del stream |
| **DTLS-SRTP** | Cifrado obligatorio de WebRTC. Los paquetes de video viajan siempre encriptados — WebRTC no tiene "modo en claro". | Transparente, no se configura |
| **TLS / SSL** | El "candadito HTTPS". Cifra la comunicación entre el navegador y nuestro nginx. | Lo emite gratis [Let's Encrypt](https://letsencrypt.org) vía `certbot` |
| **nginx** | Servidor web y proxy reverso. Acepta requests HTTPS del exterior, los enruta a los containers internos. | El "borde" del sistema. Único componente que escucha en puertos públicos junto con MediaMTX |
| **Docker** | Tecnología para empaquetar aplicaciones en containers aislados. Cada componente corre en su propio container y se restarta solo si crashea. | Despliegue reproducible: cualquier máquina con Docker levanta lo mismo |
| **Docker Compose** | Herramienta para describir y levantar múltiples containers con un solo archivo YAML. | Definimos los 4 containers en `docker-compose.yml` |
| **MediaMTX** | Servidor de medios open source escrito en Go. Acepta RTMP/RTSP/HLS/WebRTC y los convierte entre sí. | El "traductor" RTMP → WebRTC |
| **Authelia** | Software open source de autenticación. Maneja login, sesiones con cookies, 2FA (TOTP), protección contra fuerza bruta, reset de contraseña, audit log. | El "guardia" en la puerta. Quién es usted? |
| **Argon2** | Función de hashing de contraseñas. Más fuerte que bcrypt o PBKDF2; ganadora del Password Hashing Competition (2015). | Authelia guarda los passwords como hash argon2 |
| **TOTP** | Time-based One-Time Password. Códigos de 6 dígitos que rotan cada 30 segundos. Lo genera una app (Google Authenticator, Authy, 1Password). | Segundo factor de autenticación, opcional |
| **Cookie de sesión** | Pequeño dato que el navegador guarda y manda en cada request. Authelia setea una al loguearte. | Reemplaza al JWT del sistema viejo |
| **JWT** | JSON Web Token. Un string firmado que prueba "soy fulano". Lo usaba el sistema viejo. **El sistema nuevo NO lo usa** salvo internamente. | Mencionado solo por historia |
| **FastAPI** | Framework de Python para hacer APIs HTTP. | Lo usa `auth-service` para exponer dos endpoints |
| **YAML** | Formato de archivo de configuración legible por humanos (basado en indentación). | `companies.yml`, `configuration.yml`, `docker-compose.yml`, etc. |
| **Reverse proxy** | Servidor que recibe requests y los reenvía a otro servidor interno. Lo que hace nginx con cada uno de los containers. | Por eso solo nginx escucha públicamente |

---

## 3. Arquitectura completa

### 3.1 Diagrama de bloques

```
                          INTERNET PÚBLICA
   ┌─────────────────────────────────────────────────────────────────┐
   │                                                                 │
   │   ┌──────────────┐                              ┌────────────┐  │
   │   │ FlightHub 2  │ ─── RTMP, puerto 1935 ───►   │            │  │
   │   │  (drones)    │                              │            │  │
   │   └──────────────┘                              │            │  │
   │                                                 │   nginx    │  │
   │   ┌──────────────┐                              │   del HOST │  │
   │   │   Operador   │ ─── HTTPS, puerto 443 ───►   │ (TLS proxy)│  │
   │   │ (navegador)  │ ◄── WebRTC video UDP 8189 ── │            │  │
   │   └──────────────┘                              │            │  │
   │                                                 └─────┬──────┘  │
   └───────────────────────────────────────────────────────┼─────────┘
                                                           │
                              loopback (127.0.0.1)         │ proxy
                                                           │ a containers
                       ┌───────────────┬───────────────────┼───────────────┐
                       │               │                   │               │
                       ▼               ▼                   ▼               ▼
                ┌────────────┐  ┌────────────┐      ┌────────────┐  ┌────────────┐
                │ MediaMTX   │  │ dashboard_ │      │  Authelia  │  │auth-service│
                │ 8889/TCP   │  │ web 18082  │      │  9091/TCP  │  │ 19100/TCP  │
                │ (RTMP+WHEP)│  │ (HTML stat)│      │ (login,2FA)│  │ (cameras,  │
                │            │  │            │      │            │  │  authz)    │
                └────────────┘  └────────────┘      └────────────┘  └────────────┘

                                  TODOS DENTRO DE LA MISMA VM
                                  (red Docker `monitoring_net`)
```

Lo que **se ve desde internet**: nginx (puertos 80/443/HTTPS), MediaMTX
RTMP (1935), WebRTC UDP (8189). Nada más.

Lo que **vive en `127.0.0.1` (loopback)**: los cuatro containers. Solo
nginx les habla. Nadie de afuera puede conectarse directamente.

### 3.2 Flujo de PUBLICACIÓN (el dron sube video)

1. El dron filma. FlightHub 2 levanta el feed.
2. En FlightHub 2 hay configurado un "canal de reenvío" RTMP que apunta
   a `rtmp://panel.dronefieldoperation.cloud:1935/<path-de-la-cámara>`.
3. Los paquetes RTMP llegan al puerto 1935 de la VM.
4. **MediaMTX** (escuchando en 1935) los acepta. Verifica que el path
   esté declarado en `mediamtx.yml` (`dock-cam-dock-1`, etc.).
5. MediaMTX deja el stream "vivo" en memoria, esperando consumidores.

Importante: FlightHub 2 **no manda credenciales** aunque le metas
`rtmp://user:pass@host/path` en la URL. Por eso el publish está abierto
y la seguridad la da la restricción a paths conocidos (cualquier intento
a un path no declarado es rechazado).

### 3.3 Flujo de VISUALIZACIÓN (operador ve el video)

1. Operador entra a `https://panel.dronefieldoperation.cloud/`.
2. **nginx del host** recibe el request HTTPS.
3. Antes de servir nada, nginx hace una sub-consulta interna (un
   `auth_request`) a **Authelia** preguntando "¿este browser tiene
   cookie de sesión válida?".
4. **Caso A — sin cookie o cookie inválida:** Authelia responde 401,
   nginx redirige al navegador a `/authelia/?rd=<URL-original>`. El
   usuario ve el portal de login de Authelia.
5. Operador escribe usuario + contraseña (y TOTP si está activado).
   Authelia valida contra `users_database.yml` (hashes argon2).
6. Authelia setea una cookie `authelia_session` en el navegador y lo
   redirige de vuelta al dashboard.
7. **Caso B — cookie válida:** nginx sigue al paso siguiente.
8. El navegador descarga `index.html` desde el container `dashboard_web`.
9. El JavaScript del dashboard hace `fetch("/center-auth/cameras")`. La
   cookie viaja sola. nginx vuelve a validar contra Authelia, e inyecta
   un header `Remote-User: <username>` antes de pasar el request al
   `auth-service`.
10. **`auth-service`** lee el header, busca al usuario en
    `companies.yml`, y devuelve la lista de cámaras que puede ver según
    su rol.
11. El dashboard renderiza un grid con un `<video>` por cámara.
12. Para cada `<video>`, el navegador hace un `POST` WebRTC al endpoint
    `/webrtc/<path>/whep`. nginx hace **dos** validaciones:
    - **#1**: ¿La cookie de Authelia es válida? (es decir: ¿está
      logueado?)
    - **#2**: ¿Este usuario puede ver *este path específico*? (consulta
      al auth-service, que mira `allowed_paths` o el rol)
13. Si ambas pasan, nginx proxea el request a MediaMTX (en
    `127.0.0.1:8889`), que negocia WebRTC con el navegador.
14. Tras la negociación, el video fluye **directo del puerto UDP 8189
    de la VM al navegador del operador**, sin pasar por nginx. Es
    UDP nativo, cifrado con DTLS-SRTP, latencia 0.5–2 segundos.

### 3.4 Resumen del modelo de seguridad

| Pregunta | Quién la responde |
|---|---|
| ¿Quién es este usuario? (autenticación) | **Authelia** (cookie + hash argon2 + opcional TOTP) |
| ¿Está intentando demasiados logins? | **Authelia** (regulation: 5 fallidos en 2 min = ban de 5 min) |
| ¿Pertenece a algún grupo válido? | **Authelia** (regla `access_control`) |
| ¿Puede ver ESTE path específico? | **`auth-service`** (lee `companies.yml`) |
| ¿Está autorizado a publicar RTMP? | **MediaMTX** (solo paths declarados, sin user/pass) |

---

## 4. Componentes del sistema (uno por uno)

Los cuatro containers, qué hace cada uno y por qué.

### 4.1 MediaMTX

- **Container:** `mediamtx_drones`
- **Imagen:** `bluenviron/mediamtx:latest`
- **Configuración:** [mediamtx.yml](mediamtx.yml)
- **Puertos:** 1935/TCP (RTMP público), 8189/UDP (video WebRTC público),
  8889/TCP (señalización WHEP, solo loopback)

Es el **servidor de medios**: acepta RTMP y lo traduce a WebRTC. Tiene
un *path* por cada cámara. Para sumar una cámara nueva hay que
declarar el path acá.

Por qué no otra alternativa:
- *Nginx-rtmp-module*: solo recibe RTMP y emite HLS (latencia alta),
  no habla WebRTC.
- *OvenMediaEngine*: muy bueno pero más pesado y curva de aprendizaje
  más alta.
- *Janus / mediasoup*: SDKs WebRTC potentes, pero requieren escribir
  código de servidor.

### 4.2 dashboard_web

- **Container:** `dashboard_drones`
- **Imagen:** `nginx:alpine` (~25 MB)
- **Sirve:** [html/index.html](html/index.html) + favicon + logo
- **Puerto:** 18082/TCP (solo loopback)

Es un **nginx ultra-simple** que solo sirve archivos estáticos: el HTML
del dashboard, el CSS embebido, el JavaScript embebido, el favicon, el
logo de NQN Petrol. No tiene lógica de servidor — toda la lógica está
en el JavaScript del navegador.

Para editar el dashboard, basta editar [html/index.html](html/index.html).
El volumen está montado read-only, así que el archivo del disco es la
fuente de verdad; un `Ctrl+F5` en el navegador refleja los cambios sin
restart del container.

### 4.3 auth_service

- **Container:** `auth_service`
- **Imagen:** Construida desde [auth-service/Dockerfile](auth-service/Dockerfile)
  (FastAPI + uvicorn + Python 3.12)
- **Configuración:** lee [companies.yml](companies.yml) montado como volumen
- **Puerto:** 19100/TCP (solo loopback)

Es un **microservicio Python** que expone dos endpoints HTTP:

1. **`GET /center-auth/cameras`** — el dashboard lo llama al cargar.
   Devuelve la lista de sitios y cámaras que el usuario puede ver, en
   formato JSON. Filtra según el rol del user (`companies.yml`).

2. **`GET /center-auth/authz-path`** — nginx lo llama internamente en
   cada request a `/webrtc/<path>/whep`. Devuelve 200 si el usuario
   puede ver ese path, 403 si no.

Ambos endpoints reciben el username vía el header `Remote-User` que
nginx inyecta tras validar la sesión contra Authelia. **El auth-service
no valida sesiones; confía en lo que nginx le pasa.**

### 4.4 Authelia

- **Container:** `authelia`
- **Imagen:** `authelia/authelia:4.38`
- **Configuración:** [authelia/configuration.yml](authelia/configuration.yml)
- **Usuarios:** [authelia/users_database.yml](authelia/users_database.yml.example)
  (el archivo real está gitignored, hay un `.example` para copiar)
- **Datos persistentes:** volume Docker `authelia_data` montado en
  `/config/data/` (SQLite con sesiones, dispositivos TOTP registrados,
  intentos de login)
- **Puerto:** 9091/TCP (solo loopback)

Es el **servicio de autenticación**. Maneja:

- Página de login (visible en `https://panel.dronefieldoperation.cloud/authelia/`)
- Validación de password (argon2)
- Cookies de sesión (8h de vida, 1h de inactividad)
- Configuración voluntaria de 2FA (TOTP) por usuario
- Brute force protection (`regulation`: ban temporal después de N fallos)
- Audit log (a stdout, ver `docker logs authelia`)
- Endpoint `/api/authz/auth-request` que nginx consulta en cada request

### 4.5 nginx del host (no es un container)

- **Configuración:** instrucciones en [nginxconfig.txt](nginxconfig.txt)
- **Vive:** en la VM, fuera de Docker (`/etc/nginx/sites-available/drones.conf`)
- **Puertos:** 80/TCP y 443/TCP (públicos)

Por qué **no** vive en Docker:
- El servidor cloud (`nqnpetrol`) hospeda otros sitios además de este
  (`qntdrones.com`, `app`, `n8n`, etc.). Mover nginx adentro de Docker
  rompería esa convivencia.
- `certbot` ya está integrado con el nginx del host. Migrar significaría
  rehacer el setup de SSL.

Funciones:
- Termina TLS con cert de Let's Encrypt (renovación automática cada 60
  días, gestionada por un timer systemd).
- Hace `auth_request` a Authelia en cada request al dashboard.
- Hace **doble** `auth_request` (Authelia + auth-service) en cada
  request a `/webrtc/*`.
- Inyecta los headers `Remote-User`, `Remote-Groups`, `Remote-Name`,
  `Remote-Email` antes de pasar requests a los upstreams.
- Reescribe el header `Location` que devuelve MediaMTX (necesario para
  que el flujo WHEP completo funcione).

---

## 5. Roles, permisos y usuarios

### 5.1 Los cuatro roles

Definidos en [companies.yml](companies.yml). Lo que cada uno ve:

| Rol | Empresa | Qué ve | Caso de uso |
|---|---|---|---|
| **`superadmin`** | (ninguna) | **TODAS las cámaras de TODAS las empresas.** | El dueño del centro de monitoreo (Bruno) |
| **`admin_empresa`** | Una sola | Todos los sitios y todas las cámaras de su empresa | Administrador completo del cliente |
| **`admin_site`** | Una sola | Solo los sitios listados en su campo `sites` | Supervisor de un yacimiento específico |
| **`viewer_drone`** | Una sola | Solo los paths listados en su campo `allowed_paths` | Operador que solo monitorea una cámara |

### 5.2 Cómo se decide qué ve cada usuario (paso a paso)

```
Usuario abre el dashboard
       │
       ▼
nginx pregunta a Authelia: ¿está logueado y en algún grupo válido?
       │
       ▼ (sí)
nginx inyecta Remote-User: <username>
       │
       ▼
Dashboard llama a /center-auth/cameras
       │
       ▼
auth-service busca <username> en companies.yml
       │
       ▼
Según el rol, filtra los sites y cameras:
   superadmin    → itera TODAS las companies de companies.yml
   admin_empresa → filtra por user["empresa"]
   admin_site    → además filtra por sites ∈ user["sites"]
   viewer_drone  → además filtra cameras donde path ∈ user["allowed_paths"]
       │
       ▼
Devuelve JSON al dashboard
       │
       ▼
Dashboard renderiza el grid con esas cámaras
```

### 5.3 Dos archivos para los usuarios: ¿por qué?

Esto puede confundir al principio. Hay **dos** archivos relacionados con
usuarios:

| Archivo | Para qué sirve | Qué contiene | ¿Va al git? |
|---|---|---|---|
| `companies.yml` | Mapping de usuario a permisos | username, empresa, rol, sites/allowed_paths | **Sí** |
| `authelia/users_database.yml` | Identidad y autenticación | username, password (hash argon2), email, displayname, groups | **No** (gitignored) |

El `username` debe coincidir EXACTAMENTE entre ambos archivos.

**Por qué separados:**
- `companies.yml` describe **la lógica de negocio** (qué empresa tiene
  qué sitios y cámaras, quién puede ver qué). Cambia cuando se agrega
  una cámara o un cliente nuevo.
- `users_database.yml` contiene **secretos** (los hashes de las
  contraseñas). Cambia cuando se agrega/quita un usuario o cuando alguien
  resetea su contraseña.

Authelia inyecta el header `Remote-User: <username>`. El auth-service usa
ese username para buscar al usuario en `companies.yml`. **Authelia no
sabe nada de empresas, sitios o cámaras; auth-service no sabe nada de
contraseñas.**

### 5.4 Grupos de Authelia

En `users_database.yml` cada usuario pertenece a un `groups: [...]`. Los
grupos que usamos:

| Grupo | Uso |
|---|---|
| `superadmins` | Para el rol `superadmin` |
| `quintana-admins` | Para `admin_empresa` de Quintana |
| `quintana-ca` | Para `admin_site` de Cañadón Amarillo |
| `quintana-operadores` | Para `viewer_drone` de Quintana |

Los grupos son **gruesos**: solo definen si el usuario puede entrar
*en general* a `/webrtc/*` (filtro a nivel Authelia). La granularidad
fina (qué path específico) la decide el auth-service usando
`companies.yml`. Por eso `users_database.yml` no necesita un grupo por
path individual — eso lo maneja la lógica de roles del auth-service.

Cuando se sume una empresa nueva (ej. ACME):
- Crear grupos: `acme-admins`, `acme-<site>`, etc.
- Agregarlos al `subject:` de la regla `/webrtc/*` en
  [authelia/configuration.yml](authelia/configuration.yml).
- El grupo `superadmins` ya da acceso porque es cross-empresa.

---

## 6. Estructura del repo

```
.
├── docker-compose.yml          # Define los 4 containers
├── mediamtx.yml                # Config del servidor de medios
├── nginxconfig.txt             # Instrucciones para el nginx del host
├── companies.yml               # Mapping usuario → empresa / rol / cámaras
├── .env.example                # Plantilla de secrets (copiar a .env)
├── .gitignore
├── Readme.md                   # Este archivo
│
├── html/
│   ├── index.html              # Dashboard (HTML + CSS + JS embebido)
│   ├── favicon.png             # Icono de la pestaña
│   └── images.png              # Logo NQN Petrol
│
├── auth-service/               # Microservicio Python de autorización
│   ├── Dockerfile              # Cómo construir la imagen
│   ├── requirements.txt        # Dependencias Python
│   └── main.py                 # Endpoints /cameras y /authz-path
│
└── authelia/                   # Configuración de Authelia
    ├── configuration.yml       # Reglas de access_control, sesiones, TOTP
    └── users_database.yml.example  # Plantilla de usuarios + hashes
       # users_database.yml (sin .example) es el archivo real
       # NO se commitea (tiene los hashes argon2 reales)
```

---

## 7. Despliegue paso a paso en una VM nueva

Esta sección asume **cero conocimientos** del stack. Si ya hiciste estos
pasos, podés saltar a la sección [Operación diaria](#8-operación-diaria).

### 7.1 Pre-requisitos

Necesitás:
1. Una VM con Ubuntu 22.04 LTS (o 24.04) y acceso por SSH.
   Recomendado: 2 vCPU, 4 GB RAM, 40 GB disco. La cantidad de tráfico
   depende de cuántas cámaras × cuántos espectadores simultáneos haya.
2. Un dominio que apunte por DNS a la IP pública de la VM. En este caso:
   `panel.dronefieldoperation.cloud`. Registro tipo `A` o `AAAA`.
3. Acceso de administrador (`sudo`) en la VM.
4. Los puertos 80, 443, 1935 (TCP) y 8189 (UDP) abiertos en el firewall
   del proveedor de la nube (AWS Security Group, GCP Firewall Rule,
   Azure NSG, etc.) — esto es **además** del UFW de la VM.

### 7.2 Conectarse a la VM y actualizar el sistema

Desde tu compu local:

```bash
ssh usuario@<ip-de-la-vm>
```

Una vez adentro:

```bash
sudo apt update
sudo apt upgrade -y
```

### 7.3 Instalar Docker y Docker Compose

```bash
# Instalar dependencias
sudo apt install -y ca-certificates curl gnupg lsb-release

# Agregar la clave GPG oficial de Docker
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
    sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Agregar el repo de Docker
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Instalar Docker + Compose plugin
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# (Opcional) Agregar tu usuario al grupo docker para no usar sudo
sudo usermod -aG docker $USER
# Cerrar sesión y volver a entrar para que tome efecto:
exit
# (volver a entrar por SSH)
```

Verificación:

```bash
docker --version
docker compose version
```

### 7.4 Instalar nginx y certbot

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

### 7.5 Clonar el repo

```bash
cd ~
git clone <url-del-repo> centro-monitoreo
cd centro-monitoreo
```

### 7.6 Generar el archivo `.env` con los secrets

El archivo `.env` contiene **claves secretas** que cifran las sesiones
de Authelia. Cada una tiene que ser distinta, larga y aleatoria.

```bash
# Copiar la plantilla
cp .env.example .env

# Generar tres secrets distintos
for key in AUTHELIA_SESSION_SECRET AUTHELIA_STORAGE_ENCRYPTION_KEY AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET; do
    value=$(openssl rand -base64 64 | tr -d '\n')
    echo "Generated $key"
    sed -i "s|^$key=.*|$key=$value|" .env
done

# Generar el password de publicación (hoy FlightHub no lo usa, pero por las dudas)
sed -i "s|^MEDIAMTX_PUBLISH_PASS=.*|MEDIAMTX_PUBLISH_PASS=$(openssl rand -base64 24 | tr -d '\n')|" .env

# Verificar
cat .env
```

> **CRÍTICO:** `.env` no se commitea (ya está en `.gitignore`). Si lo
> pierdes, las sesiones existentes se invalidan y la base SQLite de
> Authelia queda ilegible. Hacé backup del archivo (ver §13).

### 7.7 Generar el archivo `authelia/users_database.yml`

Acá viven los usuarios y sus contraseñas (como hash argon2).

```bash
# Copiar la plantilla
cp authelia/users_database.yml.example authelia/users_database.yml
```

Para cada usuario, generá un hash argon2 de su contraseña:

```bash
# Reemplazá 'mi-clave-aqui' por la contraseña real
docker run --rm authelia/authelia:4.38 \
    authelia crypto hash generate argon2 --password 'mi-clave-aqui'
```

El output será algo como:

```
Digest: $argon2id$v=19$m=65536,t=3,p=4$abc...xyz
```

Copiá el string completo (desde `$argon2id` hasta el final) y pegalo en
`authelia/users_database.yml` reemplazando `REEMPLAZAR_CON_HASH_ARGON2`
del usuario correspondiente.

Repetir para cada usuario. Cuando termines:

```bash
# Verificar que no quedó ningún placeholder
grep -n "REEMPLAZAR_CON_HASH_ARGON2" authelia/users_database.yml
# (no debe devolver nada)
```

### 7.8 Levantar los containers

```bash
docker compose up -d
```

Verificar que los 4 están en `Up`:

```bash
docker compose ps
```

Esperado:

```
NAME                IMAGE                            STATUS
auth_service        centro-monitoreo-auth_service    Up
authelia            authelia/authelia:4.38           Up
dashboard_drones    nginx:alpine                     Up
mediamtx_drones     bluenviron/mediamtx:latest       Up
```

Si alguno aparece como `Restarting` o `Exited`, ver los logs:

```bash
docker compose logs <nombre>
# Ejemplos:
docker compose logs authelia
docker compose logs auth_service
```

### 7.9 Verificar que los containers responden internamente

Antes de tocar el nginx público, comprobá que los servicios responden en
loopback:

```bash
# Authelia
curl -I http://127.0.0.1:9091/api/health
# Esperado: HTTP/1.1 200 OK

# auth-service (sin Remote-User devolverá 401 — es lo esperado)
curl -I http://127.0.0.1:19100/center-auth/cameras
# Esperado: HTTP/1.1 401 Unauthorized

# dashboard estático
curl -I http://127.0.0.1:18082/
# Esperado: HTTP/1.1 200 OK

# MediaMTX WHEP (sin path devuelve 404 — es lo esperado)
curl -I http://127.0.0.1:8889/
# Esperado: HTTP/1.1 404 Not Found
```

Si algún `curl` falla con `connection refused`, ese container no
arrancó bien. Revisar logs.

### 7.10 Verificar el DNS

```bash
dig +short panel.dronefieldoperation.cloud
```

Tiene que devolver la IP pública de la VM. Si no, esperar a que el DNS
propague (puede tardar minutos a horas).

### 7.11 Configurar el firewall (UFW)

```bash
# Permitir SSH primero (CRÍTICO: no te cierres tu propia sesión)
sudo ufw allow OpenSSH

# Puertos de la app
sudo ufw allow 80/tcp           # HTTP (redirect + certbot)
sudo ufw allow 443/tcp          # HTTPS dashboard
sudo ufw allow 1935/tcp         # RTMP de FlightHub 2
sudo ufw allow 8189/udp         # WebRTC ICE

# Activar
sudo ufw enable
sudo ufw status
```

Recordá que en la nube (AWS / GCP / Azure) también hay que abrir estos
puertos a nivel de Security Group / firewall del proveedor.

### 7.12 Configurar nginx — FASE 1 (HTTP, sin SSL)

Crear el archivo de sitio:

```bash
sudo nano /etc/nginx/sites-available/drones.conf
```

Pegar el bloque `server { ... }` que está en
[nginxconfig.txt](nginxconfig.txt) (el archivo lo muestra completo, en
español, con todos los comentarios; copialo entero).

Activar el sitio:

```bash
sudo ln -s /etc/nginx/sites-available/drones.conf /etc/nginx/sites-enabled/

# IMPORTANTE: si el server ya hospeda otros sitios, NO borres el default
# y NO borres los otros sitios habilitados.

# Validar la sintaxis
sudo nginx -t

# Si dice "syntax is ok" y "test is successful":
sudo systemctl reload nginx
```

Probar:

```bash
curl -I http://panel.dronefieldoperation.cloud/
# Esperado: HTTP/1.1 302 Found
# Location: http://panel.dronefieldoperation.cloud/authelia/?rd=...
```

Esto significa que nginx está activo y le está preguntando a Authelia.
Como aún no hay cookie, Authelia rechaza y nginx redirige al portal.

### 7.13 Emitir el certificado SSL con certbot — FASE 2

```bash
sudo certbot --nginx -d panel.dronefieldoperation.cloud
```

certbot va a:
1. Preguntar tu email (para notificaciones de renovación).
2. Pedirte aceptar los términos de Let's Encrypt.
3. Validar que el dominio apunta a esta VM (hace una request HTTP por
   sus medios).
4. Emitir el cert y guardarlo en `/etc/letsencrypt/`.
5. Preguntar si querés redirect HTTP→HTTPS. **Elegir opción "2:
   Redirect".**
6. Modificar `drones.conf` automáticamente: duplica el bloque `server{}`
   con `listen 443 ssl`, agrega las líneas `ssl_certificate`, y convierte
   el bloque `:80` original en un redirect 301.

Después:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Probar:

```bash
curl -I https://panel.dronefieldoperation.cloud/
# Esperado: HTTP/1.1 302 Found
# Location: https://panel.dronefieldoperation.cloud/authelia/?rd=...
```

### 7.14 Verificar que la renovación automática del cert está activa

certbot instaló un timer systemd. Para confirmar:

```bash
systemctl list-timers | grep certbot
sudo certbot renew --dry-run
```

El dry-run simula una renovación sin escribir nada. Si devuelve
"Congratulations, all simulated renewals succeeded", está OK.

### 7.15 Primer login y smoke test end-to-end

1. Abrí en el navegador: `https://panel.dronefieldoperation.cloud/`.
2. Debería redirigirte automáticamente al portal de Authelia.
3. Logueate con un usuario que esté en `authelia/users_database.yml`
   (ej. `bruno` con la contraseña que usaste al generar el hash).
4. Después del login, deberías volver al dashboard y ver el grid de
   cámaras.
5. Si todavía no hay un dron publicando, las celdas dirán "Sin señal".
   Eso es correcto: el dashboard funciona; falta el flujo de
   publicación (sección 8.7).

Pruebas rápidas:

```bash
# Como un usuario logueado en otro tab del navegador, abrí DevTools:
#   Network → recargá → buscá la request a /center-auth/cameras
#   Tiene que devolver 200 con un JSON: { empresa, rol, sites: [...] }

# Para probar que la cookie se borra al logout:
# Click en "Salir" → debería ir a /authelia/logout → portal de login.

# Para probar 401 en /center-auth/cameras sin cookie:
curl -I https://panel.dronefieldoperation.cloud/center-auth/cameras
# Esperado: 401 con JSON {"error":"unauthorized","login":"/authelia/"}
```

---

## 8. Operación diaria

### 8.1 Configurar FlightHub 2 para publicar

Por cada cámara, en FlightHub 2 → *Canal de reenvío* → tipo **RTMP**.

En "Dirección del servidor":

```
rtmp://panel.dronefieldoperation.cloud:1935/dock-cam-dock-1
rtmp://panel.dronefieldoperation.cloud:1935/dron-cam-q1-angular
rtmp://panel.dronefieldoperation.cloud:1935/dron-cam-q1-infrarojo
rtmp://panel.dronefieldoperation.cloud:1935/dron-cam-q1-zoom
```

> Sin user/pass: FlightHub 2 ignora las credenciales embebidas en la
> URL. La seguridad la da hoy la restricción a paths conocidos en
> `mediamtx.yml`.

#### Test sin un dron real (con ffmpeg)

Si no hay un dron disponible para probar la cadena entera, podés simular
un publisher desde tu compu local:

```bash
# Asumiendo ffmpeg instalado en tu máquina
ffmpeg -re -f lavfi -i testsrc=size=1280x720:rate=30 \
       -c:v libx264 -preset veryfast -tune zerolatency \
       -f flv "rtmp://panel.dronefieldoperation.cloud:1935/dock-cam-dock-1"
```

Abrí el dashboard → en el cuadro "Dock 1" debería aparecer un patrón de
colores en movimiento.

### 8.2 Agregar / sacar / renombrar una cámara

Hay que tocar **3 lugares** sincronizados. Asumamos que querés agregar
una cámara nueva `dron-cam-q1-termica`:

**1. [mediamtx.yml](mediamtx.yml):**
   - Bajo `authInternalUsers` → regla de publish: agregar
     ```yaml
     - action: publish
       path: dron-cam-q1-termica
     ```
   - Bajo `paths:` agregar
     ```yaml
     dron-cam-q1-termica:
       source: publisher
       record: false
     ```

**2. [companies.yml](companies.yml):**
   - En `companies.QUINTANA.sites.canadon-amarillo.cameras` agregar:
     ```yaml
     - path: dron-cam-q1-termica
       titulo: "Q1 · Térmica"
     ```

**3. FlightHub 2:**
   - Configurar el canal de reenvío de esa cámara para que publique a
     `rtmp://panel.dronefieldoperation.cloud:1935/dron-cam-q1-termica`.

Para aplicar:

```bash
cd ~/centro-monitoreo
git pull   # si estás trayendo cambios del repo
docker compose restart mediamtx auth_service
```

`mediamtx.yml` y `companies.yml` están montados como volumen
read-only, así que un `restart` los recarga. No hace falta `docker
compose down`.

### 8.3 Agregar / modificar / quitar usuarios

Hay que tocar **2 archivos**:

**1. [companies.yml](companies.yml):** definir el username + rol +
   permisos. Ejemplo de operador nuevo:

```yaml
users:
  - username: operador_termica
    empresa: QUINTANA
    rol: viewer_drone
    allowed_paths:
      - dron-cam-q1-termica
```

**2. `authelia/users_database.yml`:** la identidad (password + grupo).
Primero generar el hash:

```bash
docker run --rm authelia/authelia:4.38 \
    authelia crypto hash generate argon2 --password 'la-contraseña-real'
```

Después agregar:

```yaml
users:
  operador_termica:
    disabled: false
    displayname: "Operador Térmica"
    password: "$argon2id$v=19$m=65536,t=3,p=4$abc...xyz"
    email: operador.termica@quintana.example
    groups:
      - quintana-operadores
```

Authelia tiene `watch: true` configurado, así que **recarga automática**
cuando el archivo cambia (no necesitás restart del container).

Para confirmar:

```bash
docker logs authelia 2>&1 | tail -20
# Tiene que aparecer una línea tipo: "users database file reloaded"
```

#### Quitar un usuario (sin borrar)

Editar `users_database.yml` y poner:

```yaml
users:
  operador_termica:
    disabled: true   # ← bloqueado, no puede loguear
    # ... resto igual
```

Authelia recarga y rechaza nuevos logins de ese usuario. Si tiene una
sesión activa, dura hasta que expire (max 8h).

Para invalidar la sesión inmediatamente:

```bash
docker exec authelia rm -f /config/data/db.sqlite3
docker compose restart authelia
```

> **CUIDADO:** esto borra TODAS las sesiones y dispositivos TOTP de
> TODOS los usuarios. Solo en emergencias.

#### Borrar definitivamente un usuario

1. Quitar la entrada de `companies.yml`.
2. Quitar la entrada de `authelia/users_database.yml`.
3. Authelia recarga solo. Si tenía sesión, expira al rato (o hacer
   restart de Authelia para forzar).

### 8.4 Cambiar la contraseña de un usuario

1. Generar nuevo hash: `docker run --rm authelia/authelia:4.38 authelia crypto hash generate argon2 --password 'nueva-clave'`
2. Pegar el hash en `authelia/users_database.yml` reemplazando el
   anterior.
3. Authelia recarga solo. Las sesiones existentes del usuario siguen
   válidas hasta que expiren — si querés cortarlas inmediatamente,
   marcar `disabled: true` un instante y después volver a `false`.

### 8.5 Agregar una empresa nueva

Asumamos la empresa **ACME** con un sitio **Site1** y dos cámaras
**cam-a** y **cam-b**.

**1. [companies.yml](companies.yml):**

```yaml
companies:
  QUINTANA:
    # ... (lo existente)
  ACME:
    display: "ACME Industrial"
    sites:
      site1:
        display: "Site 1"
        cameras:
          - path: acme-cam-a
            titulo: "Cámara A"
          - path: acme-cam-b
            titulo: "Cámara B"

users:
  # ... (los existentes)
  - username: admin_acme
    empresa: ACME
    rol: admin_empresa
```

**2. [mediamtx.yml](mediamtx.yml):** agregar los paths nuevos (en la
regla de publish y en `paths:`).

**3. [authelia/users_database.yml](authelia/users_database.yml):**
agregar el usuario `admin_acme` con grupo `acme-admins`.

**4. [authelia/configuration.yml](authelia/configuration.yml):** agregar
`group:acme-admins` (y cualquier otro grupo de ACME) a la regla
`/webrtc/.*` en `subject:`.

**5. FlightHub 2:** configurar publicación de las nuevas cámaras a los
paths declarados.

**Aplicar:**

```bash
docker compose restart mediamtx auth_service
# Authelia se recarga sola (watch: true).
# nginx no necesita reload (no cambió su config).
```

### 8.6 Activar 2FA (TOTP) por usuario

Hoy las reglas de Authelia están en `one_factor`, así que el TOTP es
**opcional**: el usuario puede registrar un dispositivo voluntariamente,
pero si no lo hace, igual entra con solo password.

Para registrar TOTP:

1. El usuario entra a `https://panel.dronefieldoperation.cloud/authelia/`.
2. Se loguea con su password.
3. Va a *Settings* → *Two-Factor Authentication* → *Register a new
   device*.
4. Authelia escribe el link de registro en
   `/config/data/notifications.txt` (porque no tenemos SMTP).
5. Para sacar ese link y dárselo al usuario:
   ```bash
   docker exec authelia cat /config/data/notifications.txt
   ```
6. El usuario abre ese link, escanea el QR con Google Authenticator /
   Authy / 1Password, y confirma.

### 8.7 Pasar de "2FA opcional" a "2FA obligatorio"

Cuando todos los usuarios tengan TOTP configurado:

1. Editar [authelia/configuration.yml](authelia/configuration.yml).
2. Cambiar todas las `policy: one_factor` por `policy: two_factor`.
3. Recargar:
   ```bash
   docker compose restart authelia
   ```
4. A partir de ese momento, cada login pide password + código TOTP.

### 8.8 Ver los logs / debuggear

```bash
# Todos los containers
docker compose logs --tail=100 -f

# Solo uno
docker compose logs --tail=100 -f authelia
docker compose logs --tail=100 -f auth_service
docker compose logs --tail=100 -f mediamtx

# nginx del host
sudo tail -f /var/log/nginx/error.log
sudo tail -f /var/log/nginx/access.log
```

### 8.9 Reiniciar todo el stack

```bash
cd ~/centro-monitoreo
docker compose down       # frena y elimina containers (mantiene volumes)
docker compose up -d      # vuelve a levantar
```

> **NO** uses `docker compose down -v` salvo que quieras BORRAR el
> volume de Authelia (sesiones + TOTP + base de regulación). Es
> destructivo.

### 8.10 Aplicar cambios traídos del repo

```bash
cd ~/centro-monitoreo
git pull

# Si cambió el código de auth-service:
docker compose build auth_service
docker compose up -d auth_service

# Si cambiaron solo configs (mediamtx.yml, companies.yml, authelia/configuration.yml):
docker compose restart mediamtx auth_service authelia

# Si cambió html/index.html: no hace falta nada — Ctrl+F5 en el browser.

# Si cambió nginxconfig.txt: hay que reflejar manualmente en
# /etc/nginx/sites-available/drones.conf, después:
sudo nginx -t && sudo systemctl reload nginx
```

---

## 9. Cómo verificar que todo funciona

Lista para correr después de un deploy o después de un cambio.

| Qué probar | Cómo | Esperado |
|---|---|---|
| Containers arriba | `docker compose ps` | 4 servicios en estado `Up` |
| Logs sin errores | `docker compose logs --tail=50` | Sin tracebacks ni "exited" |
| Authelia health | `curl http://127.0.0.1:9091/api/health` | `{"status":"OK"}` |
| Sin cookie redirige al portal | `curl -I https://panel.dronefieldoperation.cloud/` | 302 a `/authelia/?rd=...` |
| Cert válido | Candado verde en el navegador, sin warning | OK |
| Login carga | Ir al portal en el browser | Form visible |
| Login funciona | Logueate con un usuario válido | Vuelve al dashboard |
| `/cameras` filtra por rol | Como `operador_angular`, ver el grid | Solo cámara Q1 Angular |
| Como `superadmin` ves todo | Logueate como `bruno` | Todas las cámaras |
| `/webrtc/` con path no permitido da 401 | Como `operador_angular`, intentar ver Q1 Zoom | Celda en error |
| Publish RTMP llega | `docker logs mediamtx_drones \| grep RTMP` mientras hay un publisher | Línea `is publishing to path '...'` |
| Logout funciona | Click en "Salir" | Vuelve al portal |
| Brute force | 6 logins fallidos seguidos | Ban temporal: "too many fails" |
| Auditoría | `docker logs authelia 2>&1 \| grep authentication` | Línea por cada login |

---

## 10. Troubleshooting

Cosas concretas que pueden fallar y cómo resolverlas.

### El portal de Authelia no carga (502 Bad Gateway)

- Verificá que el container está arriba: `docker compose ps`.
- Probá interno: `curl http://127.0.0.1:9091/api/health`. Si falla, el
  container no levantó. `docker compose logs authelia`.
- En `authelia/configuration.yml`, el campo `server.address` tiene que
  estar en `tcp://0.0.0.0:9091/authelia` para que escuche dentro del
  container.
- Verificá que `nginxconfig.txt` location `/authelia/` está actualizada.

### "user not in companies.yml" al pegarle a `/cameras`

El usuario existe en Authelia pero no en `companies.yml`. Agregalo a
`companies.yml` con su rol y permisos, después
`docker compose restart auth_service`.

### El video no se ve, dice "Error 405"

Causa típica: `proxy_redirect ~^/(.*)$ /webrtc/$1;` falta en el
`location /webrtc/` del nginx del host. Sin esto, el flujo WHEP de
MediaMTX se rompe en el PATCH de trickle ICE.

### El video no se ve, dice "Sin señal"

- Verificar que el dron está publicando: `docker logs mediamtx_drones |
  grep -i publish`. Si no aparece nada, FlightHub no está enviando.
- Verificar el path: FlightHub puede estar publicando a un path con un
  typo, o un path no declarado en `mediamtx.yml`. MediaMTX en ese caso
  loguea `failed to authenticate`.
- Verificar que el path está en `companies.yml` para ese usuario.

### "authentication failed" en logs de MediaMTX

- Si la IP es pública (`81.x.x.x`): `webrtcTrustedProxies` está
  poblado, hacelo `[]` (ver Troubleshooting "Trusted proxies" más
  abajo).
- Si la IP es privada (`127.0.0.1`): la regla de read no incluye esa
  IP en `authInternalUsers`. Revisá `mediamtx.yml`.

### Brute force me banea legítimamente

```bash
# Ver intentos recientes
docker exec authelia ls -la /config/data/
docker exec authelia sqlite3 /config/data/db.sqlite3 \
    "SELECT * FROM authentication_logs ORDER BY time DESC LIMIT 10;"

# Limpiar el ban (no recomendado en operación)
docker exec authelia sqlite3 /config/data/db.sqlite3 \
    "DELETE FROM authentication_logs WHERE successful=0;"
```

### El timer del certbot no renueva

```bash
sudo systemctl status snap.certbot.renew.timer
sudo certbot renew --dry-run
# Si falla: ver /var/log/letsencrypt/letsencrypt.log
```

### Cambié una contraseña pero el usuario sigue logueado

Las cookies tienen 8h de vida. Para forzar logout inmediato:

```bash
docker exec authelia rm -f /config/data/db.sqlite3
docker compose restart authelia
# ATENCIÓN: borra TODAS las sesiones de TODOS los usuarios.
```

### Logs muy grandes llenan el disco

```bash
# Ver cuánto ocupan los logs de Docker
sudo du -sh /var/lib/docker/containers/*/

# Configurar rotación global en /etc/docker/daemon.json:
sudo nano /etc/docker/daemon.json
```

Contenido:

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "20m",
    "max-file": "5"
  }
}
```

Después:

```bash
sudo systemctl restart docker
docker compose up -d
```

### Trusted proxies (gotcha histórico de MediaMTX)

`webrtcTrustedProxies: []` en `mediamtx.yml` es **a propósito**. Si lo
poblamos con `127.0.0.1`, MediaMTX confía en `X-Forwarded-For` y toma la
IP pública del cliente como "origen", rompiendo las reglas de IP de
`authInternalUsers`. Síntoma: `authentication failed` en logs con la IP
pública.

### Audio del dron descartado (MPEG-4 AAC)

WebRTC no soporta AAC nativamente. MediaMTX skipea esa pista y
reproduce solo el video. Aceptable: el audio del dron no tiene utilidad
operativa.

### YAML 1.1 vs unmarshaler estricto

MediaMTX (Go) parsea YAML con un unmarshaler estricto. `yes`/`no` sin
comillas son booleans en YAML 1.1. Los strings tipo `rtmpEncryption`
necesitan comillas (`rtmpEncryption: "no"`). Si no se ponen, error tipo
`cannot unmarshal string into Go value of type bool`. Regla general:
booleans = `true`/`false`, strings = entre comillas.

---

## 11. Deuda técnica conocida

- **Auth de publish RTMP abierta:** cualquiera con el dominio + un path
  conocido puede publicar. Mitigación inmediata: whitelist por IP origen
  en `mediamtx.yml` con los rangos DJI. Para producción seria: mTLS o
  token rotativo.
- **Sin SMTP:** Authelia no puede enviar emails. Reset de contraseña =
  manual (regenerar hash + editar yml). Notifications de TOTP setup
  caen en `/config/data/notifications.txt`.
- **`latest` en MediaMTX:** la imagen no está pineada. Pinear a
  `bluenviron/mediamtx:1.18.1`.
- **Sin monitoreo de salud:** no detectamos automáticamente cuando un
  stream se cae. Conviene un healthcheck que pingue WHEP cada N
  segundos y avise por algún canal.
- **Sin grabación:** los `paths:` tienen `record: false`. Si en algún
  momento se necesita guardar vuelos, cambiar a `true` y dimensionar
  disco.
- **Sin tests automatizados:** el deploy es manual. Conviene un pipeline
  mínimo que valide `nginx -t`, `docker compose config`, y un curl al
  WHEP de un path conocido.
- **`companies.yml` y `users_database.yml` separados:** dos archivos
  para tocar al agregar un usuario. Conviene un script que sincronice
  ambos.
- **Convivencia con otros sitios del server:** cambios a `nginx.conf`
  global o restarts de Docker pueden afectar a `qntdrones.com`, `app`,
  `n8n`, etc. Revisar `sudo nginx -t` antes de aplicar cambios.

---

## 12. Mapa de puertos

| Puerto | Proto | Acceso | Quién lo usa |
|---|---|---|---|
| 22 | TCP | público* | SSH (en producción, restringir por IP) |
| 80 | TCP | público | Redirect a HTTPS + ACME challenges de certbot |
| 443 | TCP | público | HTTPS del dashboard, portal de Authelia, todo |
| 1935 | TCP | público | RTMP ingesta desde FlightHub 2 |
| 8189 | UDP | público | WebRTC ICE (paquetes de video) |
| 9091 | TCP | loopback | Authelia (login, sesión, validación) |
| 18082 | TCP | loopback | Container `dashboard_web` (HTML estático) |
| 19100 | TCP | loopback | Container `auth_service` (Python FastAPI) |
| 8889 | TCP | loopback | Container `mediamtx` (señalización WHEP) |

\* SSH debería estar restringido por IP en `ufw` para producción.

---

## 13. Backups y mantenimiento

### Qué hay que respaldar

| Archivo / Volume | Por qué | Frecuencia sugerida |
|---|---|---|
| `.env` | Secrets de Authelia. Si los perdés, las sesiones se invalidan y la base SQLite queda ilegible. | Semanal |
| `authelia/users_database.yml` | Usuarios + hashes. Si lo perdés, todos pierden acceso. | Cuando cambia |
| `companies.yml` | Está en git, pero hacé un backup adicional. | Cuando cambia |
| Volume `authelia_data` | Sesiones + TOTP + logs. Perderlo = todos tienen que registrar TOTP de vuelta. | Diaria |
| `/etc/letsencrypt/` | Certs SSL. Se pueden regenerar, pero da pelusa. | Mensual |
| `/etc/nginx/sites-available/drones.conf` | La config de nginx. | Cuando cambia |

### Cómo respaldar el volume de Authelia

```bash
# Pause Authelia para consistencia
docker compose stop authelia

# Copiar el volume a un tarball
docker run --rm \
    -v centro-monitoreo_authelia_data:/data \
    -v $(pwd):/backup \
    alpine tar czf /backup/authelia-data-$(date +%Y%m%d).tar.gz -C /data .

# Reanudar
docker compose start authelia

# El backup queda en ./authelia-data-YYYYMMDD.tar.gz — guardalo
# en otro lugar (S3, otra VM, etc.).
```

### Cómo restaurar

```bash
docker compose stop authelia
docker run --rm \
    -v centro-monitoreo_authelia_data:/data \
    -v $(pwd):/backup \
    alpine sh -c "rm -rf /data/* && tar xzf /backup/authelia-data-YYYYMMDD.tar.gz -C /data"
docker compose start authelia
```

### Mantenimiento periódico

- **Mensual:** `sudo apt update && sudo apt upgrade` en la VM. Reiniciar
  si se actualiza el kernel.
- **Mensual:** actualizar imágenes de Docker:
  ```bash
  docker compose pull
  docker compose up -d
  ```
  Probar después que todo siga andando — sobre todo MediaMTX, que cambia
  campos entre versiones.
- **Anual:** rotar los secrets del `.env`. Implica que todas las
  sesiones se invalidan; informar a los usuarios.
- **Anual:** revisar los logs de Authelia por accesos sospechosos o
  intentos de brute force exitosos:
  ```bash
  docker exec authelia sqlite3 /config/data/db.sqlite3 \
      "SELECT username, time, successful, remote_ip FROM authentication_logs ORDER BY time DESC LIMIT 100;"
  ```

---

## URLs finales

- Dashboard: `https://panel.dronefieldoperation.cloud/`
- Portal Authelia: `https://panel.dronefieldoperation.cloud/authelia/`
- Logout: `https://panel.dronefieldoperation.cloud/authelia/logout`
- Publicación RTMP: `rtmp://panel.dronefieldoperation.cloud:1935/<path>`

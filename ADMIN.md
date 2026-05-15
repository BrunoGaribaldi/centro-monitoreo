# Guía operativa de administración

**Centro de Monitoreo de Drones · NQN Petrol**

Cómo administrar el sistema en el día a día: usuarios, roles, empresas,
cámaras, 2FA. Está pensada como referencia rápida para no tener que
recordar de memoria qué archivo tocar y en qué orden.

Si todavía no tenés contexto del stack (qué hace MediaMTX, Authelia,
auth-service, nginx), leé primero [Readme.md](Readme.md). Este archivo
asume que el sistema ya está desplegado y funcionando.

---

## Tabla de contenidos

1. [El modelo conceptual](#1-el-modelo-conceptual)
2. [Los 4 roles](#2-los-4-roles)
3. [Anatomía de Authelia](#3-anatomía-de-authelia-configurationyml)
4. [Operaciones paso a paso](#4-operaciones-paso-a-paso)
   - [Agregar usuario](#operación-1--agregar-un-usuario-nuevo)
   - [Cambiar contraseña](#operación-2--cambiar-la-contraseña-de-un-usuario)
   - [Bloquear / desbloquear usuario](#operación-3--bloquear--desbloquear-un-usuario)
   - [Borrar usuario](#operación-4--borrar-definitivamente-un-usuario)
   - [Agregar cámara](#operación-5--agregar-una-cámara-nueva)
   - [Agregar empresa](#operación-6--agregar-una-empresa-nueva)
   - [Activar 2FA por usuario](#operación-7--activar-2fa-totp-para-un-usuario)
   - [Forzar 2FA global](#operación-8--forzar-2fa-para-todos)
   - [Agregar rol nuevo](#operación-9--agregar-un-rol-nuevo)
5. [Tabla resumen "qué cambia dónde"](#5-tabla-resumen-qué-archivo-toco-para-cada-acción)
6. [Reglas de oro](#6-reglas-de-oro)
7. [Verificación rápida después de cada cambio](#7-verificación-rápida-después-de-cada-cambio)
8. [Logs, auditoría e historial de inicios de sesión](#8-logs-auditoría-e-historial-de-inicios-de-sesión)

---

## 1. El modelo conceptual

El sistema separa **identidad** (¿quién sos?) de **autorización**
(¿qué podés ver?) en dos archivos distintos:

| Archivo | Responsabilidad | Quién lo lee |
|---|---|---|
| `authelia/users_database.yml` | **Identidad**: username, password (hash argon2), email, displayname, grupos | Authelia |
| `companies.yml` | **Autorización**: empresa, rol, sitios visibles, cámaras visibles, jerarquía de negocio | auth-service |

El nexo entre los dos archivos es el **`username`** — tiene que ser
**idéntico** en ambos lados. Si en `users_database.yml` lo escribís
`Juan` y en `companies.yml` `juan`, el sistema no los relaciona.
Es case-sensitive.

### Flujo end-to-end

```
Browser → POST /authelia/api/firstfactor con {username, password}
       ↓
Authelia: ¿el password matchea el hash argon2 de users_database.yml?
       ↓ sí
Authelia: setea cookie de sesión, recuerda el username
       ↓
Browser carga el dashboard → GET /center-auth/cameras
       ↓
nginx: subrequest a Authelia "¿la cookie es válida?" → sí, Remote-User: juan
       ↓
nginx: agrega header "Remote-User: juan" al request → lo pasa al auth-service
       ↓
auth-service: busca "juan" en companies.yml → encuentra su rol y permisos
       ↓
auth-service: devuelve la lista de cámaras filtrada según su rol
       ↓
Dashboard: pinta el grid con esas cámaras
```

Cuando el browser pide ver una cámara (`POST /webrtc/dron-cam-q1-angular/whep`),
pasa **dos veces** por validación:
1. Authelia: ¿está logueado?
2. auth-service: ¿el rol del usuario le permite ver *ese path*?

---

## 2. Los 4 roles

Los roles están **hardcodeados** en el código del auth-service
([auth-service/main.py](auth-service/main.py), función `resolve_cameras()`).
No son configurables sin tocar Python. Son:

### `superadmin`
- **Qué ve**: TODAS las cámaras de TODAS las empresas.
- **Para qué**: el dueño del centro de monitoreo, técnicos que dan
  soporte cross-empresa.
- **Campos requeridos en `companies.yml`**: solo `username` y `rol`.
  NO lleva `empresa`.
- **Ejemplo**:
  ```yaml
  - username: ingenieria
    rol: superadmin
  ```

### `admin_empresa`
- **Qué ve**: todos los sitios y todas las cámaras **de su empresa**.
- **Para qué**: el administrador del cliente (alguien de Quintana que
  tiene que ver todo lo de Quintana, pero no de otros clientes).
- **Campos requeridos**: `username`, `rol`, `empresa`.
- **Ejemplo**:
  ```yaml
  - username: admin_quintana
    empresa: QUINTANA
    rol: admin_empresa
  ```

### `admin_site`
- **Qué ve**: solo los **sitios** listados en el campo `sites`.
  Dentro de cada sitio, ve TODAS las cámaras.
- **Para qué**: un supervisor responsable de un yacimiento específico
  ("Cañadón Amarillo").
- **Campos requeridos**: `username`, `rol`, `empresa`, `sites` (lista).
- **Ejemplo**:
  ```yaml
  - username: supervisor_ca
    empresa: QUINTANA
    rol: admin_site
    sites:
      - canadon-amarillo
  ```

### `viewer_drone`
- **Qué ve**: solo las cámaras cuyo `path` está en `allowed_paths`.
  Granularidad por cámara individual.
- **Para qué**: un operador que solo monitorea una sola cámara (ej.
  solo la térmica del Q1).
- **Campos requeridos**: `username`, `rol`, `empresa`, `allowed_paths` (lista).
- **Ejemplo**:
  ```yaml
  - username: operador_angular
    empresa: QUINTANA
    rol: viewer_drone
    allowed_paths:
      - dron-cam-q1-angular
  ```

---

## 3. Anatomía de Authelia (`configuration.yml`)

[authelia/configuration.yml](authelia/configuration.yml) tiene 9 secciones.
Esto es qué hace cada una y cuándo tocarías o no.

### `server`
```yaml
server:
  address: 'tcp://0.0.0.0:9091/authelia'
```
Le dice a Authelia que escuche en el puerto 9091 con prefijo `/authelia`.
**No tocar** — el nginx del host está configurado para proxearle ahí.

### `authentication_backend`
```yaml
authentication_backend:
  password_reset:
    disable: true
  refresh_interval: '5m'
  file:
    path: /config/users_database.yml
    watch: true
    password:
      algorithm: argon2
```
- `password_reset.disable: true` — no podés resetear password vía email
  (no hay SMTP configurado). Reset = manual (ver Operación 2).
- `refresh_interval: '5m'` — Authelia revalida grupos y `disabled: true`
  de un usuario cada 5 min. Si marcás a alguien `disabled`, sus logins
  nuevos son rechazados al instante; las sesiones ya activas mueren en
  máximo 5 min.
- `watch: true` — Authelia recarga `users_database.yml` solo cuando el
  archivo cambia. **No necesitás `docker restart` después de agregar
  usuarios.**
- `algorithm: argon2` — algoritmo de hashing. El más fuerte disponible
  (ganador del Password Hashing Competition 2015).

### `access_control`
Acá viven las reglas de **autorización gruesa** (qué grupos pueden
acceder a qué URLs). Dos reglas hoy:

**Regla 1 — Assets + API del dashboard:**
```yaml
- resources: ['^/$', '^/index\.html$', '^/favicon\.png$', '^/images\.png$', '^/center-auth/.*']
  policy: one_factor
```
Cualquier usuario autenticado (sin importar grupo) accede al dashboard
y al endpoint `/center-auth/cameras`. La filtración fina la hace el
auth-service.

**Regla 2 — Streams WebRTC:**
```yaml
- resources: ['^/webrtc/.*']
  subject:
    - 'group:superadmins'
    - 'group:quintana-admins'
    - 'group:quintana-ca'
    - 'group:quintana-operadores'
  policy: one_factor
```
Solo usuarios con uno de esos grupos pueden hacer requests a
`/webrtc/*`. Si más adelante sumás la empresa ACME, agregás
`group:acme-admins`, etc., a esa lista.

`policy: one_factor` = solo password. Si querés exigir 2FA, cambiás
a `policy: two_factor`.

### `session`
```yaml
session:
  expiration: '8h'
  inactivity: '1h'
  remember_me: '8h'
```
- `expiration: 8h` — la cookie expira 8 horas después del login, sin
  importar si el user estuvo activo.
- `inactivity: 1h` — si el user no toca el dashboard por 1h, la sesión
  muere antes.
- `remember_me: 8h` — igualado a `expiration` para que la opción
  "remember me" del portal nativo no genere sesiones de meses.

### `regulation`
```yaml
regulation:
  max_retries: 5
  find_time: '2m'
  ban_time: '5m'
```
Brute force protection: 5 intentos fallidos en 2 minutos = ban de 5
minutos para esa IP/usuario.

### `storage`
SQLite en un volume Docker. Persiste sesiones, dispositivos TOTP,
intentos de login. **No tocar.**

### `notifier`
```yaml
notifier:
  filesystem:
    filename: /config/data/notifications.txt
```
Como no hay SMTP, los emails que Authelia "mandaría" (ej. link para
registrar TOTP) caen en un archivo. Para leerlos:
```bash
docker exec authelia cat /config/data/notifications.txt
```

### `totp` e `identity_validation`
Para activación voluntaria de 2FA y reset de password vía JWT. Solo se
usa si el usuario va al portal de Authelia (`/authelia/`) a configurarse 2FA.

---

## 4. Operaciones paso a paso

### Operación 1 · Agregar un usuario nuevo

Caso: agregar a "Juan Pérez", supervisor del sitio Cañadón Seco, con
contraseña inicial `Juan2026!`.

**Paso 1** — Generar el hash argon2 de la contraseña:
```bash
docker run --rm authelia/authelia:4.38 \
    authelia crypto hash generate argon2 --password 'Juan2026!'
```
Te devuelve algo como:
```
Digest: $argon2id$v=19$m=65536,t=3,p=4$abc123...xyz
```
Copiás el string completo que arranca con `$argon2id`.

**Paso 2** — Agregar la identidad en `authelia/users_database.yml`:
```yaml
users:
  # ... usuarios existentes ...

  supervisor_cs:
    disabled: false
    displayname: "Juan Pérez"
    password: "$argon2id$v=19$m=65536,t=3,p=4$abc123...xyz"
    email: juan.perez@quintana.example
    groups:
      - quintana-cs
```

Authelia recarga solo (porque `watch: true`). Verificá:
```bash
docker logs authelia 2>&1 | grep -i "users database" | tail -3
```

**Paso 3** — Agregar la autorización en `companies.yml`:
```yaml
users:
  # ... usuarios existentes ...

  - username: supervisor_cs
    empresa: QUINTANA
    rol: admin_site
    sites:
      - canadon-seco
```

**Paso 4** — Si el grupo `quintana-cs` es **nuevo**, sumarlo al
`subject:` de la regla `/webrtc/*` en `authelia/configuration.yml`:
```yaml
- domain: 'panel.dronefieldoperation.cloud'
  resources: ['^/webrtc/.*']
  subject:
    - 'group:superadmins'
    - 'group:quintana-admins'
    - 'group:quintana-ca'
    - 'group:quintana-cs'          # ← nuevo
    - 'group:quintana-operadores'
  policy: one_factor
```

**Paso 5** — Aplicar cambios:
```bash
cd ~/centro-monitoreo
docker compose restart authelia auth_service
```

- `authelia` se restartea solo si cambiaste `configuration.yml`
  (paso 4). Si solo agregaste usuarios en `users_database.yml`, no
  hace falta — recarga sola.
- `auth_service` se restartea para invalidar el cache de
  `companies.yml` (sino tardaría hasta 30s en reflejar el cambio).

**Verificar** — abrí incógnito, logueá a `supervisor_cs` con
`Juan2026!`, debería ver solo las cámaras de Cañadón Seco.

---

### Operación 2 · Cambiar la contraseña de un usuario

**Paso 1** — Generar el nuevo hash:
```bash
docker run --rm authelia/authelia:4.38 \
    authelia crypto hash generate argon2 --password 'nuevaclave'
```

**Paso 2** — En `authelia/users_database.yml`, reemplazar el campo
`password:` del usuario por el hash nuevo.

**Paso 3** — Authelia recarga sola. Las sesiones existentes del
usuario siguen vivas hasta que expiren (8h máximo). Si necesitás
cortarlas YA, hay un truco:

```yaml
# Editar users_database.yml, cambiar el flag del usuario:
operador_angular:
  disabled: true     # ← guardar archivo, esperar 5-10 segundos
  # luego:
  disabled: false    # ← volver a false, guardar
```

Al bouncear `disabled` se invalidan las sesiones existentes y a la
vez se reactiva el usuario para que pueda loguear con la clave nueva.

---

### Operación 3 · Bloquear / desbloquear un usuario

**Bloquear sin borrar** — en `authelia/users_database.yml`:
```yaml
operador_angular:
  disabled: true     # ← antes era false
  # ... resto igual ...
```
Authelia recarga sola. Próximos logins rechazados. Sesiones activas
mueren en máximo 5 min (`refresh_interval`).

**Desbloquear** — `disabled: false` de vuelta.

---

### Operación 4 · Borrar definitivamente un usuario

**Paso 1** — Eliminar la entrada en `authelia/users_database.yml`.

**Paso 2** — Eliminar la entrada del array `users:` en
`companies.yml`.

**Paso 3** — Si el usuario era el único en su grupo, podés eliminar
el grupo del `subject:` de `authelia/configuration.yml` (cosmético,
no es urgente).

**Paso 4**:
```bash
docker compose restart authelia auth_service
```

---

### Operación 5 · Agregar una cámara nueva

Caso: instalar la cámara térmica `dron-cam-q1-termica` en Cañadón
Amarillo.

**Paso 1** — Declarar el path en `mediamtx.yml`, sección `paths:`:
```yaml
paths:
  # ... paths existentes ...
  dron-cam-q1-termica:
    source: publisher
    record: false
```

**Paso 2** — Agregar la cámara al sitio correspondiente en
`companies.yml`:
```yaml
companies:
  QUINTANA:
    sites:
      canadon-amarillo:
        cameras:
          # ... cámaras existentes ...
          - path: dron-cam-q1-termica
            titulo: "Q1 · Térmica"
```

**Paso 3** — Si querés que un `viewer_drone` específico la pueda
ver, agregar el path a su `allowed_paths`.

**Paso 4** — Configurar FlightHub 2 para que publique la cámara
nueva a:
```
rtmp://panel.dronefieldoperation.cloud:1935/dron-cam-q1-termica
```

**Paso 5** — Aplicar:
```bash
docker compose restart mediamtx auth_service
```

`Ctrl+F5` en el navegador. La cámara aparece automáticamente para los
usuarios con permiso.

---

### Operación 6 · Agregar una empresa nueva

Caso: la empresa "ACME Industrial" se incorpora con un sitio "Site
Norte" y dos cámaras.

**Paso 1** — Agregar la empresa y sus sitios en `companies.yml`:
```yaml
companies:
  QUINTANA:
    # ... lo existente ...

  ACME:
    display: "ACME Industrial"
    sites:
      site-norte:
        display: "Site Norte"
        cameras:
          - path: acme-norte-cam1
            titulo: "Cámara A"
          - path: acme-norte-cam2
            titulo: "Cámara B"
```

**Paso 2** — Declarar los paths nuevos en `mediamtx.yml`:
```yaml
paths:
  # ... paths existentes ...
  acme-norte-cam1:
    source: publisher
    record: false
  acme-norte-cam2:
    source: publisher
    record: false
```

**Paso 3** — Crear los usuarios de ACME en `companies.yml`:
```yaml
users:
  # ... existentes ...

  - username: admin_acme
    empresa: ACME
    rol: admin_empresa
```

**Paso 4** — Crear los mismos usuarios en `authelia/users_database.yml`
con su grupo `acme-admins` (generá el hash con el comando de la
Operación 1):
```yaml
admin_acme:
  disabled: false
  displayname: "Admin ACME"
  password: "$argon2id$..."
  email: admin@acme.example
  groups:
    - acme-admins
```

**Paso 5** — Sumar el grupo nuevo al `subject:` de la regla
`/webrtc/*` en `authelia/configuration.yml`:
```yaml
- resources: ['^/webrtc/.*']
  subject:
    - 'group:superadmins'
    - 'group:quintana-admins'
    - 'group:quintana-ca'
    - 'group:quintana-operadores'
    - 'group:acme-admins'     # ← nuevo
  policy: one_factor
```

**Paso 6** — Configurar FlightHub 2 de ACME para publicar a los
nuevos paths RTMP.

**Paso 7** — Aplicar:
```bash
docker compose restart mediamtx authelia auth_service
```

---

### Operación 7 · Activar 2FA (TOTP) para un usuario

Authelia tiene 2FA disponible pero **no obligatorio** hoy. Para que
un usuario lo active voluntariamente:

**Paso 1** — El usuario se loguea normalmente desde el dashboard.

**Paso 2** — Va a `https://panel.dronefieldoperation.cloud/authelia/`
(el portal nativo de Authelia — el link "Ir al portal" del footer
del overlay de login).

**Paso 3** — Settings → Two-Factor Authentication → Register a new
device.

**Paso 4** — Authelia escribe un link de registro en
`/config/data/notifications.txt` (no hay SMTP). Lo sacás con:
```bash
docker exec authelia cat /config/data/notifications.txt
```

**Paso 5** — Le pasás el link al usuario. Lo abre, le aparece un QR.
Lo escanea con Google Authenticator / Authy / 1Password / Bitwarden.
Confirma con el código de 6 dígitos.

**Paso 6** — Mientras la regla esté en `one_factor`, el TOTP queda
*opcional*: el user puede usarlo o no. Si pasás a `two_factor`, le
pide TOTP en cada login.

---

### Operación 8 · Forzar 2FA para todos

Una vez que todos los usuarios tengan TOTP configurado:

**Paso 1** — Editar `authelia/configuration.yml`. Cambiar las dos
reglas de `access_control`:
```yaml
- resources: [...]
  policy: two_factor    # ← era one_factor
```

**Paso 2** — Aplicar:
```bash
docker compose restart authelia
```

A partir de ahora, todo login pide password + TOTP.

> **Cuidado**: si algún usuario no tenía TOTP configurado, no podrá
> entrar (la opción de configurarlo aparece solo después de loguear
> con primer factor). Si pasa, lo desbloqueás temporalmente bajando
> solo ese resource a `one_factor`, dejás que configure, y volvés a
> subir.

---

### Operación 9 · Agregar un rol nuevo

Los 4 roles están **hardcodeados** en la función `resolve_cameras()`
del auth-service ([auth-service/main.py](auth-service/main.py)). Si
necesitás un rol nuevo (ej. `viewer_grupo` que vea un grupo de cámaras
arbitrario), hay que:

**Paso 1** — Editar `auth-service/main.py`: agregar un nuevo branch
al `if rol == ...` dentro de `resolve_cameras()` que filtre según los
campos que definas.

**Paso 2** — Documentar el rol nuevo en los comentarios de
`companies.yml`.

**Paso 3** — Rebuild + restart:
```bash
docker compose build auth_service
docker compose up -d auth_service
```

> Los 4 roles actuales cubren bien las jerarquías "empresa → sitio →
> cámara". Si tu caso de uso encaja ahí, no inventes un rol nuevo —
> usá los que hay.

---

## 5. Tabla resumen "qué archivo toco para cada acción"

| Acción | `users_database.yml` | `companies.yml` | `configuration.yml` (Authelia) | `mediamtx.yml` | FlightHub 2 |
|---|---|---|---|---|---|
| Agregar usuario (grupo existente) | Sí | Sí | No | No | No |
| Agregar usuario (grupo nuevo) | Sí | Sí | **Sí** (subject) | No | No |
| Cambiar contraseña | Sí | No | No | No | No |
| Bloquear usuario | Sí (`disabled: true`) | No | No | No | No |
| Borrar usuario | Sí | Sí | No | No | No |
| Agregar cámara | No | Sí | No | **Sí** (paths) | Sí (canal RTMP) |
| Agregar sitio (en empresa existente) | No | Sí | No | No | No |
| Agregar empresa entera | Sí (admins) | Sí | **Sí** (subject) | Sí (paths) | Sí |
| Activar 2FA (opt-in) | No | No | No | No | No |
| Forzar 2FA global | No | No | Sí (`two_factor`) | No | No |
| Agregar rol nuevo | No | No | No | No | **Código Python** |

---

## 6. Reglas de oro

1. **El `username` es la llave universal**. Si lo escribís distinto
   en los dos YAMLs, el sistema no los relaciona. Case-sensitive.
2. **Los hashes de passwords NUNCA se commitean**.
   `authelia/users_database.yml` está en `.gitignore`. Solo se commitea
   el `.example`.
3. **Después de tocar `companies.yml`**: `docker compose restart
   auth_service` para invalidar el cache de 30s (o esperá 30s).
4. **Después de tocar `users_database.yml`**: nada — Authelia recarga
   sola (`watch: true`).
5. **Después de tocar `configuration.yml` (Authelia)**:
   `docker compose restart authelia`.
6. **Después de tocar `mediamtx.yml`**: `docker compose restart
   mediamtx`.
7. **Después de tocar `html/index.html`**: nada — el volume está
   montado en vivo, `Ctrl+F5` en el browser.
8. **Si dudás**: `docker compose restart` reinicia todo el stack. No
   es destructivo (los volumes persisten).

---

## 7. Verificación rápida después de cada cambio

| Acción | Cómo verificás que andá |
|---|---|
| Agregaste un usuario | `docker logs authelia 2>&1 \| grep "users database file reloaded" \| tail -1` — debe haber un reload reciente. Después: login en incógnito con el usuario nuevo. |
| Cambiaste contraseña | Login en incógnito con la clave nueva. |
| Bloqueaste un user | Intentar login en incógnito → "user not enabled" o credenciales rechazadas. |
| Agregaste cámara | `Ctrl+F5` en el dashboard → debe aparecer la cámara nueva en el grid (en estado "Sin señal" hasta que FlightHub publique). |
| Agregaste empresa | Logueate como el `admin_<empresa>` nuevo → debe ver solo lo suyo, nada de QUINTANA. |
| Forzaste 2FA | Login → después del password te pide código TOTP. |
| Restart de containers | `docker compose ps` → todos en `Up (healthy)`. |
| Pista de auditoría | `docker logs authelia 2>&1 \| grep "authentication" \| tail -10` — debe mostrar los logins recientes con éxito/fallo + username + IP. |

---

## 8. Logs, auditoría e historial de inicios de sesión

Esta sección cubre tres cosas distintas que conviene no mezclar:

1. **Logs de cada container** — qué pasó "ahora" o "hace minutos".
   Son volátiles (los rota Docker), útiles para debuggear.
2. **Historial de inicios de sesión** — registro **persistente** de
   cada login (éxito o fallo) que Authelia guarda en su base SQLite.
   Es la fuente de verdad para auditoría.
3. **Logs de nginx del host** — `/var/log/nginx/*.log`. Acá caen los
   401/403 que rechaza el `auth_request` y los errores de proxy.

### 8.1 Qué loguea cada container

| Container | Qué loguea | Cuándo te sirve |
|---|---|---|
| `authelia` | Login exitoso/fallido (con username + IP), bans de brute force, recarga de `users_database.yml`, errores de config | Investigar "no me deja entrar", validar que un cambio en YAML se aplicó |
| `auth_service` | Cada decisión de `/cameras` y `/webrtc-authz`: usuario, path, resultado | Responder "no me deja ver tal cámara" |
| `mediamtx_drones` | Publicaciones RTMP entrantes (con IP origen del dron), sesiones WebRTC, drops de stream | "El dron no publica", "el operador dice que no ve nada" |
| `dashboard_drones` | Access log de archivos servidos (nginx alpine) | Casi nunca útil — es solo HTML estático |

### 8.2 Cómo leer los logs

**En vivo (tail -f):**
```bash
# Un container específico
docker compose logs -f authelia
docker compose logs -f auth_service
docker compose logs -f mediamtx

# Todos a la vez
docker compose logs --tail=100 -f
```

**Histórico (últimas N líneas):**
```bash
docker compose logs --tail=200 authelia
docker logs --since 1h auth_service       # última hora
docker logs --since 2026-05-15T10:00:00 mediamtx_drones
```

**Filtrar por palabra clave:**
```bash
# Logins fallidos recientes
docker logs authelia 2>&1 | grep -i "authentication attempt" | tail -20

# Recargas de users_database.yml (confirma que un cambio se aplicó)
docker logs authelia 2>&1 | grep -i "users database file reloaded"

# Decisiones del auth-service sobre un usuario específico
docker logs auth_service 2>&1 | grep "operador_angular"

# Publicaciones RTMP entrantes
docker logs mediamtx_drones 2>&1 | grep -i "is publishing"
```

### 8.3 Historial de inicios de sesión (persistente)

Authelia guarda **cada intento de login** en una base SQLite, sea
exitoso o fallido. Sobrevive a restarts del container porque está en
el volume `authelia_data` (montado en `/config/data/`).

Esta es la fuente de verdad para:
- Auditoría ("¿quién entró el martes a las 14hs?").
- Investigación de incidentes ("¿hubo intentos sospechosos esta
  semana?").
- Compliance (registro de accesos).

**Estructura de la tabla** `authentication_logs`:

| Columna | Tipo | Qué guarda |
|---|---|---|
| `id` | int | ID auto-incremental |
| `time` | datetime | Cuándo (timestamp) |
| `successful` | bool | 1 = login OK, 0 = login fallido |
| `banned` | bool | 1 = el intento gatilló un ban de brute force |
| `username` | string | Usuario que intentó loguear |
| `auth_type` | string | "1FA" (solo password) o "2FA" (con TOTP) |
| `remote_ip` | string | IP del cliente |

**Queries útiles** (copy-paste):

```bash
# Últimos 20 intentos (cualquier resultado)
docker exec authelia sqlite3 /config/data/db.sqlite3 \
    "SELECT datetime(time, 'unixepoch', 'localtime') AS hora, username, successful, banned, remote_ip
     FROM authentication_logs
     ORDER BY time DESC LIMIT 20;"

# Solo logins exitosos del último día
docker exec authelia sqlite3 /config/data/db.sqlite3 \
    "SELECT datetime(time, 'unixepoch', 'localtime') AS hora, username, remote_ip
     FROM authentication_logs
     WHERE successful = 1 AND time > strftime('%s', 'now', '-1 day')
     ORDER BY time DESC;"

# Solo intentos FALLIDOS del último día (los que importan para seguridad)
docker exec authelia sqlite3 /config/data/db.sqlite3 \
    "SELECT datetime(time, 'unixepoch', 'localtime') AS hora, username, remote_ip
     FROM authentication_logs
     WHERE successful = 0 AND time > strftime('%s', 'now', '-1 day')
     ORDER BY time DESC;"

# Conteo de logins por usuario en los últimos 30 días
docker exec authelia sqlite3 /config/data/db.sqlite3 \
    "SELECT username, COUNT(*) AS intentos,
            SUM(CASE WHEN successful=1 THEN 1 ELSE 0 END) AS exitos,
            SUM(CASE WHEN successful=0 THEN 1 ELSE 0 END) AS fallos
     FROM authentication_logs
     WHERE time > strftime('%s', 'now', '-30 days')
     GROUP BY username
     ORDER BY intentos DESC;"

# IPs con más intentos fallidos (posibles atacantes)
docker exec authelia sqlite3 /config/data/db.sqlite3 \
    "SELECT remote_ip, COUNT(*) AS fallos
     FROM authentication_logs
     WHERE successful = 0 AND time > strftime('%s', 'now', '-7 days')
     GROUP BY remote_ip
     ORDER BY fallos DESC
     LIMIT 10;"

# Bans de brute force activos / históricos
docker exec authelia sqlite3 /config/data/db.sqlite3 \
    "SELECT datetime(time, 'unixepoch', 'localtime') AS hora, username, remote_ip
     FROM authentication_logs
     WHERE banned = 1
     ORDER BY time DESC LIMIT 20;"
```

#### Exportar el historial a CSV (para Excel / reportes)

```bash
docker exec authelia sqlite3 -header -csv /config/data/db.sqlite3 \
    "SELECT datetime(time, 'unixepoch', 'localtime') AS hora,
            username, successful, banned, auth_type, remote_ip
     FROM authentication_logs
     ORDER BY time DESC;" > historial-logins-$(date +%Y%m%d).csv
```

#### Retención: cómo evitar que la tabla crezca infinitamente

Authelia **no limpia automáticamente** la tabla de logs. A largo plazo
puede crecer mucho (no rompe nada, pero conviene tener una política).

Para borrar logs más viejos que 6 meses (ejecutar manualmente cada
tanto, o automatizar con cron):

```bash
docker exec authelia sqlite3 /config/data/db.sqlite3 \
    "DELETE FROM authentication_logs WHERE time < strftime('%s', 'now', '-6 months');"

# Compactar la base después de borrar (recupera el espacio en disco)
docker exec authelia sqlite3 /config/data/db.sqlite3 "VACUUM;"
```

**Si la auditoría es importante para vos**, antes de purgar los logs
exportá a CSV (comando del bloque anterior) y archivá el archivo en
algún lado (S3, otra VM, drive). Una vez exportado, podés borrar de
la base sin perder la información histórica.

### 8.4 Logs de nginx del host

nginx loguea separadamente del Docker stack porque está instalado en
el host (no es un container).

```bash
# Errores (502, 504, problemas de upstream)
sudo tail -f /var/log/nginx/error.log

# Accesos (cada request HTTP que entra)
sudo tail -f /var/log/nginx/access.log

# 401/403 recientes (rechazos de auth_request)
sudo grep -E " (401|403) " /var/log/nginx/access.log | tail -20

# Requests a /webrtc/* (publicaciones de cámaras intentadas)
sudo grep "/webrtc/" /var/log/nginx/access.log | tail -20
```

nginx rota estos archivos automáticamente (paquete `logrotate` en
Ubuntu rota cada semana, mantiene 14 archivos). No requieren
mantenimiento.

### 8.5 Diagnóstico rápido por problema

**"El usuario X no puede entrar"**
```bash
# 1. ¿Authelia lo está rechazando?
docker logs authelia 2>&1 | grep -i "$USERNAME" | tail -20

# 2. ¿Está disabled? ¿O fue baneado por brute force?
docker exec authelia sqlite3 /config/data/db.sqlite3 \
    "SELECT datetime(time, 'unixepoch', 'localtime'), successful, banned, remote_ip
     FROM authentication_logs WHERE username = '$USERNAME' ORDER BY time DESC LIMIT 10;"

# 3. ¿Existe en companies.yml?
docker exec auth_service grep -A2 "username: $USERNAME" /app/companies.yml
```

**"El usuario X no ve una cámara que debería ver"**
```bash
# Logs del auth-service: cada decisión 200/403 sobre paths
docker logs auth_service 2>&1 | grep "$USERNAME" | tail -20

# Verificar el rol en companies.yml
docker exec auth_service grep -B1 -A5 "username: $USERNAME" /app/companies.yml
```

**"Un dron no publica"**
```bash
# ¿Llegó el RTMP a MediaMTX?
docker logs mediamtx_drones 2>&1 | grep -iE "publish|RTMP" | tail -30

# Si dice "authentication failed" → el path no está declarado o
# webrtcTrustedProxies está mal (ver Troubleshooting del Readme).
```

**"El portal de Authelia no carga"**
```bash
docker compose ps authelia          # ¿está Up?
curl http://127.0.0.1:9091/api/health   # ¿responde?
docker logs authelia --tail 50
sudo tail -20 /var/log/nginx/error.log
```

### 8.6 Rotación de logs de Docker

Por default los logs de los containers se acumulan en
`/var/lib/docker/containers/*/`. **Pueden crecer indefinidamente** y
llenar el disco si nadie configura rotación.

Para configurar rotación global (recomendado en producción):

```bash
sudo nano /etc/docker/daemon.json
```

Contenido (si el archivo no existe, crealo con este contenido; si
existe y tiene otras keys, agregar las dos opts dentro del JSON
existente):

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "20m",
    "max-file": "5"
  }
}
```

Significado: cada container guarda hasta **5 archivos** de **20 MB**
cada uno (= 100 MB máximo por container). Cuando se llena el más
viejo, se descarta.

Aplicar:
```bash
sudo systemctl restart docker
docker compose up -d   # los containers reciben la nueva config
```

> **Importante:** esto rota los logs en disco, pero **no toca la base
> SQLite de Authelia** — el historial de inicios de sesión sigue
> intacto porque está en el volume `authelia_data`, no en los logs de
> Docker.

### 8.7 Backup del historial de inicios de sesión

El SQLite de Authelia es un archivo plano. Backup periódico:

```bash
# Snapshot del SQLite (Authelia detecta el archivo abierto pero
# SQLite es safe para copia en caliente)
docker exec authelia sqlite3 /config/data/db.sqlite3 ".backup '/config/data/db-backup.sqlite3'"

# Copiar fuera del container
docker cp authelia:/config/data/db-backup.sqlite3 ./db-authelia-$(date +%Y%m%d).sqlite3
```

Guardalo en otra VM, S3, o lo que uses como destino de backups.

---

## Cheatsheet de comandos

```bash
# Entrar al directorio del proyecto en el server
cd ~/centro-monitoreo

# Generar hash de una contraseña
docker run --rm authelia/authelia:4.38 \
    authelia crypto hash generate argon2 --password 'MiClave2026!'

# Restart de containers (selectivos)
docker compose restart authelia
docker compose restart auth_service
docker compose restart mediamtx
docker compose restart   # todos (más seguro si dudás)

# Logs en vivo
docker compose logs -f authelia
docker compose logs -f auth_service
docker compose logs --tail=100 -f

# Ver el archivo de notifications de Authelia (links de 2FA)
docker exec authelia cat /config/data/notifications.txt

# Ver intentos de login (auditoría)
docker exec authelia sqlite3 /config/data/db.sqlite3 \
    "SELECT username, time, successful, remote_ip FROM authentication_logs ORDER BY time DESC LIMIT 20;"

# Estado del stack
docker compose ps
```

#!/usr/bin/env python3
"""Instalador del CRM Omnicanal. Solo biblioteca estándar: se ejecuta en el
ordenador de quien instala, sin pedirle que monte un entorno de Python.

Lo usan dos manos a la vez:

  · TÚ, en una segunda terminal ("la terminal de llaves"), para todo lo que
    escribe secretos o puede borrar datos: `secret set`, `mcp-setup`,
    `save-key`, `rollback`. El `!` de Claude Code NO tiene terminal de verdad,
    así que `getpass` revienta ahí; es a propósito que esos comandos no se
    puedan correr desde el chat.
  · EL AGENTE, desde el chat, para lo que no enseña valores: `doctor`,
    `secrets --check`, `push-env`, `wait`, `verify`, `status`, `urls`, `update`.

Regla de oro: este script NUNCA imprime el valor de un secreto. Enseña nombres,
longitudes y si está o no está. Lo que se ve en el chat acaba en el transcript.

Los datos de cada instalación viven en `~/.omnicanal/<dominio>.env` (permisos
600, en tu carpeta personal y no junto al script) y el índice en
`~/.omnicanal/instances.json`. Hasta la 0.11.0 vivían en `.instalacion/`, junto
al script: la primera vez se copian de ahí (`_traer_llaves_de_antes`).
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import secrets as _secrets
import shlex
import shutil
import socket
import string
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
# En tu carpeta personal y no junto al script: el paquete del curso llega como
# un zip nuevo en cada versión, y cada zip es una carpeta nueva. Con las llaves
# dentro, la carpeta nueva no traía el token de Coolify ni el registro que usa
# `rollback`, y operar desde otra copia de la que instaló fallaba sin decir por qué.
DIR_INSTALACION = Path.home() / ".omnicanal"
INDICE = DIR_INSTALACION / "instances.json"
# Donde vivían hasta la 0.11.0. Se copian a DIR_INSTALACION la primera vez.
DIR_ANTIGUO = RAIZ / ".instalacion"
MARCA_TRAIDA = "YA-NO-SE-USA.txt"

# Las cuatro que no pueden salir de la terminal de llaves. El resto de la
# configuración (dominio, perfil, versión) no es secreta y se puede ver.
SECRETOS = ("POSTGRES_PASSWORD", "JWT_SECRET", "ENCRYPTION_KEY", "INITIAL_ADMIN_PASSWORD")
# Token de GitHub para bajar las imágenes privadas de GHCR. No va al servidor
# por variable de entorno: se usa una vez para `docker login`.
SECRETOS_EXTRA = ("GHCR_TOKEN", "COOLIFY_TOKEN")
# Claves de servicios que también se guardan aquí. No son de la instalación (no
# las exige `secrets --check`), pero `secret list` tampoco puede enseñarlas: antes
# las imprimía en claro porque solo conocía las dos listas de arriba.
CLAVES_DE_SERVICIOS = (
    "OPENAI_API_KEY",
    "YCLOUD_API_KEY",
    "YCLOUD_WEBHOOK_SECRET",
    "EMAIL_APP_PASSWORD",
    "ZERNIO_API_KEY",
    "RETELL_API_KEY",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
)
# Estos no son de una instalación, son tuyos: el token de GitHub y el de
# Coolify valen para todas. Guardarlos por dominio obligaba a volver a pegarlos
# cada vez que se instala otra — y peor, a pegarlos ANTES de saber el dominio,
# que es lo que pasó en la primera prueba en frío.
GLOBALES = ("GHCR_TOKEN", "GHCR_USER", "COOLIFY_TOKEN", "COOLIFY_BASE_URL")
# No son secretos, así que `secret set` los deja ver al escribirlos: un correo
# tecleado a ciegas sale mal sin que se note hasta que el panel no deja entrar.
SE_VEN_AL_ESCRIBIR = ("INITIAL_ADMIN_EMAIL", "COOLIFY_BASE_URL", "GHCR_USER")

VERSION_MCP_COOLIFY = "@masonator/coolify-mcp@3.5.1"

ROJO, VERDE, AMARILLO, GRIS, FIN = "\033[31m", "\033[32m", "\033[33m", "\033[90m", "\033[0m"


def _en_windows() -> bool:
    return os.name == "nt"


def _salida_en_utf8(*flujos) -> None:
    """Que imprimir ✓, ✗ o → no reviente en Windows.

    Con la salida por una tubería (así corre omni.py Claude Code), Python en
    Windows escribe con la página de códigos del sistema, cp1252, que no tiene
    esos símbolos: el primer `ok()` lanzaba UnicodeEncodeError y el comando
    moría a medias. En UTF-8, y un carácter imposible nunca lo tumba.
    """
    for flujo in flujos or (sys.stdout, sys.stderr):
        codificacion = (getattr(flujo, "encoding", "") or "").lower().replace("-", "")
        reconfigurar = getattr(flujo, "reconfigure", None)
        if reconfigurar is None or codificacion == "utf8":
            continue
        try:
            reconfigurar(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _programa(nombre: str) -> str:
    """La ruta de un programa del PATH. En Windows `npx` (y `claude` si vino de
    npm) es un `.cmd`, y subprocess no lo encuentra por su nombre a secas."""
    return shutil.which(nombre) or nombre


def _colores_activos() -> bool:
    """En la consola clásica de Windows los colores salen como basura (`←[32m`):
    solo en Windows Terminal o en la terminal de VS Code, que sí los pintan."""
    if not sys.stdout.isatty():
        return False
    return not _en_windows() or bool(os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM"))


def _color(txt: str, c: str) -> str:
    return f"{c}{txt}{FIN}" if _colores_activos() else txt


def _terminal_de_llaves_en_windows() -> bool:
    """En Windows, getpass solo oculta lo que escribes en una consola de verdad
    (PowerShell, Windows Terminal). En Git Bash avisa y lo enseña en claro."""
    if _en_windows() and not sys.stdin.isatty():
        falla(
            "aquí no se pueden escribir llaves",
            "en Windows se escriben en PowerShell o en Windows Terminal: Git Bash no "
            "puede ocultar lo que escribes y la llave se vería en pantalla",
        )
        return False
    return True


def ok(msg: str) -> None:
    print(f"{_color('✓', VERDE)} {msg}")


def falla(msg: str, solucion: str) -> None:
    """Un fallo SIEMPRE trae qué hacer. Un ✗ a secas deja a la persona parada."""
    print(f"{_color('✗', ROJO)} {msg}\n  {_color('→ solución:', AMARILLO)} {solucion}")


def aviso(msg: str) -> None:
    print(f"{_color('!', AMARILLO)} {msg}")


# ---------------------------------------------------------------------------
# Archivo de la instalación
# ---------------------------------------------------------------------------

def _ruta_env(dominio: str) -> Path:
    return DIR_INSTALACION / f"{dominio}.env"


RUTA_GLOBAL = "global"


def _leer_archivo(ruta: Path) -> dict:
    if not ruta.is_file():
        return {}
    datos = {}
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if not linea.strip() or linea.lstrip().startswith("#") or "=" not in linea:
            continue
        k, _, v = linea.partition("=")
        datos[k.strip()] = v.strip()
    return datos


def _leer_env(dominio: str) -> dict:
    """Lo global primero y lo de la instalación encima, que manda."""
    datos = _leer_archivo(_ruta_env(RUTA_GLOBAL))
    datos.update(_leer_archivo(_ruta_env(dominio)))
    return datos


def _escribir_env(dominio: str, datos: dict) -> None:
    """Escribe con permisos 600 desde el principio.

    Con umask: si se creara con los permisos por defecto y se ajustaran
    después, habría un instante en el que las llaves son legibles por
    cualquiera del sistema. Es corto, pero es real.
    """
    DIR_INSTALACION.mkdir(mode=0o700, exist_ok=True)
    ruta = _ruta_env(dominio)
    contenido = "# Llaves de esta instalación. NO lo subas a git ni lo pegues en un chat.\n"
    contenido += "# Si pierdes ENCRYPTION_KEY, las credenciales guardadas no se recuperan.\n"
    contenido += "".join(f"{k}={v}\n" for k, v in sorted(datos.items()))
    anterior = os.umask(0o077)
    try:
        ruta.write_text(contenido, encoding="utf-8")
    finally:
        os.umask(anterior)
    os.chmod(ruta, 0o600)


def _indice() -> dict:
    if INDICE.is_file():
        return json.loads(INDICE.read_text(encoding="utf-8"))
    return {}


def _guardar_indice(datos: dict) -> None:
    DIR_INSTALACION.mkdir(mode=0o700, exist_ok=True)
    INDICE.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")


def _copiar_privado(origen: Path, destino: Path) -> None:
    """Copia con permisos 600 desde el primer byte (el porqué, en `_escribir_env`)."""
    anterior = os.umask(0o077)
    try:
        destino.write_bytes(origen.read_bytes())
    finally:
        os.umask(anterior)
    os.chmod(destino, 0o600)


def _juntar_indice(origen: Path) -> list | None:
    """Añade al índice las instalaciones del índice viejo que no estén ya.

    Una instalación que ya está apuntada no se pisa: su `ultima_actualizacion`
    puede ser más reciente y es lo que usa `rollback`. None si el viejo no se lee.
    """
    try:
        viejo = json.loads(origen.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(viejo, dict):
        return None
    idx = _indice()
    nuevas = [d for d in viejo if d not in idx]
    if nuevas:
        idx.update({d: viejo[d] for d in nuevas})
        _guardar_indice(idx)
    return nuevas


def _traer_llaves_de_antes() -> None:
    """La primera vez, copia a DIR_INSTALACION lo que había junto al script.

    Solo lo que falta: si en la carpeta nueva ya hay un archivo con ese nombre,
    manda el suyo, que puede ser más reciente. La carpeta vieja no se borra:
    guarda llaves de instalaciones reales y eso lo decide la persona. Se deja
    dentro un aviso de que ya no se usa, que es también la marca para no volver
    a copiarla. Nunca se imprime un valor, solo nombres de archivo y dominios.
    """
    antiguo = DIR_ANTIGUO
    if not antiguo.is_dir() or (antiguo / MARCA_TRAIDA).exists():
        return
    if antiguo.resolve() == DIR_INSTALACION.resolve():
        return
    archivos = sorted(p for p in antiguo.iterdir() if p.is_file() and not p.name.startswith("."))
    if not archivos:
        return
    DIR_INSTALACION.mkdir(mode=0o700, parents=True, exist_ok=True)
    copiados, ya_estaban = [], []
    for origen in archivos:
        destino = DIR_INSTALACION / origen.name
        if origen.name == INDICE.name:
            nuevas = _juntar_indice(origen)
            if nuevas is None:
                ya_estaban.append(f"{origen.name} (no se pudo leer)")
            elif nuevas:
                copiados.append(f"{origen.name} ({', '.join(nuevas)})")
        elif destino.exists():
            ya_estaban.append(origen.name)
        else:
            _copiar_privado(origen, destino)
            copiados.append(origen.name)
    try:
        (antiguo / MARCA_TRAIDA).write_text(
            f"Desde la 0.12.0, omni.py guarda las llaves en {DIR_INSTALACION}\n"
            f"y ya no lee esta carpeta. Se copiaron allí el {time.strftime('%Y-%m-%d %H:%M')}.\n"
            "Cuando compruebes que todo funciona, puedes borrarla: tiene llaves de verdad.\n",
            encoding="utf-8",
        )
    except OSError:
        pass  # sin la marca se vuelve a mirar la próxima vez; lo copiado no se repite
    if not copiados:
        return
    aviso(f"desde la 0.12.0 las llaves viven en {DIR_INSTALACION} (antes, en {antiguo})")
    ok(f"copiadas: {', '.join(copiados)}")
    if ya_estaban:
        print(_color(f"  sin copiar, porque ya estaban en la carpeta nueva y manda la suya: "
                     f"{', '.join(ya_estaban)}", GRIS))
    print(_color("  La carpeta vieja se queda como estaba: bórrala tú cuando quieras.", GRIS))


def _dominio_de(args) -> str:
    """Resuelve la instalación sobre la que se trabaja.

    Con una sola instalación no hay que escribir `--instance` nunca; con varias,
    se exige, porque adivinar aquí significaría tocar el servidor equivocado.
    """
    if getattr(args, "instance", None):
        return args.instance
    idx = _indice()
    if len(idx) == 1:
        return next(iter(idx))
    if not idx:
        print(f"No hay ninguna instalación apuntada todavía en {DIR_INSTALACION}.")
        print("  → solución: repite el comando añadiendo `--instance <tu dominio>`")
        sys.exit(1)
    print("Hay varias instalaciones: " + ", ".join(sorted(idx)))
    print("  → solución: repite el comando añadiendo `--instance <dominio>`")
    sys.exit(1)


def _normaliza_dominio(valor: str) -> str:
    """Quita https:// y la barra final: es el copiar y pegar de siempre."""
    v = valor.strip()
    v = re.sub(r"^https?://", "", v)
    return v.split("/")[0].strip().lower()


# ---------------------------------------------------------------------------
# doctor — ¿está el ordenador y el servidor listos?
# ---------------------------------------------------------------------------

def _hay(programa: str) -> bool:
    return shutil.which(programa) is not None


def _resolver(dominio: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(dominio, None)
    except socket.gaierror:
        return []
    return sorted({i[4][0] for i in infos})


def _puerto_abierto(host: str, puerto: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, puerto), timeout=timeout):
            return True
    except OSError:
        return False


def cmd_doctor(args) -> int:
    """Comprueba el entorno ANTES de tocar nada.

    Cada fallo aquí es media hora de despiste después: un DNS sin propagar se
    manifiesta como un certificado que no se emite, y el 80 cerrado, como un
    despliegue verde al que no se entra.
    """
    problemas = 0
    print("— Tu ordenador —")
    if sys.version_info >= (3, 9):
        ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")
    else:
        problemas += 1
        falla("Python muy antiguo", "instala Python 3.9 o superior desde python.org")
    for prog, pista in (
        ("git", "instala git: https://git-scm.com/downloads"),
        ("ssh", "en Windows, instala Git Bash o activa OpenSSH"),
        ("node", "instala Node 20 o superior desde nodejs.org (lo pide el MCP de Coolify)"),
        ("claude", "instala Claude Code: https://code.claude.com/docs/en/setup"),
    ):
        if _hay(prog):
            ok(prog)
        else:
            problemas += 1
            falla(f"falta {prog}", pista)

    dominio = getattr(args, "instance", None) or ""
    ip_servidor = getattr(args, "ip", None) or _leer_env(dominio).get("SERVER_IP", "")

    if dominio:
        print("\n— Tu dominio —")
        ips = _resolver(dominio)
        if not ips:
            problemas += 1
            falla(
                f"{dominio} no resuelve a ninguna IP",
                "crea un registro A apuntando a la IP de tu servidor y espera unos minutos; "
                "compruébalo con `dig +short " + dominio + "`",
            )
        elif ip_servidor and ip_servidor not in ips:
            problemas += 1
            falla(
                f"{dominio} resuelve a {', '.join(ips)} y tu servidor es {ip_servidor}",
                "corrige el registro A; si acabas de cambiarlo, espera a que caduque la caché",
            )
        else:
            ok(f"{dominio} → {', '.join(ips)}")
            if any(":" in i for i in ips) and not any("." in i for i in ips):
                aviso(
                    "solo hay registro AAAA (IPv6). Coolify comprueba la salud por IPv4: "
                    "añade también un registro A"
                )

    if ip_servidor:
        print("\n— Tu servidor —")
        for puerto, para_que in ((22, "SSH"), (80, "certificado"), (443, "panel")):
            if _puerto_abierto(ip_servidor, puerto):
                ok(f"puerto {puerto} abierto ({para_que})")
            else:
                problemas += 1
                falla(
                    f"puerto {puerto} cerrado ({para_que})",
                    "abre el puerto en el cortafuegos del proveedor; sin el 80 no hay "
                    "certificado y el panel no carga por https",
                )
        salida = _ssh(ip_servidor, "free -m | awk '/^Mem:/{print $2}'", args)
        if salida.isdigit():
            mb = int(salida)
            # 1900 y no 2048: un VPS "de 2 GB" declara algo menos tras el kernel.
            if mb >= 1900:
                ok(f"{mb} MB de RAM")
            else:
                problemas += 1
                falla(
                    f"{mb} MB de RAM",
                    "sube el plan del VPS: con menos de 2 GB el primer arranque se queda "
                    "sin memoria a mitad de las migraciones",
                )
        elif ip_servidor:
            aviso("no se pudo entrar por SSH para mirar la RAM; "
                  "comprueba que tu llave está en el servidor (`ssh-copy-id`)")

    print()
    if problemas:
        print(_color(f"{problemas} cosa(s) que arreglar antes de instalar.", ROJO))
        return 1
    print(_color("Todo listo para instalar.", VERDE))
    return 0


def _cmd_ssh(usuario: str, ip: str, comando: str) -> list[str]:
    """BatchMode: sin llave cargada falla rápido en vez de quedarse esperando una
    contraseña que nadie va a escribir (y que colgaría al agente)."""
    return [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=15", f"{usuario}@{ip}", comando,
    ]


def _correr(cmd: list[str], entrada: str | None, timeout: int) -> tuple[int, str, str]:
    """Corre `cmd` y devuelve (código, salida, errores) como texto.

    La entrada va en BYTES, no en modo texto: en Windows, Python cambia cada
    `\n` de la entrada por `\r\n`, y el bash del servidor no entiende ese `\r`
    (`update` decía «no puedo entrar por SSH» con la llave bien puesta).
    Lanza lo mismo que `subprocess.run`.
    """
    r = subprocess.run(
        cmd,
        input=entrada.encode("utf-8") if entrada is not None else None,
        capture_output=True,
        timeout=timeout,
    )
    return (
        r.returncode,
        r.stdout.decode("utf-8", errors="replace"),
        r.stderr.decode("utf-8", errors="replace"),
    )


def _ssh(ip: str, comando: str, args=None, entrada: str | None = None) -> str:
    """Un comando por SSH. Devuelve la salida o "" si falla."""
    usuario = getattr(args, "user", None) or "root"
    try:
        codigo, salida, _ = _correr(_cmd_ssh(usuario, ip, comando), entrada, 120)
        return salida.strip() if codigo == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


# ---------------------------------------------------------------------------
# Secretos: generarlos, guardarlos y enseñar SOLO si están
# ---------------------------------------------------------------------------

def _clave_fernet() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def _contrasena_admin() -> str:
    """16 caracteres con letras, números y símbolos.

    Se construye a mano y no con `token_urlsafe` porque la política del panel
    exige mezcla (backend/app/core/security.py): una cadena aleatoria puede
    salir sin un solo dígito y el sembrado la rechazaría al arrancar, dejando
    la instalación sin administrador.
    """
    alfabeto = string.ascii_letters + string.digits + "!@#%*_-+="
    while True:
        c = "".join(_secrets.choice(alfabeto) for _ in range(16))
        if (any(x.islower() for x in c) and any(x.isupper() for x in c)
                and any(x.isdigit() for x in c) and any(x in "!@#%*_-+=" for x in c)):
            return c


def _generadores() -> dict:
    return {
        # hex, no base64: la contraseña de Postgres viaja dentro de una URL de
        # conexión y un "/" o un "+" la parten por la mitad.
        "POSTGRES_PASSWORD": lambda: _secrets.token_hex(24),
        "JWT_SECRET": lambda: base64.b64encode(os.urandom(48)).decode(),
        "ENCRYPTION_KEY": _clave_fernet,
        "INITIAL_ADMIN_PASSWORD": _contrasena_admin,
    }


def cmd_secrets(args) -> int:
    """Genera lo que falte. NUNCA rota lo que ya existe.

    Regenerar una ENCRYPTION_KEY ya usada deja ilegibles las credenciales, las
    notas internas y las transcripciones, sin aviso y sin vuelta atrás. Por eso
    este comando es de una sola dirección: rellena huecos.
    """
    dominio = _normaliza_dominio(args.instance) if args.instance else _dominio_de(args)
    datos = _leer_env(dominio)

    if args.check:
        print(f"Instalación: {dominio}")
        globales = _leer_archivo(_ruta_env(RUTA_GLOBAL))
        for nombre in SECRETOS + SECRETOS_EXTRA:
            valor = datos.get(nombre, "")
            donde = " (global)" if nombre in globales and nombre in GLOBALES else ""
            estado = f"guardado ({len(valor)} caracteres){donde}" if valor else "falta"
            marca = _color("✓", VERDE) if valor else _color("·", GRIS)
            print(f"  {marca} {nombre}: {estado}")
        print(f"\nArchivos: {_ruta_env(dominio)}")
        print(f"          {_ruta_env(RUTA_GLOBAL)} (lo que vale para todas)")
        print("Los valores no se imprimen nunca: esto se puede correr desde el chat.")
        return 0 if all(datos.get(n) for n in SECRETOS) else 1

    nuevos = []
    for nombre, genera in _generadores().items():
        if not datos.get(nombre):
            datos[nombre] = genera()
            nuevos.append(nombre)
    datos.setdefault("DOMINIO", dominio)
    datos.setdefault("SEED_PROFILE", "dental-mx")
    if getattr(args, "ip", None):
        datos["SERVER_IP"] = args.ip
    if getattr(args, "email", None):
        datos["INITIAL_ADMIN_EMAIL"] = args.email
    _escribir_env(dominio, datos)

    idx = _indice()
    idx.setdefault(dominio, {})["actualizado"] = time.strftime("%Y-%m-%d %H:%M")
    _guardar_indice(idx)

    if nuevos:
        ok(f"generadas {len(nuevos)}: {', '.join(nuevos)}")
    else:
        ok("ya estaban las cuatro; no se ha tocado ninguna")
    print(f"Guardadas en {_ruta_env(dominio)} (permisos 600).")
    print("Siguiente: `python3 scripts/omni.py save-key` para apuntarlas en tu gestor.")
    return 0


def cmd_secret(args) -> int:
    """`secret set <nombre>`: pide un valor a mano, sin eco y sin argumentos.

    A propósito NO acepta el valor por argumento: acabaría en el historial de
    la shell y, si se corre desde el chat, en el transcript.
    """
    if args.accion != "set":
        dominio = _dominio_de(args)
        datos = _leer_env(dominio)
        for nombre in sorted(datos):
            if nombre in SECRETOS + SECRETOS_EXTRA + CLAVES_DE_SERVICIOS:
                print(f"{nombre}: guardado ({len(datos[nombre])} caracteres)")
            else:
                print(f"{nombre}: {datos[nombre]}")
        return 0

    nombre = args.nombre.upper()
    # Un token de GitHub no pertenece a ninguna instalación: si se pide antes de
    # saber el dominio (que es el orden natural), guardarlo por dominio obliga a
    # repetirlo después.
    destino = RUTA_GLOBAL if nombre in GLOBALES else _dominio_de(args)
    if nombre not in SE_VEN_AL_ESCRIBIR and not _terminal_de_llaves_en_windows():
        return 1
    try:
        if nombre in SE_VEN_AL_ESCRIBIR:
            valor = input(f"Valor de {nombre}: ").strip()
        else:
            valor = getpass.getpass(f"Valor de {nombre} (no se ve al escribir): ").strip()
    except Exception:
        falla(
            "aquí no se puede escribir a ciegas",
            "esto va en tu segunda terminal, no en el chat: el `!` de Claude Code no "
            "tiene terminal de verdad y getpass no funciona",
        )
        return 1
    if not valor:
        falla("no has escrito nada", "vuelve a ejecutarlo y pega el valor")
        return 1
    # Se comprueba AQUÍ lo que el panel va a exigir después. Guardar una
    # contraseña que no cumple la política se descubre dos pasos más tarde,
    # como un "credenciales inválidas" que parece cualquier otra cosa.
    if nombre == "INITIAL_ADMIN_PASSWORD":
        if len(valor) < 12:
            falla(
                f"esa contraseña tiene {len(valor)} caracteres y el panel pide 12",
                "vuelve a escribirla entera; si la estás pegando, revisa que no se "
                "haya cortado",
            )
            return 1
        if not (any(c.isalpha() for c in valor) and any(c.isdigit() for c in valor)):
            falla("esa contraseña no mezcla letras y números",
                  "el panel las rechaza; usa una con las dos cosas")
            return 1
    datos = _leer_archivo(_ruta_env(destino))
    if datos.get(nombre) and nombre == "ENCRYPTION_KEY":
        aviso("ya había una ENCRYPTION_KEY guardada.")
        print("Cambiarla deja ilegible todo lo cifrado. Escribe SUSTITUIR para continuar:")
        if input("> ").strip() != "SUSTITUIR":
            print("Cancelado, no se ha tocado nada.")
            return 1
    datos[nombre] = valor
    _escribir_env(destino, datos)
    ok(f"{nombre} guardado ({len(valor)} caracteres) en {_ruta_env(destino)}")
    if destino == RUTA_GLOBAL:
        print("Vale para todas tus instalaciones: no hay que repetirlo por dominio.")
    return 0


def cmd_save_key(args) -> int:
    """Enseña la llave y la contraseña UNA vez y no sigue sin confirmación.

    La ENCRYPTION_KEY no se puede recuperar de ningún sitio: si se pierde, las
    credenciales de los proveedores y las notas internas quedan ilegibles para
    siempre. Este es el único comando que imprime valores, y por eso solo
    funciona en una terminal de verdad.
    """
    if not sys.stdin.isatty():
        falla(
            "esto no se corre desde el chat",
            "ábrelo en tu segunda terminal: imprime la llave en pantalla",
        )
        return 1
    dominio = _dominio_de(args)
    datos = _leer_env(dominio)
    if not datos.get("ENCRYPTION_KEY"):
        falla("no hay llaves generadas todavía", "corre antes `omni.py secrets`")
        return 1
    print(_color("\n══ Guarda esto en tu gestor de contraseñas ANTES de seguir ══", AMARILLO))
    print(f"Panel:       https://{dominio}")
    print(f"Usuario:     {datos.get('INITIAL_ADMIN_EMAIL', '(sin definir)')}")
    print(f"Contraseña:  {datos['INITIAL_ADMIN_PASSWORD']}")
    print(f"ENCRYPTION_KEY: {datos['ENCRYPTION_KEY']}")
    print(_color(
        "\nSin la ENCRYPTION_KEY no se recupera NINGUNA credencial guardada: ni las\n"
        "claves de los proveedores, ni las notas internas, ni las transcripciones.\n"
        "No se puede regenerar. No la cambies nunca.", ROJO))
    print("\nEscribe GUARDADO para continuar:")
    if input("> ").strip() != "GUARDADO":
        print("Cancelado. Vuelve cuando lo tengas apuntado.")
        return 1
    ok("perfecto, seguimos")
    return 0


def cmd_mcp_setup(args) -> int:
    """Registra el MCP de Coolify en Claude Code sin que el token pase por el chat."""
    if not _hay("claude"):
        falla("no encuentro el comando `claude`",
              "instala Claude Code: https://code.claude.com/docs/en/setup")
        return 1
    if not _hay("npx"):
        falla("no encuentro `npx`",
              "instala Node.js (la versión LTS, desde nodejs.org): el MCP de Coolify "
              "corre con él. Para actualizar sin el MCP basta con `omni.py secret set "
              "COOLIFY_BASE_URL` y `omni.py secret set COOLIFY_TOKEN`")
        return 1
    if not _terminal_de_llaves_en_windows():
        return 1
    try:
        url = input("URL de tu Coolify (ej. https://coolify.midominio.com): ").strip()
        token = getpass.getpass("Token de la API de Coolify (no se ve al escribir): ").strip()
    except Exception:
        falla("aquí no se puede escribir a ciegas",
              "esto va en tu segunda terminal; el `!` de Claude Code no tiene terminal")
        return 1
    if not url or not token:
        falla("faltan la URL o el token",
              "el token se crea en Coolify → Keys & Tokens → API tokens, con permisos "
              "read, write y deploy")
        return 1
    url = url.rstrip("/")

    # Primero se comprueba y después se registra. Al revés, una URL equivocada
    # dejaba el MCP registrado con ella, y al reintentar con la buena Claude Code
    # contestaba «already exists»: no había forma de corregirla sin saber
    # `claude mcp remove`.
    print("Comprobando el token contra tu Coolify…")
    d = subprocess.run(
        [_programa("npx"), "-y", VERSION_MCP_COOLIFY, "doctor"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "COOLIFY_BASE_URL": url, "COOLIFY_ACCESS_TOKEN": token},
        timeout=180,
    )
    salida = (d.stdout + d.stderr).strip()
    # El doctor imprime la URL y el estado, nunca el token; aun así se recorta.
    print("\n".join(salida.splitlines()[-12:]))
    if d.returncode != 0:
        falla("el doctor del MCP no pasó",
              "revisa que el token tenga permisos read, write y deploy, y que la URL "
              "sea la de tu panel de Coolify (no la de tu CRM). No se registró nada: "
              "vuelve a correr `omni.py mcp-setup` con los datos buenos")
        return 1
    ok("Coolify responde")

    # Si ya había un MCP «coolify» en esta carpeta (otro Coolify, otro token), se
    # reemplaza: es justo lo que quiere quien vuelve a correr mcp-setup.
    quitar = subprocess.run(
        [_programa("claude"), "mcp", "remove", "coolify", "--scope", "local"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if quitar.returncode == 0:
        aviso("había un MCP «coolify» registrado en esta carpeta: lo reemplazo por este")
    # El token va en el argumento del proceso hijo, no impreso: lo que se
    # publica en pantalla es el comando con el valor tapado.
    cmd = [
        _programa("claude"), "mcp", "add", "coolify", "--scope", "local",
        "-e", f"COOLIFY_BASE_URL={url}",
        "-e", f"COOLIFY_ACCESS_TOKEN={token}",
        "--", "npx", "-y", VERSION_MCP_COOLIFY,
    ]
    print(_color(f"claude mcp add coolify --scope local -e COOLIFY_BASE_URL={url} "
                 f"-e COOLIFY_ACCESS_TOKEN=*** -- npx -y {VERSION_MCP_COOLIFY}", GRIS))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        falla("Claude Code no aceptó el MCP", (r.stderr or r.stdout).strip()[:300] or "sin detalle")
        return 1
    ok("MCP de Coolify registrado (alcance local: no se comparte con otros proyectos)")

    # También para omni.py: `update` habla con la API de Coolify directamente
    # (un script no puede usar el MCP), y así no hay que volver a pegar el token.
    globales = _leer_archivo(_ruta_env(RUTA_GLOBAL))
    globales.update({"COOLIFY_BASE_URL": url, "COOLIFY_TOKEN": token})
    _escribir_env(RUTA_GLOBAL, globales)
    ok(f"guardado también para omni.py en {_ruta_env(RUTA_GLOBAL)} (permisos 600)")
    aviso("reinicia Claude Code para que cargue el MCP (ctrl+c y vuelve a abrirlo)")
    return 0


# ---------------------------------------------------------------------------
# Servidor y Coolify
# ---------------------------------------------------------------------------

def cmd_login_registry(args) -> int:
    """`docker login ghcr.io` en el servidor, con el token del archivo local.

    Solo hace falta con imágenes privadas: desde la 0.12.0 las del curso son
    públicas y el servidor las baja sin login. Con privadas, sin este paso el
    despliegue se queda sin contenedores y Traefik contesta un 404 que no
    menciona el registro.
    """
    dominio = _dominio_de(args)
    datos = _leer_env(dominio)
    ip = args.ip or datos.get("SERVER_IP", "")
    token = datos.get("GHCR_TOKEN", "")
    usuario = args.github_user or datos.get("GHCR_USER", "")
    if not ip:
        falla("no sé la IP del servidor", "pásala con `--ip <ip>`")
        return 1
    if not token or not usuario:
        falla(
            "falta el token de GitHub o el usuario",
            "en tu segunda terminal: `omni.py secret set GHCR_TOKEN` y "
            "`--github-user <tu-usuario>`. El token es uno clásico con el permiso "
            "read:packages; sin ese permiso el login funciona pero el pull da 403",
        )
        return 1
    salida = _ssh(
        ip, f"docker login ghcr.io -u {usuario} --password-stdin", args, entrada=token + "\n"
    )
    if "Login Succeeded" not in salida:
        falla(
            "el servidor no pudo entrar en GHCR",
            "entra por SSH y prueba a mano: "
            f"`ssh {getattr(args, 'user', 'root')}@{ip}` y luego "
            f"`docker login ghcr.io -u {usuario}`. Si dice Login Succeeded pero el pull "
            "da 403, al token le falta read:packages",
        )
        return 1
    ok("el servidor ya puede bajar las imágenes privadas")
    datos["GHCR_USER"] = usuario
    _escribir_env(dominio, datos)
    return 0


def _credenciales_coolify(args) -> tuple[str, str, str]:
    """(URL, token, de dónde salen) de Coolify, los dos SIEMPRE del mismo sitio.

    Antes cada uno se buscaba por su lado: la URL guardada y el token de una
    variable de entorno que había quedado de un Coolify anterior. Coolify
    contestaba 401 y la búsqueda del service lo tapaba con «no encuentro un
    único service», que manda a buscar el problema donde no está. Ahora manda
    el par explícito (`--coolify-url` o `COOLIFY_BASE_URL` junto a
    `COOLIFY_ACCESS_TOKEN`) si está completo; si no, el par guardado.
    """
    explicito = (getattr(args, "coolify_url", None) or os.environ.get("COOLIFY_BASE_URL", ""),
                 os.environ.get("COOLIFY_ACCESS_TOKEN", ""))
    if all(explicito):
        return explicito[0].rstrip("/"), explicito[1], "COOLIFY_ACCESS_TOKEN de tu terminal"
    datos = _leer_env(_dominio_de(args))
    guardado = (datos.get("COOLIFY_BASE_URL", ""), datos.get("COOLIFY_TOKEN", ""))
    if all(guardado):
        return guardado[0].rstrip("/"), guardado[1], f"el token guardado en {DIR_INSTALACION}"
    falla(
        "falta la URL o el token de Coolify",
        "en tu segunda terminal: `omni.py mcp-setup` (desde la 0.11.0 los guarda "
        "también para omni.py), o `omni.py secret set COOLIFY_BASE_URL` y "
        "`omni.py secret set COOLIFY_TOKEN`: valen para todas tus instalaciones",
    )
    sys.exit(1)


def _pista_coolify(args, estado: int) -> str:
    """Qué revisar cuando Coolify dice que no, empezando por el token si es 401/403."""
    base, _, origen = _credenciales_coolify(args)
    if estado == 401:
        return (f"Coolify rechazó el token (HTTP 401): usé {origen} contra {base}. "
                "Si cambiaste de Coolify o el token caducó, guarda el nuevo con `omni.py "
                "mcp-setup` en tu segunda terminal"
                + ("; y quita COOLIFY_ACCESS_TOKEN de tu terminal, que manda sobre el guardado"
                   if origen.startswith("COOLIFY_ACCESS_TOKEN") else ""))
    if estado == 403:
        return (f"el token ({origen}) no tiene permiso: créalo en Coolify → Keys & Tokens "
                "con permisos read, write y deploy")
    if estado == 0:
        return f"no hay respuesta de {base}: ¿es la URL de tu panel de Coolify?"
    return "revisa el uuid y que el token tenga permisos read, write y deploy"


def _api_coolify(args, metodo: str, ruta: str, cuerpo: dict | None = None) -> tuple[int, dict]:
    base, token, _ = _credenciales_coolify(args)
    req = urllib.request.Request(
        f"{base}/api/v1{ruta}",
        method=metodo,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            texto = r.read().decode()
            return r.status, (json.loads(texto) if texto.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:300]}
    except urllib.error.URLError as e:
        return 0, {"error": str(e.reason)}


def cmd_push_env(args) -> int:
    """Sube las cuatro variables secretas a Coolify por su API.

    Va directo a la API y no por el MCP a propósito: el MCP devuelve los
    valores en claro al listarlos, y eso acabaría en el transcript. Aquí el
    agente solo ve cuántas se guardaron.
    """
    dominio = _dominio_de(args)
    datos = _leer_env(dominio)
    faltan = [n for n in SECRETOS if not datos.get(n)]
    if faltan:
        falla(f"faltan {', '.join(faltan)}", "corre antes `omni.py secrets`")
        return 1
    if not datos.get("INITIAL_ADMIN_EMAIL"):
        falla("falta el correo del administrador",
              "corre `omni.py secrets --email tu@correo`")
        return 1

    pares = {n: datos[n] for n in SECRETOS}
    pares["INITIAL_ADMIN_EMAIL"] = datos["INITIAL_ADMIN_EMAIL"]
    guardadas, errores = 0, []
    for clave, valor in pares.items():
        estado, cuerpo = _api_coolify(
            args, "POST", f"/services/{args.service}/envs",
            {"key": clave, "value": valor, "is_preview": False},
        )
        if estado in (200, 201):
            guardadas += 1
            continue
        # Ya existía: Coolify responde 409 (o 422 según versión) y toca PATCH.
        estado, cuerpo = _api_coolify(
            args, "PATCH", f"/services/{args.service}/envs",
            {"key": clave, "value": valor, "is_preview": False},
        )
        if estado in (200, 201):
            guardadas += 1
        else:
            errores.append(f"{clave} ({estado})")
    if errores:
        falla(
            f"no se pudieron guardar: {', '.join(errores)}",
            "comprueba que el uuid del service es correcto y que el token de Coolify "
            "tiene permiso de escritura",
        )
        return 1
    ok(f"guardado ✓ ({guardadas} variables)")
    print("Los valores no se imprimen: si necesitas verlos, míralos en la UI de Coolify.")
    # El service de esta instalación queda apuntado: `update` lo necesita.
    if _RE_UUID.match(args.service):
        _registrar(dominio, service=args.service)
    return 0


# ---------------------------------------------------------------------------
# wait / verify
# ---------------------------------------------------------------------------

class _dns_a_mano:
    """Resuelve un dominio a una IP concreta durante unas llamadas.

    Para qué: el DNS público puede estar ya correcto y tu computadora seguir
    con la respuesta vieja en caché (o negativa) diez minutos más. Sin esto,
    `wait` y `verify` fallan con "no responde" cuando el servidor está
    perfectamente — que es justo lo que pasó en la primera prueba en frío, y
    obligó a inventar `curl --resolve` a mano.

    Se toca el resolutor SOLO dentro del bloque, y solo para ese dominio: la
    conexión TLS sigue presentando el nombre real, así que el certificado se
    valida como toca.
    """

    def __init__(self, dominio: str, ip: str | None) -> None:
        self.dominio, self.ip, self.original = dominio, ip, None

    def __enter__(self):
        if not self.ip:
            return self
        self.original = socket.getaddrinfo

        def resolver(host, port, *args, **kwargs):
            if host == self.dominio:
                host = self.ip
            return self.original(host, port, *args, **kwargs)

        socket.getaddrinfo = resolver
        return self

    def __exit__(self, *_) -> None:
        if self.original:
            socket.getaddrinfo = self.original


def _get(url: str, timeout: int = 10, cabeceras: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=cabeceras or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:500]
    except Exception as e:  # noqa: BLE001 — aquí cualquier fallo es "aún no responde"
        return 0, str(e)


def _put(url: str, cuerpo: dict, cabeceras: dict | None = None, timeout: int = 30):
    return _post(url, cuerpo, cabeceras, timeout, metodo="PUT")


def _post(url: str, cuerpo: dict, cabeceras: dict | None = None, timeout: int = 30,
          metodo: str = "POST"):
    req = urllib.request.Request(
        url, method=metodo, data=json.dumps(cuerpo).encode(),
        headers={"Content-Type": "application/json", **(cabeceras or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            texto = r.read().decode(errors="replace")
            return r.status, (json.loads(texto) if texto.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode(errors="replace")[:300]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def _diagnostico_http(estado: int, cuerpo: str, dominio: str) -> str:
    """Traduce lo que devuelve un despliegue a medio hacer.

    Los tres síntomas de abajo son los que salen de verdad, y ninguno menciona
    su causa: el 404 lo escribe Traefik cuando no hay contenedor detrás.
    """
    if estado == 404 and "page not found" in cuerpo.lower():
        return ("Traefik responde pero no hay contenedor detrás. Suele ser que el "
                "servidor no pudo bajar las imágenes: mira el registro del despliegue "
                "en Coolify. «manifest unknown» es una versión que no está publicada; "
                "«denied», que las imágenes son privadas (`omni.py login-registry`).")
    if "no available server" in cuerpo.lower():
        return ("el contenedor existe pero no acepta conexiones todavía: sigue "
                "arrancando, o se quedó sin memoria a mitad de las migraciones.")
    if estado == 0:
        # Distinguir el certificado del DNS: mientras Let's Encrypt emite, el
        # handshake TLS falla y parecía un problema de dominio. En la prueba en
        # frío el mensaje mandaba a revisar un DNS que estaba perfecto.
        if any(p in cuerpo.lower() for p in
               ("certificate", "ssl", "handshake", "tlsv1", "certificado")):
            return ("el dominio resuelve pero el certificado todavía no está emitido. "
                    "Let's Encrypt tarda un minuto largo después de que el stack levanta; "
                    "si pasa de cinco, revisa que el puerto 80 esté abierto.")
        return (f"no hay respuesta de {dominio}: revisa el DNS (`omni.py doctor "
                f"--instance {dominio}`) y que el puerto 443 esté abierto. Si el DNS ya "
                f"está bien y es tu computadora la que no se entera, repite con `--ip`.")
    if estado in (502, 503):
        return "el proxy no encuentra la aplicación: aún arranca o se está reiniciando."
    return ""


def _ip_forzada(args, dominio: str) -> str | None:
    """IP a la que hablar cuando el DNS de tu computadora aún no está al día."""
    ip = getattr(args, "ip", None)
    if ip:
        return ip
    return None


def cmd_wait(args) -> int:
    """Espera a que la instalación esté viva, explicando lo que ve."""
    dominio = _dominio_de(args)
    base = f"https://{dominio}" if not args.http else f"http://{dominio}"
    ip = _ip_forzada(args, dominio)
    limite = time.time() + args.timeout
    ultimo = ""
    print(f"Esperando a {base}/health (hasta {args.timeout // 60} min)…")
    if ip:
        print(_color(f"  (hablando con {ip} sin esperar a que el DNS llegue aquí)", GRIS))

    with _dns_a_mano(dominio, ip):
        while time.time() < limite:
            estado, cuerpo = _get(f"{base}/health")
            if estado == 200:
                try:
                    salud = json.loads(cuerpo)
                except json.JSONDecodeError:
                    salud = {}
                if salud.get("db") == "ok" and salud.get("redis") == "ok":
                    worker = salud.get("worker")
                    ok(f"la API responde (db ok, redis ok, worker {worker})")
                    if worker != "ok":
                        # El primer latido tarda hasta un minuto: no es un fallo.
                        aviso("el worker aún no ha latido; suele tardar un minuto más")
                    return 0
                ultimo = f"db={salud.get('db')} redis={salud.get('redis')}"
            else:
                pista = _diagnostico_http(estado, cuerpo, dominio)
                if pista and pista != ultimo:
                    print(_color(f"  {pista}", GRIS))
                    ultimo = pista
            time.sleep(10)

    falla(
        f"{dominio} no respondió en {args.timeout // 60} minutos",
        "mira los registros del contenedor `app` en Coolify: si el último mensaje es "
        "de migraciones, dale más tiempo; si habla de autenticación contra Postgres, "
        "la contraseña de la base no coincide con POSTGRES_PASSWORD. Si el servidor ya "
        "está listo y lo que falla es tu DNS, repite con `--ip <ip del servidor>`",
    )
    return 1


def cmd_verify(args) -> int:
    """La prueba de que la instalación sirve, no solo de que arranca.

    Entra, mira el checklist, comprueba que los webhooks rechazan lo que no
    viene firmado, habla con el widget como hablaría un paciente y borra su
    propio rastro. Lo que exige del agente es lo importante: que conteste el
    horario SEMBRADO, no cualquier cosa que suene bien.
    """
    dominio = _dominio_de(args)
    datos = _leer_env(dominio)
    if _faltan_datos_de_instalacion(dominio, datos):
        return 1
    base = f"https://{dominio}" if not args.http else f"http://{dominio}"
    api = f"{base}/api/v1"
    fallos = 0
    # Mientras el DNS llega a tu computadora, `--ip` deja verificar igual.
    resolutor = _dns_a_mano(dominio, _ip_forzada(args, dominio))
    resolutor.__enter__()
    try:
        return _verify(args, dominio, datos, base, api, fallos)
    finally:
        resolutor.__exit__()


def _verify(args, dominio: str, datos: dict, base: str, api: str, fallos: int) -> int:
    estado, cuerpo = _get(f"{base}/health")
    if estado == 200:
        ok("la API responde")
    else:
        falla("la API no responde", _diagnostico_http(estado, cuerpo, dominio)
              or "corre `omni.py wait` y mira los registros en Coolify")
        return 1

    correo = datos.get("INITIAL_ADMIN_EMAIL", "")
    estado, cuerpo = _post(api + "/auth/login",
                           {"email": correo, "password": datos.get("INITIAL_ADMIN_PASSWORD", "")})
    if estado != 200 or not cuerpo.get("access_token"):
        # Llegar aquí ya significa que el archivo existe y trae correo y
        # contraseña: lo único que queda es que no sean los del panel.
        falla(
            f"el administrador no puede entrar ({correo})",
            "el sembrado solo crea el usuario la primera vez y nunca lo pisa, así que "
            "si el panel se instaló con otra contraseña —o la cambiaste desde Mi "
            "perfil— la de tu archivo local ya no vale"
            # Un 429 del limitador o un 500 no son "contraseña mala": si el
            # panel contestó otra cosa, se dice, que si no se depura a ciegas.
            + (f" · el panel respondió HTTP {estado}" if estado not in (401, 200) else ""),
        )
        return 1
    token = cuerpo["access_token"]
    cab = {"Authorization": f"Bearer {token}"}
    ok(f"el administrador entra ({correo})")

    estado, _ = _get(api + "/auth/me", cabeceras=cab)
    if estado == 200:
        ok("la sesión vale")
    else:
        fallos += 1
        falla("la sesión no vale", "revisa JWT_SECRET: si cambió, las sesiones no sirven")

    estado, cuerpo = _get(api + "/admin/onboarding", cabeceras=cab)
    if estado == 403 and "debe_cambiar_password" in cuerpo:
        # No es un fallo: es el primer paso del checklist, y hasta darlo la
        # sesión solo sirve para eso. Se avisa y se sigue con lo que no
        # necesita sesión de administrador.
        aviso("falta cambiar la contraseña con la que se instaló")
        print(_color(f"  Entra a {base} y cámbiala; hasta entonces el panel no deja"
                     " hacer nada más.", GRIS))
        fallos += _verify_widget(base, api, cab)
        print()
        print(_color("Instalación verificada (queda cambiar la contraseña).", VERDE))
        return 1 if fallos else 0
    if estado == 200:
        try:
            pasos = json.loads(cuerpo).get("steps") or json.loads(cuerpo).get("pasos") or []
            hechos = sum(1 for p in pasos if p.get("done") or p.get("hecho"))
            ok(f"checklist de inicio: {hechos} de {len(pasos)} pasos")
        except json.JSONDecodeError:
            ok("checklist de inicio disponible")
    else:
        fallos += 1
        falla("no se pudo leer el checklist", "mira los registros de `app`")

    # Avisos de los canales, como los eventos que YCloud no activó al conectar
    # WhatsApp: no tumban la verificación (el canal funciona a medias), pero
    # tienen que verse aquí y no solo en la tarjeta del panel.
    estado, cuerpo = _get(api + "/admin/channels/estado", cabeceras=cab)
    if estado == 200:
        try:
            for canal in json.loads(cuerpo).get("channels") or []:
                for texto in canal.get("avisos") or []:
                    aviso(f"{canal.get('canal')}: {texto}")
        except (json.JSONDecodeError, AttributeError):
            pass

    # Un webhook que acepte cualquier cosa es una puerta abierta: se comprueba
    # que SIN firma responde 401, no 200 "por si acaso".
    for ruta, nombre in (("/webhooks/ycloud", "YCloud"),):
        estado, _ = _post(api + ruta, {"prueba": True})
        if estado in (401, 403):
            ok(f"el webhook de {nombre} rechaza lo que no viene firmado ({estado})")
        elif estado == 404:
            aviso(f"el webhook de {nombre} no está montado en esta versión")
        else:
            fallos += 1
            falla(f"el webhook de {nombre} respondió {estado} sin firma",
                  "no debería aceptar nada sin firmar; revisa la configuración del canal")

    fallos += _verify_widget(base, api, cab)

    estado, cuerpo = _get(api + "/voice/status", cabeceras=cab)
    if estado == 200:
        ok("el módulo de voz responde")
    elif estado == 404:
        aviso("el módulo de voz no está activo en esta instalación")
    else:
        fallos += 1
        falla(f"el módulo de voz respondió {estado}", "mira los registros de `app`")

    print()
    if fallos:
        print(_color(f"{fallos} comprobación(es) fallaron.", ROJO))
        return 1
    print(_color("Instalación verificada.", VERDE))
    return 0


def _verify_widget(base: str, api: str, cab: dict) -> int:
    """Habla con el agente por el widget y exige el horario sembrado.

    Sin clave de IA la respuesta correcta es la derivación a una persona, no un
    error: la instalación en modo degradado también tiene que funcionar.
    """
    estado, cuerpo = _get(api + "/webchat/demo")
    if estado != 200:
        aviso("el widget de demostración no está publicado; me salto la prueba del agente")
        return 0
    api_key = json.loads(cuerpo).get("api_key", "")
    # Como el navegador en la página /demo, que vive en el dominio del CRM. El
    # perfil siembra el chat con ese dominio como único permitido, y el CRM
    # rechaza con 403 lo que llega sin origen (un script): sin esto, en una
    # instalación nueva esta prueba fallaba siempre.
    origen = {"Origin": base, "Referer": f"{base}/demo"}
    estado, sesion = _post(api + "/webchat/sessions", {"api_key": api_key}, cabeceras=origen)
    if estado != 200:
        falla(f"no se pudo abrir una sesión de widget ({estado})",
              "revisa los dominios permitidos del canal de chat web: para que funcione "
              f"/demo tiene que estar el del CRM ({urllib.parse.urlparse(base).hostname})")
        return 1
    visitor, token_ws = sesion["visitor_id"], sesion["session_token"]
    estado, enviado = _post(api + "/webchat/messages", {
        "visitor_id": visitor, "token": token_ws, "text": "¿Qué horario tienen?",
    }, cabeceras=origen)
    if estado != 200:
        falla(f"el widget no aceptó el mensaje ({estado})", "mira los registros de `app`")
        return 1
    conversacion = enviado.get("conversation_id", "")

    # El agente contesta en segundo plano (buffer de mensajes + LLM): se
    # consulta el historial hasta que aparezca algo suyo.
    respuesta = ""
    for _ in range(24):
        time.sleep(5)
        estado, cuerpo = _get(
            f"{api}/webchat/history?visitor_id={visitor}&token={token_ws}", timeout=15)
        if estado != 200:
            continue
        mensajes = json.loads(cuerpo).get("messages", [])
        # El widget devuelve `role`/`text` (en inglés, como el resto de su
        # contrato público); el inbox interno usa `rol`/`contenido`. Se aceptan
        # los dos para que esto no se rompa si un día se unifican.
        agente = [m for m in mensajes
                  if (m.get("role") or m.get("rol")) in ("assistant", "bot")]
        if agente:
            respuesta = " ".join((m.get("text") or m.get("contenido") or "") for m in agente)
            break
    _borrar_conversacion(api, cab, conversacion)

    if not respuesta:
        falla("el agente no contestó en dos minutos",
              "mira el contenedor `worker`: sin clave de IA debería derivar a una "
              "persona, y si no hay worker no contesta nadie")
        return 1

    texto = respuesta.lower()
    if any(p in texto for p in ("8:00", "17:00", "lunes a viernes", "8 a 5")):
        ok("el agente contesta con el horario sembrado")
        return 0
    if any(p in texto for p in ("persona", "equipo", "en un momento", "humano",
                                "te contestar", "escribir")):
        aviso("sin clave de IA: el agente deriva a una persona, que es lo correcto")
        print(_color(f"  respuesta: {respuesta[:160]}", GRIS))
        return 0
    falla(
        "el agente contestó algo que no es el horario sembrado",
        "revisa la base de conocimiento (¿se indexó?) y el prompt del agente de texto. "
        f"Contestó: {respuesta[:160]}",
    )
    return 1


def _borrar_conversacion(api: str, cab: dict, conversacion_id: str) -> None:
    """Deja la instalación como estaba: la conversación de prueba no se queda.

    Si falla, no es motivo para dar la verificación por mala — pero sí para
    decirlo, o quien reciba el panel se encuentra un chat que no reconoce.
    """
    if not conversacion_id:
        return
    req = urllib.request.Request(
        f"{api}/conversations/{conversacion_id}/archive", method="DELETE", headers=cab)
    try:
        with urllib.request.urlopen(req, timeout=20):
            ok("conversación de prueba archivada")
    except Exception:  # noqa: BLE001
        aviso(f"no se pudo archivar la conversación de prueba ({conversacion_id[:8]}…); "
              "bórrala desde el Inbox")


# ---------------------------------------------------------------------------
# connect — dejar la instalación usable
# ---------------------------------------------------------------------------

def _guarda_el_usuario_del_panel(dominio: str) -> str:
    return (
        "guarda en tu segunda terminal el correo y la contraseña con los que entras al "
        f"panel: `python3 scripts/omni.py secret set INITIAL_ADMIN_EMAIL --instance {dominio}` "
        f"y `python3 scripts/omni.py secret set INITIAL_ADMIN_PASSWORD --instance {dominio}`"
    )


def _faltan_datos_de_instalacion(dominio: str, datos: dict) -> bool:
    """Avisa si aquí no hay usuario del panel de esa instalación, ANTES de entrar.

    Sin el archivo, lo que se veía era un correo y una contraseña vacíos y un
    401, y el mensaje de antes echaba la culpa a la contraseña —"la de tu
    archivo local no vale"—: mandaba a buscar un problema que no estaba ahí. En
    la instalación en un clic es lo normal: el administrador se crea en el panel
    y aquí no queda nada hasta que la persona guarda su usuario.
    """
    ruta = _ruta_env(dominio)
    if not ruta.is_file():
        conocidas = [d for d in _indice() if d != RUTA_GLOBAL]
        falla(
            f"aquí no hay datos de {dominio}",
            f"omni.py guarda cada instalación en {DIR_INSTALACION} y esta no está. Si la "
            "instalaste en un clic desde Coolify (o desde otra computadora), "
            + _guarda_el_usuario_del_panel(dominio),
        )
        print(_color(
            f"  Instalaciones apuntadas: {', '.join(conocidas) if conocidas else 'ninguna'}.",
            GRIS,
        ))
        return True
    if not datos.get("INITIAL_ADMIN_EMAIL") or not datos.get("INITIAL_ADMIN_PASSWORD"):
        falla(
            f"el archivo de {dominio} no tiene el administrador",
            f"faltan INITIAL_ADMIN_EMAIL o INITIAL_ADMIN_PASSWORD en {ruta}: "
            + _guarda_el_usuario_del_panel(dominio),
        )
        return True
    return False


def _token_admin(api: str, datos: dict) -> str | None:
    estado, cuerpo = _post(api + "/auth/login", {
        "email": datos.get("INITIAL_ADMIN_EMAIL", ""),
        "password": datos.get("INITIAL_ADMIN_PASSWORD", ""),
    })
    return cuerpo.get("access_token") if estado == 200 else None


def cmd_connect(args) -> int:
    dominio = _dominio_de(args)
    datos = _leer_env(dominio)
    if _faltan_datos_de_instalacion(dominio, datos):
        return 1
    base = f"https://{dominio}" if not args.http else f"http://{dominio}"
    api = f"{base}/api/v1"
    token = _token_admin(api, datos)
    if not token:
        falla("no se pudo entrar como administrador", "corre antes `omni.py verify`")
        return 1
    cab = {"Authorization": f"Bearer {token}"}

    # Mientras la contraseña sea la de la instalación, la sesión solo sirve
    # para cambiarla: cualquier cosa de aquí abajo respondería 403.
    estado, cuerpo = _get(api + "/auth/me", cabeceras=cab)
    if estado == 200 and json.loads(cuerpo).get("debe_cambiar_password"):
        falla(
            "primero hay que cambiar la contraseña de la instalación",
            f"entra a {base}, cámbiala en Mi perfil y vuelve a lanzar esto",
        )
        return 1

    if args.que == "ai":
        clave = datos.get("OPENAI_API_KEY", "")
        if not clave:
            falla(
                "no hay clave de IA guardada",
                "en tu segunda terminal: `omni.py secret set OPENAI_API_KEY`",
            )
            return 1
        # Sin base_url = OpenAI oficial. Esa clave es la COMPARTIDA de la
        # instalación: la usan también los embeddings de la base de
        # conocimiento, la transcripción y la moderación.
        estado, cuerpo = _post(api + "/admin/llm-providers", {
            "name": "OpenAI", "api_key": clave, "is_default": True,
        }, cab)
        if estado in (409, 422):
            # Ya existe: se actualiza en vez de duplicar. Instalar dos veces no
            # puede dejar dos proveedores compitiendo por ser el de por defecto.
            listado, cuerpo_listado = _get(api + "/admin/llm-providers", cabeceras=cab)
            existente = None
            if listado == 200:
                for p in json.loads(cuerpo_listado):
                    if not (p.get("base_url") or "").strip():
                        existente = p["id"]
                        break
            if existente:
                estado, cuerpo = _put(f"{api}/admin/llm-providers/{existente}", {
                    "name": "OpenAI", "api_key": clave, "is_default": True,
                }, cab)
                cuerpo = cuerpo if isinstance(cuerpo, dict) else {}
                cuerpo.setdefault("id", existente)
        if estado not in (200, 201):
            falla(f"no se pudo guardar la clave ({estado})",
                  "mira Conexiones → Servicios en el panel; el detalle sale en "
                  "Logs en vivo")
            return 1
        proveedor = cuerpo.get("id")
        ok("clave de IA guardada (cifrada en la base de datos)")
        if proveedor:
            estado, cuerpo = _post(f"{api}/admin/llm-providers/{proveedor}/test", {}, cab)
            if estado == 200 and cuerpo.get("ok"):
                ok(f"la clave funciona: {cuerpo.get('message', 'el proveedor contesta')}")
            else:
                falla(f"la clave no pasó la prueba: {cuerpo.get('message', estado)}",
                      "revisa que sea de OpenAI, que esté activa y que tenga saldo")
                return 1
        return 0

    if args.que == "widget":
        estado, cuerpo = _post(api + "/admin/channels/webchat/provision", {}, cab)
        if estado not in (200, 201):
            falla(f"no se pudo preparar el chat web ({estado})",
                  "míralo en Conexiones → Chat web")
            return 1
        ok("chat web listo")
        if args.dominios:
            permitidos = [d.strip() for d in args.dominios.split(",") if d.strip()]
            estado, _ = _post(api + "/admin/channels/webchat/allowed-domains",
                              {"allowed_domains": permitidos}, cab)
            if estado in (200, 201):
                ok(f"dominios permitidos: {', '.join(permitidos)}")
            else:
                aviso("no se pudieron guardar los dominios; ponlos desde el panel. "
                      "Ojo: vacío significa que CUALQUIER web puede usar tu agente")
        else:
            aviso("sin `--dominios`, el widget queda abierto a cualquier web: "
                  "pon el de la clínica desde el panel antes de publicarlo")
        print(f"Snippet para la web: mira Conexiones → Chat web en {base}")
        return 0

    if args.que == "email":
        clave = datos.get("EMAIL_APP_PASSWORD", "")
        if not clave or not args.correo or not args.proveedor:
            falla(
                "falta la contraseña de aplicación, el correo o el proveedor",
                "en tu segunda terminal: `omni.py secret set EMAIL_APP_PASSWORD`, y pasa "
                "`--correo recepcion@clinica.mx --proveedor hostinger`",
            )
            return 1
        buzon = {"preset": args.proveedor, "usuario": args.correo, "password": clave}
        estado, cuerpo = _post(api + "/admin/channels/email/imap/probar", buzon, cab)
        if estado != 200:
            falla(f"no se pudo probar el buzón ({estado})", "el detalle sale en Logs en vivo")
            return 1
        for error in cuerpo.get("errores") or []:
            falla(error, "corrígelo y repite esto mismo")
        if not (cuerpo.get("imap_ok") and cuerpo.get("smtp_ok")):
            return 1
        ok("el buzón entra (IMAP) y puede enviar (SMTP)")
        estado, cuerpo = _post(api + "/admin/channels/email/imap", {
            **buzon, "auto_send": bool(args.envia_sola), "no_responder": [],
        }, cab)
        if estado not in (200, 201):
            falla(f"no se pudo guardar el buzón ({estado})", "míralo en Conexiones → Servicios → Correo")
            return 1
        ok(f"correo conectado ({args.correo})")
        if cuerpo.get("carpeta_enviados"):
            ok(f"las respuestas se copian en «{cuerpo['carpeta_enviados']}»")
        print("Checklist:")
        print("  · Manda un correo al buzón desde otra cuenta: en ≤ 3 min sale en Chats.")
        print("  · " + ("Sofía responde sola." if args.envia_sola else
                        "Sofía deja un borrador: revísalo y dale a Enviar."))
        return _esperar_si_toca(args, api, cab, "email")

    if args.que == "whatsapp":
        clave = datos.get("YCLOUD_API_KEY", "")
        numero = args.numero or datos.get("YCLOUD_PHONE_NUMBER", "")
        if not clave or not numero:
            falla(
                "falta la clave de YCloud o el número",
                "en tu segunda terminal: `omni.py secret set YCLOUD_API_KEY`, y pasa el "
                "número con `--numero +529981234567` (el que tienes dado de alta en YCloud)",
            )
            return 1
        # El secreto de firma NO se manda desde aquí, aunque haya uno guardado
        # en el archivo local. Lo crea el propio CRM al registrar el webhook, y
        # cuando YCloud reutiliza un endpoint ya existente el que vale es el que
        # esté en el panel. Mandarlo pisaba ese —bueno— con la copia local
        # —vieja, o un relleno de un intento anterior—, y el síntoma es que
        # deja de entrar todo sin que nada lo diga. Si falta, se pide con
        # «Rotar secreto del webhook».
        estado, cuerpo = _post(api + "/admin/channels/whatsapp/provision", {
            "provider": "ycloud",
            "api_key": clave,
            "phone_number": numero,
        }, cab)
        if estado not in (200, 201):
            falla(f"no se pudo conectar WhatsApp ({estado})",
                  "revisa la clave de YCloud y que el número esté dado de alta ahí; "
                  "el detalle sale en Logs en vivo")
            return 1
        ok(f"canal de WhatsApp conectado ({numero})")
        registro = cuerpo.get("webhook_estado", "manual")
        if registro == "registrado":
            ok("webhook dado de alta en YCloud y secreto de firma guardado")
        elif registro == "reutilizado":
            ok("ya había un webhook con esa URL: se actualizaron sus eventos")
        elif registro == "error":
            # Sin esto el alta salía en verde con el webhook sin crear: no
            # llega ni un mensaje y no hay nada en pantalla que lo explique.
            aviso("el canal quedó creado, pero YCloud NO aceptó el webhook")
            print(f"  Motivo: {cuerpo.get('webhook_error', '(sin detalle)')}")
            print(f"  URL que se le mandó: {cuerpo.get('webhook_url', '')}")
            print("  Sin webhook no entra ningún mensaje. Arregla eso y repite esto mismo.")
        if cuerpo.get("needs_secret"):
            aviso("falta el secreto de firma: sin él, los mensajes entran sin validar")
            if registro == "reutilizado":
                # Aquí NO sirve pegarlo a mano: YCloud solo enseña el secreto
                # al CREAR el webhook, así que quien conecta sobre una cuenta
                # que ya lo tenía no lo ha visto nunca. Hay que pedir uno nuevo.
                print(f"  Entra a {base} → Conexiones → WhatsApp y dale a")
                print("  «Rotar secreto del webhook»: el CRM le pide uno nuevo a YCloud")
                print("  y se lo guarda. No hay que copiar nada de su consola.")
            else:
                print("  En tu segunda terminal: `omni.py secret set YCLOUD_WEBHOOK_SECRET`")
                print(f"  y en YCloud, que el webhook apunte a {cuerpo.get('webhook_url', '')}")
        print("\nPara terminar, en la app WhatsApp Business del negocio:")
        print("  1. Escanea el QR de coexistencia que te enseña YCloud.")
        print("  2. Manda un mensaje desde otro teléfono y míralo llegar al Inbox.")
        print("  3. Contesta desde el celular: debe aparecer como operador y Sofía se aparta.")
        if registro == "error":
            # Sin webhook no puede entrar nada: esperar la prueba sería perder
            # cinco minutos para llegar al mismo aviso de arriba.
            return 1
        return _esperar_si_toca(args, api, cab, "whatsapp")

    if args.que == "social":
        return _connect_social(args, api, base, datos, cab) or _esperar_si_toca(
            args, api, cab, "social"
        )

    if args.que == "voice":
        resultado: dict = {}
        codigo = _connect_voice(args, api, datos, cab, resultado)
        if codigo or not resultado.get("telefono"):
            return codigo
        return _esperar_si_toca(args, api, cab, "voice")

    falla(f"no sé conectar «{args.que}»",
          "por ahora: `ai`, `widget`, `whatsapp`, `email`, `social` y `voice`")
    return 1


# Qué esperar después de conectar cada canal (Fase 10.2): los canales cuyo
# primer mensaje cuenta, cómo se llaman, qué hay que hacer y qué mirar si no llega.
_ESPERA = {
    "whatsapp": (
        ("whatsapp",), "WhatsApp",
        "Manda un WhatsApp de prueba al número del negocio desde otro teléfono.",
        "revisa en YCloud que el número esté conectado y el webhook activo; el motivo "
        "sale en el panel, en Registros",
    ),
    "social": (
        ("instagram_dm", "messenger"), "Instagram o Messenger",
        "Manda un DM de prueba a la cuenta de Instagram, o un mensaje a la página de "
        "Facebook, desde otra cuenta.",
        "revisa en Zernio que la cuenta siga conectada y que no haya otro bot (ManyChat) "
        "en ella; el motivo sale en el panel, en Registros",
    ),
    "email": (
        ("email",), "correo",
        "Manda un correo de prueba al buzón desde otra cuenta (el buzón se lee cada minuto).",
        "busca el correo en la carpeta de spam del buzón y mira Registros en el panel",
    ),
    "voice": (
        ("retell_voice",), "teléfono",
        "Llama al número de la clínica desde tu celular y di algo.",
        "revisa en Retell que el número conteste con este agente; el motivo sale en el "
        "panel, en Registros",
    ),
}


def _hora(valor) -> datetime | None:
    try:
        return datetime.fromisoformat(str(valor))
    except (TypeError, ValueError):
        return None


def _esperar_si_toca(args, api: str, cab: dict, que: str) -> int:
    """Espera a que entre el mensaje de prueba por el canal recién conectado.

    El alta puede salir en verde y el canal no recibir nada: un webhook que el
    proveedor no llama, otro bot conectado a la cuenta, un buzón que filtra.
    La única prueba de que funciona es un mensaje que entra, así que se le
    pregunta al CRM cada 10 s (`GET /admin/channels/estado`) comparando con la
    hora del SERVIDOR, no la de esta computadora. `--esperar 0` lo salta.
    """
    segundos = getattr(args, "esperar", 0) or 0
    if segundos <= 0:
        return 0
    canales, nombre, que_hacer, pista = _ESPERA[que]
    estado, cuerpo = _get(api + "/admin/channels/estado", cabeceras=cab)
    if estado != 200:
        aviso("no pude preguntarle al CRM por el canal: mira en Chats si llega la prueba")
        return 0
    inicio = json.loads(cuerpo)
    desde = _hora(inicio.get("ahora"))
    desde_ms = int(inicio.get("ahora_ms") or 0)
    for c in inicio.get("channels", []):
        if c.get("canal") not in canales:
            continue
        if c.get("enabled") is False:
            aviso(f"el canal de {nombre} está apagado: enciéndelo en Canales o no entrará nada")
        if c.get("paused"):
            aviso(f"Sofía está en Pausado en {nombre}: el mensaje entrará, pero no contestará")
    print(f"\n{que_hacer}")
    print(_color(f"Lo espero aquí: miro cada 10 s, hasta {max(segundos // 60, 1)} min "
                 "(Ctrl+C para dejarlo).", GRIS))
    errores_vistos: set = set()
    limite = time.time() + segundos
    while time.time() < limite:
        time.sleep(10)
        estado, cuerpo = _get(api + "/admin/channels/estado", cabeceras=cab)
        if estado != 200:
            continue
        datos = json.loads(cuerpo)
        for c in datos.get("channels", []):
            llego = _hora(c.get("last_inbound_at"))
            if c.get("canal") in canales and llego and desde and llego > desde:
                ok(f"entró el mensaje de prueba por {nombre}: el canal está en verde")
                return 0
        for e in datos.get("recent_errors", []):
            clave = (e.get("ts"), e.get("event"))
            if e.get("canal") in canales and (e.get("ts") or 0) >= desde_ms and clave not in errores_vistos:
                errores_vistos.add(clave)
                aviso(f"el CRM registró un error en {nombre}: {e.get('event')}")
    falla(f"no entró ningún mensaje por {nombre} en {max(segundos // 60, 1)} min", pista)
    return 1


def _detalle(cuerpo) -> str:
    """El `detail` de un error de la API, que `_post` devuelve como texto."""
    if not isinstance(cuerpo, dict):
        return ""
    if cuerpo.get("detail"):
        return str(cuerpo["detail"])
    try:
        return str(json.loads(cuerpo.get("error") or "{}").get("detail") or "")
    except (ValueError, AttributeError):
        return ""


def _connect_social(args, api: str, base: str, datos: dict, cab: dict) -> int:
    """Instagram y Messenger por Zernio (ZN-3).

    La llave se valida con la llamada más barata que la exige (listar cuentas),
    se enseñan las cuentas y el CRM registra él solo el aviso de mensajes con
    un secreto propio. La llave viaja al CRM, que la guarda cifrada; aquí solo
    se lee del archivo local, nunca se imprime.
    """
    clave = datos.get("ZERNIO_API_KEY", "")
    if not clave:
        falla(
            "no hay API key de Zernio guardada",
            "conecta tu Instagram y tu página en zernio.com (Connections) y, en tu "
            "segunda terminal: `omni.py secret set ZERNIO_API_KEY`",
        )
        return 1
    estado, cuerpo = _post(api + "/admin/zernio/probar", {"api_key": clave}, cab)
    if estado != 200:
        detalle = _detalle(cuerpo)
        falla(f"Zernio no acepta la llave ({estado})",
              detalle or "crea otra en Zernio → API keys → Create API key")
        return 1
    cuentas = cuerpo if isinstance(cuerpo, list) else []
    nombres = {"instagram": "Instagram", "facebook": "Facebook", "whatsapp": "WhatsApp"}
    if not cuentas:
        falla("la llave vale, pero no hay cuentas conectadas en Zernio",
              "conéctalas en zernio.com → Connections y repite esto mismo")
        return 1
    for c in cuentas:
        nombre = c.get("username") or c.get("display_name") or c.get("id")
        marca = " (reconectar en Zernio)" if c.get("needs_reconnection") else ""
        ok(f"{nombres.get(c.get('platform'), c.get('platform'))}: {nombre}{marca}")

    peticion = {
        "api_key": clave,
        "instagram_account_id": args.instagram or None,
        "facebook_account_id": args.facebook or None,
        "wa_por_zernio": bool(args.wa_por_zernio),
    }
    estado, cuerpo = _post(api + "/admin/zernio", peticion, cab)
    if estado not in (200, 201):
        detalle = _detalle(cuerpo)
        falla(f"no se pudo conectar ({estado})",
              detalle or "si hay varias cuentas de una plataforma, elige con "
              "`--instagram <id>` o `--facebook <id>`")
        return 1
    for canal in cuerpo.get("canales", []):
        tipo = "Messenger" if canal.get("tipo") == "messenger" else "Instagram"
        ok(f"canal de {tipo} conectado por Zernio")
    if cuerpo.get("webhook_registrado"):
        ok("aviso de mensajes registrado en Zernio con un secreto propio")
    else:
        # US-16 AC2: si no hay API para el webhook, se pega a mano.
        aviso("Zernio no aceptó el aviso de mensajes: pégalo a mano")
        print(f"  {cuerpo.get('aviso') or ''}")
        print(f"  URL: {cuerpo.get('webhook_url', '')}")
        print("  El secreto sale en el panel (Conexiones → Servicios → Zernio): no se imprime aquí.")
    if cuerpo.get("historial_encolado"):
        ok("trayendo el historial de las conversaciones (en segundo plano)")
    print("\nPara terminar:")
    print("  1. Desconecta ManyChat u otro bot de la cuenta de Instagram.")
    print("  2. Escribe un DM a la cuenta desde otra cuenta de Instagram: sale en Chats.")
    print("  3. Escribe a la página desde un Facebook personal que no administre la página.")
    print("  4. Contesta desde la app de Instagram: sale como operador y Sofía se aparta.")
    return 0


# Lo que el CRM comprueba al leer de vuelta Retell y Twilio, dicho para personas.
_COMPROBACIONES_VOZ = {
    "agente_publicado": "el agente está publicado",
    "contesta_el_crm": "contesta el CRM (WebSocket de esta instalación)",
    "idioma_es_419": "idioma español de Latinoamérica (es-419)",
    "webhook_del_crm": "los datos de cada llamada llegan al CRM",
    "grabaciones_firmadas": "grabaciones con enlace firmado",
    "numero_en_retell": "el número está en Retell",
    "numero_atado_al_agente": "el número contesta con este agente",
    "numero_en_twilio": "el número está en tu cuenta de Twilio",
    "numero_en_la_troncal": "el número cuelga de la troncal SIP",
    "entrada_hacia_retell": "la troncal manda las llamadas a Retell",
    "retell_autorizado": "Retell puede sacar llamadas por la troncal",
    "transferencias_permitidas": "la troncal deja pasar llamadas a recepción",
    "recepcion_permitida": "el número puede llamar a recepción",
}


def _imprimir_comprobaciones(checks: list) -> int:
    """Pinta lo comprobado y devuelve cuántas cosas fallan."""
    fallos = 0
    for c in checks:
        texto = _COMPROBACIONES_VOZ.get(c.get("name"), c.get("name"))
        if c.get("ok"):
            ok(texto)
        else:
            fallos += 1
            falla(f"{texto}: {c.get('detail', '')}",
                  "vuelve a lanzar esto mismo; si se repite, míralo en Retell con ese dato")
    return fallos


def _connect_voice(args, api: str, datos: dict, cab: dict, resultado: dict | None = None) -> int:
    """Voz por Retell (US-19): el CRM crea su agente apuntando a esta instalación,
    lo publica y, si se pide, le compra un número de EE. UU. o le conecta uno de
    Twilio (México) por troncal SIP.

    Las llaves se leen del archivo local y viajan al CRM, que guarda la de Retell
    cifrada; la de Twilio se usa para cablear la troncal y no se guarda. Aquí no
    se imprime ninguna.
    """
    clave = datos.get("RETELL_API_KEY", "")
    if not clave:
        falla(
            "no hay API key de Retell guardada",
            "créala en Retell → Settings → API Keys y, en tu segunda terminal: "
            "`omni.py secret set RETELL_API_KEY`",
        )
        return 1
    if args.twilio and not args.numero:
        falla("con --twilio hace falta el número",
              "pasa `--numero +529981234567` (el que compraste en Twilio)")
        return 1
    if args.comprar_numero and args.numero:
        falla("o compras un número en Retell o usas uno tuyo, no las dos cosas",
              "quita `--comprar-numero` o `--numero`")
        return 1
    if args.twilio and not (datos.get("TWILIO_ACCOUNT_SID") and datos.get("TWILIO_AUTH_TOKEN")):
        falla(
            "faltan las llaves de Twilio",
            "en tu segunda terminal: `omni.py secret set TWILIO_ACCOUNT_SID` y "
            "`omni.py secret set TWILIO_AUTH_TOKEN` (están en la portada de la consola de Twilio)",
        )
        return 1

    if args.comprar_numero:
        aviso("comprar un número en Retell se cobra cada mes en tu cuenta de Retell")
    estado, cuerpo = _post(api + "/admin/channels/retell/provision", {
        "api_key": clave,
        "create_agent": True,
        "voice_id": args.voz or "",
        "transfer_number": args.transferir_a or "",
        "buy_number": "us" if args.comprar_numero else None,
        "area_code": args.lada,
        # Un número que ya está en Retell se ata aquí; uno de Twilio, abajo.
        "phone_number": "" if args.twilio else (args.numero or ""),
    }, cab, timeout=90)
    if estado != 200:
        falla(f"no se pudo conectar la voz ({estado})",
              _detalle(cuerpo) or "el detalle sale en el panel, en Registros")
        return 1
    hecho = "creado y publicado" if cuerpo.get("agent_created") else "al día y publicado"
    ok(f"agente de voz {hecho} en Retell ({cuerpo.get('agent_id_retell', '')})")
    fallos = _imprimir_comprobaciones(cuerpo.get("checks") or [])
    telefono = cuerpo.get("phone_number") or ""

    if args.twilio:
        estado, cuerpo = _post(api + "/admin/channels/retell/twilio", {
            "account_sid": datos.get("TWILIO_ACCOUNT_SID", ""),
            "auth_token": datos.get("TWILIO_AUTH_TOKEN", ""),
            "phone_number": args.numero,
            "trunk_prefix": args.troncal or "",
        }, cab, timeout=120)
        if estado != 200:
            falla(f"no se pudo conectar el número de Twilio ({estado})",
                  _detalle(cuerpo) or "el detalle sale en el panel, en Registros")
            return 1
        nueva = " (creada ahora)" if cuerpo.get("trunk_created") else ""
        ok(f"troncal SIP {cuerpo.get('trunk_domain', '')}{nueva}")
        fallos += _imprimir_comprobaciones(cuerpo.get("checks") or [])
        telefono = cuerpo.get("phone_number") or args.numero

    if not telefono:
        aviso("el agente todavía no tiene número: `--comprar-numero` (EE. UU.) o "
              "`--twilio --numero +52…` (México)")
        return 1 if fallos else 0
    if resultado is not None:
        resultado["telefono"] = telefono
    print("\nPara terminar:")
    print(f"  1. Llama al {telefono} desde tu celular: contesta Sofía con el saludo del canal.")
    print("  2. Pide una cita y cuelga: la llamada sale en Voz con su transcripción.")
    print("  3. Si no contesta nadie, mira Registros en el panel: ahí sale el motivo.")
    return 1 if fallos else 0


# ---------------------------------------------------------------------------
# update / rollback — subir de versión sin miedo (Fase 10.3)
# ---------------------------------------------------------------------------

# Los contenedores que llevan la versión de la app. La base y Redis no cambian
# de imagen al actualizar. El servidor MCP no es un contenedor: va en `app`.
CON_VERSION = ("app", "worker", "beat", "frontend")
# Los que escriben en la base. Se paran antes de restaurar: así nadie escribe a
# media restauración y la imagen nueva no vuelve a aplicar lo que se deshace.
ESCRIBEN_EN_LA_BASE = ("app", "worker", "beat")
CARPETA_RESPALDOS = "omnicanal-respaldos"  # en el $HOME del usuario de SSH
RESPALDOS_QUE_SE_GUARDAN = 5
# Se cuentan antes y después: si algo baja, se perdió por el camino.
TABLAS_QUE_SE_CUENTAN = (
    ("contacts", "fichas"),
    ("conversations", "conversaciones"),
    ("messages", "mensajes"),
    ("appointments", "citas"),
)

_RE_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")
_RE_TITULO = re.compile(r"^## \[?([^\]\n]+)\]?", re.M)
# Lo que acaba dentro de un script de bash se valida antes: un uuid o un
# dominio con comillas o espacios no llega nunca al servidor.
_RE_UUID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")
_RE_DOMINIO = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")


def _version_tupla(texto: str):
    """`0.10.0` → (0, 10, 0, 1, ""). Una preliminar (`1.0.0-a`) va antes que la final."""
    m = _RE_VERSION.match((texto or "").strip())
    if not m:
        return None
    mayor, menor, parche, previa = m.groups()
    return int(mayor), int(menor), int(parche), 0 if previa else 1, previa or ""


def _compara_versiones(actual: str, nueva: str):
    """-1 si la nueva es posterior, 0 si es la misma, 1 si es anterior; None si
    la actual no es una versión (una instalación que corre `latest`)."""
    a, n = _version_tupla(actual), _version_tupla(nueva)
    if a is None:
        return None
    return (a > n) - (a < n)


def _bloques_del_changelog(texto: str) -> list:
    """[(versión, bloque)] en el orden del archivo. `[Sin publicar]` no cuenta."""
    titulos = list(_RE_TITULO.finditer(texto or ""))
    bloques = []
    for i, m in enumerate(titulos):
        version = m.group(1).strip().lstrip("v")
        if _version_tupla(version):
            fin = titulos[i + 1].start() if i + 1 < len(titulos) else len(texto)
            bloques.append((version, texto[m.start():fin].strip()))
    return bloques


def _version_del_repo():
    """La primera versión publicada del CHANGELOG de este repositorio.

    Por defecto se actualiza a ESTA y no a «la última que haya»: el `omni.py`
    que corre y la instalación quedan en la misma versión.

    El de la raíz es un enlace a `backend/CHANGELOG.md`, y en Windows git lo
    deja como un archivo de texto con esa ruta dentro: sin versiones, se pasa
    al de verdad en vez de dar la búsqueda por perdida.
    """
    for ruta in (RAIZ / "CHANGELOG.md", RAIZ / "backend" / "CHANGELOG.md"):
        if ruta.is_file():
            bloques = _bloques_del_changelog(ruta.read_text(encoding="utf-8"))
            if bloques:
                return bloques[0][0]
    return None


def _novedades_entre(changelog: str, desde: str, hasta: str) -> list:
    """Los bloques posteriores a `desde`, hasta `hasta` incluida."""
    a, b = _version_tupla(desde), _version_tupla(hasta)
    if a is None or b is None:
        return []
    return [bloque for version, bloque in _bloques_del_changelog(changelog)
            if a < _version_tupla(version) <= b]


def _tag(imagen: str) -> str:
    """`ghcr.io/x/crm-backend:0.10.0` → `0.10.0`. El `:puerto` de un registro no es un tag."""
    ultimo = imagen.split("@", 1)[0].rsplit("/", 1)[-1]
    return ultimo.split(":", 1)[1] if ":" in ultimo else "latest"


def _con_tag(imagen: str, tag: str) -> str:
    """La misma imagen con otro tag."""
    base, _, ultimo = imagen.split("@", 1)[0].rpartition("/")
    repo = ultimo.split(":", 1)[0]
    return f"{base}/{repo}:{tag}" if base else f"{repo}:{tag}"


def _registrar(dominio: str, **campos) -> None:
    idx = _indice()
    idx.setdefault(dominio, {}).update(campos)
    _guardar_indice(idx)


class _Destino:
    """Una instalación concreta: su dominio, su servidor y su service en Coolify."""

    def __init__(self, dominio: str, ip: str, servicio: str, args) -> None:
        self.dominio, self.ip, self.servicio, self.args = dominio, ip, servicio, args


def _remoto(destino: _Destino, script: str, timeout: int = 120) -> tuple:
    """Corre un script de bash en el servidor, pasándolo por la entrada de SSH.

    Por la entrada y no como argumento: así no hay comillas dentro de comillas,
    y lo que corre allí se lee aquí tal cual. Devuelve (código, salida, errores).
    """
    usuario = getattr(destino.args, "user", None) or "root"
    try:
        return _correr(_cmd_ssh(usuario, destino.ip, "bash -s"), script, timeout)
    except subprocess.TimeoutExpired:
        return 124, "", f"el servidor no terminó en {timeout} s"
    except (subprocess.SubprocessError, OSError) as e:
        return 255, "", str(e)


def _script(plantilla: str, **valores: str) -> str:
    """Rellena los `@NOMBRE@` de un script, solo con valores ya validados."""
    for clave, valor in valores.items():
        plantilla = plantilla.replace(f"@{clave}@", valor)
    return plantilla


# Coolify llama a cada contenedor `<nombre>-<uuid del service>`. Se lee con
# `docker inspect` y no con `docker ps`: da la imagen configurada con su tag
# (en `ps` puede salir su id) y la salud por separado.
_CONTENEDORES = r"""ids=$(docker ps -aq --filter "name=-@S@")
[ -z "$ids" ] || docker inspect --format \
  '{{.Name}}|{{.Config.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}' $ids
"""

# Las consultas usan las credenciales del propio contenedor de la base: ninguna
# contraseña viaja por SSH ni sale en pantalla.
_PSQL = ("docker exec -i db-@S@ sh -c "
         "'psql -X -q -tA -v ON_ERROR_STOP=1 -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\"'")

# La copia es SQL plano comprimido, no `-Fc`: así se restaura de una sola vez
# con psql, dentro de una transacción, y se puede comprobar que está entera
# (pg_dump escribe «dump complete» al terminar). Se guardan las cinco últimas.
_RESPALDAR = r"""set -euo pipefail
dir="$HOME/@CARPETA@/@DOMINIO@"
mkdir -p "$dir"
chmod 700 "$HOME/@CARPETA@" "$dir"
f="$dir/$(date +%Y%m%d-%H%M%S)-v@VERSION@.sql.gz"
trap 'rm -f "$f.parcial"' EXIT
base=$(docker exec db-@S@ sh -c 'psql -X -tA -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT pg_database_size(current_database())"')
libre=$(df -Pk "$dir" | awk 'NR==2 {print $4}')
if [ $((libre * 1024)) -lt $((base + 1073741824)) ]; then
  echo "SIN_ESPACIO libre_mb=$((libre / 1024)) base_mb=$((base / 1048576))" >&2
  exit 3
fi
docker exec db-@S@ sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip > "$f.parcial"
gunzip -t "$f.parcial"
gunzip -c "$f.parcial" | tail -n 5 | grep -F 'PostgreSQL database dump complete' > /dev/null
mv "$f.parcial" "$f"
chmod 600 "$f"
ls -1t "$dir"/*.sql.gz | tail -n +@SOBRAN@ | while read -r viejo; do rm -f -- "$viejo"; done
echo "$f|$(wc -c < "$f" | tr -d ' ')"
"""

# Todo o nada: el esquema se tira y se rehace dentro de la MISMA transacción
# que la copia. Si algo falla, Postgres deshace también el DROP y la base queda
# como estaba. `pg_restore --clean` no bastaría: deja vivas las tablas que la
# migración fallida creó, y la siguiente actualización tropezaría con ellas.
_RESTAURAR = r"""set -euo pipefail
f=@RESPALDO@
[ -f "$f" ] || { echo "NO_EXISTE $f" >&2; exit 4; }
gunzip -t "$f"
sql_otras="SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND backend_type = 'client backend';"
otras=$(printf '%s\n' "$sql_otras" | docker exec -i db-@S@ sh -c 'psql -X -tA -U "$POSTGRES_USER" -d "$POSTGRES_DB"')
if [ "$otras" != "0" ]; then
  echo "CONEXIONES $otras" >&2
  exit 5
fi
{
  printf '%s\n' "SET client_min_messages = warning;" "SET lock_timeout = '60s';" \
    "DROP SCHEMA public CASCADE;" "CREATE SCHEMA public;"
  gunzip -c "$f"
} | docker exec -i db-@S@ sh -c 'psql -X -q -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > /dev/null
echo RESTAURADO
"""


def _srv_contenedores(destino: _Destino):
    """{nombre: {imagen, estado, salud}} del service, o None si SSH falla."""
    codigo, salida, _ = _remoto(destino, _script(_CONTENEDORES, S=destino.servicio))
    if codigo != 0:
        return None
    sufijo = f"-{destino.servicio}"
    contenedores = {}
    for linea in salida.splitlines():
        partes = linea.strip().lstrip("/").split("|")
        if len(partes) == 4 and partes[0].endswith(sufijo):
            contenedores[partes[0][: -len(sufijo)]] = {
                "imagen": partes[1], "estado": partes[2], "salud": partes[3],
            }
    return contenedores


def _srv_bajar(destino: _Destino, imagenes: list) -> tuple:
    """Baja al servidor las imágenes de la versión nueva; (ok, detalle)."""
    script = "set -e\n" + "".join(f"docker pull -q {shlex.quote(i)}\n" for i in imagenes)
    codigo, salida, errores = _remoto(destino, script, timeout=900)
    return codigo == 0, (errores or salida).strip()[-300:]


def _srv_sql(destino: _Destino, sql: str) -> tuple:
    script = _script(_PSQL, S=destino.servicio) + f" <<'SQL'\n{sql}\nSQL\n"
    return _remoto(destino, script)


def _srv_revision(destino: _Destino):
    """La revisión de Alembic en la que está la base, o None si no se puede leer."""
    codigo, salida, _ = _srv_sql(destino, "SELECT version_num FROM alembic_version;")
    revision = ",".join(salida.split())
    return revision if codigo == 0 and revision else None


def _srv_recuento(destino: _Destino) -> dict:
    """Cuántas filas hay en las tablas que se cuentan (las que existan)."""
    tablas = ", ".join(f"'{t}'" for t, _ in TABLAS_QUE_SE_CUENTAN)
    codigo, salida, _ = _srv_sql(destino, (
        "SELECT t, (xpath('/row/c/text()', query_to_xml("
        "format('SELECT count(*) AS c FROM %I', t), false, true, '')))[1]::text "
        f"FROM unnest(ARRAY[{tablas}]) AS t WHERE to_regclass('public.' || t) IS NOT NULL;"
    ))
    recuento = {}
    if codigo == 0:
        for linea in salida.splitlines():
            tabla, _, n = linea.partition("|")
            if n.strip().isdigit():
                recuento[tabla.strip()] = int(n)
    return recuento


def _srv_respaldar(destino: _Destino, version: str) -> tuple:
    script = _script(
        _RESPALDAR, S=destino.servicio, DOMINIO=destino.dominio, VERSION=version,
        CARPETA=CARPETA_RESPALDOS, SOBRAN=str(RESPALDOS_QUE_SE_GUARDAN + 1),
    )
    return _remoto(destino, script, timeout=3600)


def _srv_parar(destino: _Destino) -> bool:
    """Para app, worker y beat. Con `docker stop` y no con la API de Coolify:
    vale en cualquier versión de Coolify, y un contenedor parado a mano no se
    vuelve a levantar solo (`restart: unless-stopped`)."""
    nombres = " ".join(f"{n}-{destino.servicio}" for n in ESCRIBEN_EN_LA_BASE)
    _remoto(destino, f"docker stop {nombres} > /dev/null 2>&1 || true\n", timeout=180)
    contenedores = _srv_contenedores(destino)
    if contenedores is None:
        return False
    return not any(contenedores.get(n, {}).get("estado") in ("running", "restarting")
                   for n in ESCRIBEN_EN_LA_BASE)


def _srv_restaurar(destino: _Destino, respaldo: str) -> tuple:
    script = _script(_RESTAURAR, S=destino.servicio, RESPALDO=shlex.quote(respaldo))
    return _remoto(destino, script, timeout=3600)


def _srv_changelog(destino: _Destino) -> str:
    """El CHANGELOG de la imagen que corre de verdad, no el de este repositorio."""
    comando = f"docker exec app-{destino.servicio} cat /app/CHANGELOG.md\n"
    codigo, salida, _ = _remoto(destino, comando)
    return salida if codigo == 0 else ""


def _coolify_version(destino: _Destino, version: str) -> bool:
    """Guarda APP_VERSION en el service. Solo la guarda: no despliega."""
    cuerpo = {"key": "APP_VERSION", "value": version, "is_preview": False}
    ruta = f"/services/{destino.servicio}/envs"
    estado, _ = _api_coolify(destino.args, "PATCH", ruta, cuerpo)
    if estado == 404:  # la variable aún no existía
        estado, _ = _api_coolify(destino.args, "POST", ruta, cuerpo)
    return estado in (200, 201)


def _coolify_desplegar(destino: _Destino) -> tuple:
    """Levanta el service con la versión guardada; (ok, detalle).

    Con `/deploy` y NO con `restart`: `restart` para el service entero antes
    de arrancarlo y, si algo falla a medias, se queda sin ningún contenedor
    (pasó con la 0.3.0). `/deploy` hace `up --force-recreate` sin parar antes.
    Ojo: Coolify contesta 200 aunque el token no tenga permiso de desplegar; el
    motivo va en el mensaje.
    """
    ruta = f"/deploy?uuid={destino.servicio}"
    estado, cuerpo = _api_coolify(destino.args, "POST", ruta, {})
    if estado == 405:  # Coolify anterior a la 4.2: solo por GET
        estado, cuerpo = _api_coolify(destino.args, "GET", ruta)
    despliegues = cuerpo.get("deployments") if isinstance(cuerpo, dict) else None
    mensajes = [str(d.get("message", "")) for d in despliegues or []]
    if estado != 200 or not mensajes:
        return False, f"Coolify respondió {estado}: {str(cuerpo)[:200]}"
    if any("unauthorized" in m.lower() for m in mensajes):
        return False, "el token de Coolify no tiene permiso de deploy"
    return True, mensajes[0]


def _buscar_servicio(args, dominio: str) -> list | None:
    """Los services de Coolify que sirven ese dominio (lo normal: uno).

    None si Coolify ni siquiera deja listar (ya dicho con `falla`): un 401 no es
    «no encuentro el service», y decirlo así manda a buscar en otro sitio.
    """
    estado, servicios = _api_coolify(args, "GET", "/services")
    if estado != 200 or not isinstance(servicios, list):
        falla(f"Coolify no me deja listar los services (HTTP {estado})",
              _pista_coolify(args, estado))
        return None
    encontrados = []
    for servicio in servicios:
        uuid = str(servicio.get("uuid", ""))
        estado, detalle = _api_coolify(args, "GET", f"/services/{uuid}")
        if estado != 200 or not isinstance(detalle, dict):
            continue
        for app in detalle.get("applications") or []:
            for url in str(app.get("fqdn") or "").split(","):
                if urllib.parse.urlparse(url.strip()).hostname == dominio:
                    encontrados.append(uuid)
    return sorted(set(encontrados))


def _servicio_de(args, dominio: str):
    """El uuid del service: `--service`, el índice o, si no, se busca en Coolify
    por el dominio. Lo que se encuentra se apunta para la próxima vez."""
    servicio = getattr(args, "service", None) or _indice().get(dominio, {}).get("service")
    if not servicio:
        print(f"Buscando en tu Coolify el service de {dominio}…")
        encontrados = _buscar_servicio(args, dominio)
        if encontrados is None:
            return None
        if len(encontrados) != 1:
            falla(
                f"no encuentro un único service con el dominio {dominio}"
                + (f" (hay {len(encontrados)})" if encontrados else ""),
                "pásalo con `--service <uuid>`; está en la URL del service en Coolify",
            )
            return None
        servicio = encontrados[0]
    if not _RE_UUID.match(servicio):
        falla(f"«{servicio}» no parece un uuid de Coolify",
              "cópialo de la URL del service en Coolify")
        return None
    _registrar(dominio, service=servicio)
    return servicio


def _destino_de(args, dominio: str):
    """Dónde está la instalación: el servidor por SSH y el service en Coolify."""
    if not _RE_DOMINIO.match(dominio):
        falla(f"«{dominio}» no parece un dominio", "usa `--instance crm.tuclinica.mx`")
        return None
    ip = (getattr(args, "ip", None) or _leer_env(dominio).get("SERVER_IP", "")
          or _indice().get(dominio, {}).get("ip", ""))
    if not ip:
        falla("no sé la IP del servidor",
              "pásala con `--ip <ip>`; se apunta para la próxima vez")
        return None
    servicio = _servicio_de(args, dominio)
    if not servicio:
        return None
    # Aquí y no al cambiar la versión: un token que no vale se descubre antes
    # de la copia, no con media actualización hecha.
    estado, _ = _api_coolify(args, "GET", f"/services/{servicio}")
    if estado != 200:
        falla(f"Coolify no me deja ver el service {servicio} (HTTP {estado})",
              _pista_coolify(args, estado))
        return None
    _registrar(dominio, ip=ip)
    return _Destino(dominio, ip, servicio, args)


def _pendientes(contenedores: dict, version: str) -> list:
    """Lo que falta para dar la versión por arrancada, dicho contenedor a contenedor."""
    faltan = []
    for nombre in CON_VERSION:
        c = contenedores.get(nombre)
        if not c:
            faltan.append(f"{nombre}: no está")
        elif _tag(c["imagen"]) != version:
            faltan.append(f"{nombre}: aún en la {_tag(c['imagen'])}")
        elif c["estado"] != "running":
            faltan.append(f"{nombre}: {c['estado']}")
        elif c["salud"] and c["salud"] != "healthy":
            faltan.append(f"{nombre}: {c['salud']}")
    return faltan


def _esperar_version(destino: _Destino, version: str) -> tuple:
    """Espera a que app, worker, beat y el panel corran la versión y estén sanos.

    Mirar `/health` no basta: justo después de desplegar responde el
    contenedor VIEJO, y daría por buena una actualización que ni ha empezado.
    """
    timeout = getattr(destino.args, "timeout", 600) or 600
    limite = time.time() + timeout
    ultimo = ""
    print(f"Esperando a que arranque la {version} (hasta {timeout // 60} min)…")
    while time.time() < limite:
        pendientes = _pendientes(_srv_contenedores(destino) or {}, version)
        if not pendientes:
            ok(f"app, worker, beat y el panel corren la {version} y están sanos")
            return True, ""
        resumen = ", ".join(pendientes)
        if resumen != ultimo:
            print(_color(f"  {resumen}", GRIS))
            ultimo = resumen
        time.sleep(10)
    return False, ultimo or "no terminó de arrancar"


def _args_http(destino: _Destino, timeout: int = 180) -> argparse.Namespace:
    """Lo que necesitan `wait` y `verify` para hablar con la instalación."""
    return argparse.Namespace(
        instance=destino.dominio, http=getattr(destino.args, "http", False),
        ip=getattr(destino.args, "ip", None), timeout=timeout,
    )


def _comprobar_version_viva(destino: _Destino, version: str) -> tuple:
    """Por el dominio, como entra la gente, y con el CHANGELOG de la imagen que
    arrancó: APP_VERSION puede decir una cosa y la imagen ser otra (el engaño
    de `latest`)."""
    if cmd_wait(_args_http(destino)) != 0:
        return False, "el dominio no responde", ""
    changelog = _srv_changelog(destino)
    bloques = _bloques_del_changelog(changelog)
    corriendo = bloques[0][0] if bloques else "?"
    if corriendo != version:
        return False, f"la imagen que arrancó dice ser la {corriendo}", changelog
    return True, "", changelog


def _comparar_recuento(antes: dict, despues: dict) -> bool:
    """Avisa de cualquier tabla que tenga menos filas que antes."""
    intactos = True
    for tabla, etiqueta in TABLAS_QUE_SE_CUENTAN:
        if tabla in antes and despues.get(tabla, -1) < antes[tabla]:
            intactos = False
            aviso(f"{etiqueta}: había {antes[tabla]} y ahora "
                  f"{despues.get(tabla, 'no se pueden contar')}")
    if intactos and despues:
        ok("datos intactos: " + ", ".join(
            f"{despues[t]} {e}" for t, e in TABLAS_QUE_SE_CUENTAN if t in despues))
    return intactos


def _pista_de_pull(detalle: str) -> str:
    texto = detalle.lower()
    if "manifest unknown" in texto or "not found" in texto:
        return ("esa versión no está publicada en GHCR: revisa el número, o espera a "
                "que termine la publicación de imágenes")
    if "denied" in texto or "unauthorized" in texto:
        return ("el servidor no tiene permiso para bajar las imágenes: "
                "corre `omni.py login-registry`")
    return "revisa el detalle de arriba; no se ha tocado nada"


def _preparar_update(args):
    """Todo lo que se puede comprobar sin tocar nada.

    Devuelve (destino, contenedores, versión actual, versión nueva) o None.
    """
    dominio = _dominio_de(args)
    nueva = (getattr(args, "version", None) or _version_del_repo() or "").strip().lstrip("v")
    if not _version_tupla(nueva):
        falla(
            f"«{nueva or '(ninguna)'}» no es una versión",
            "usa la versión exacta, p. ej. `--version 0.11.0`. Con `latest` Docker "
            "reutiliza la imagen vieja y el despliegue sale en verde con el código de antes",
        )
        return None
    destino = _destino_de(args, dominio)
    if destino is None:
        return None
    contenedores = _srv_contenedores(destino)
    if contenedores is None:
        falla(f"no puedo entrar por SSH a {destino.ip}",
              f"prueba `ssh {getattr(args, 'user', None) or 'root'}@{destino.ip}`; "
              "si pide contraseña, tu llave no está en el servidor (`ssh-copy-id`)")
        return None
    if "app" not in contenedores:
        falla(f"no encuentro el contenedor `app` del service {destino.servicio}",
              "¿es el uuid correcto? Míralo en la URL del service en Coolify")
        return None
    return destino, contenedores, _tag(contenedores["app"]["imagen"]), nueva


def _respaldar(destino: _Destino, actual: str, nueva: str):
    """La copia de la base antes de tocar nada. Devuelve el registro de la
    actualización (lo que `rollback` necesita para deshacerla) o None."""
    revision = _srv_revision(destino)
    if revision is None:
        falla("no puedo leer la revisión de la base",
              "¿está en marcha el contenedor `db`? Míralo en Coolify")
        return None
    recuento = _srv_recuento(destino)
    print(f"Copia de la base antes de tocar nada (revisión {revision})…")
    codigo, salida, errores = _srv_respaldar(destino, actual)
    if codigo != 0:
        if "SIN_ESPACIO" in errores:
            falla("no hay espacio en el servidor para la copia y la versión nueva",
                  f"libera disco en el VPS ({errores.strip().split('SIN_ESPACIO', 1)[1].strip()}); "
                  "no se ha tocado nada")
        else:
            falla("no se pudo hacer la copia de la base",
                  (errores.strip()[-300:] or "sin detalle") + " · no se ha tocado nada")
        return None
    ruta, _, tamano = salida.strip().splitlines()[-1].partition("|")
    mb = int(tamano) / 1048576 if tamano.strip().isdigit() else 0
    ok(f"copia hecha: {ruta} ({mb:.1f} MB)")
    return {
        "desde": actual, "hacia": nueva, "fecha": time.strftime("%Y-%m-%d %H:%M"),
        "respaldo": ruta, "revision": revision, "recuento": recuento,
        "ip": destino.ip, "service": destino.servicio, "resultado": "en curso",
    }


def _cambiar_version(destino: _Destino, nueva: str, anterior: str) -> bool:
    """APP_VERSION y despliegue. Si Coolify no despliega, la variable vuelve a la
    anterior: que un redespliegue a mano más tarde no suba la nueva a ciegas."""
    if not _coolify_version(destino, nueva):
        falla("Coolify no aceptó el cambio de APP_VERSION",
              "el token de Coolify necesita permisos read, write y deploy; no se ha tocado nada")
        return False
    desplegado, detalle = _coolify_desplegar(destino)
    if not desplegado:
        _coolify_version(destino, anterior)
        falla(f"Coolify no desplegó: {detalle}",
              "el token necesita permisos read, write y deploy; la instalación sigue "
              f"en la {anterior}")
        return False
    ok(f"APP_VERSION = {nueva}; Coolify está desplegando")
    return True


def _redesplegar(destino: _Destino, version: str) -> int:
    """Vuelve a levantar el service en una versión que ya corrió antes."""
    if not _coolify_version(destino, version):
        falla("Coolify no aceptó APP_VERSION",
              f"ponla a mano en {version} en Coolify y redespliega")
        return 1
    desplegado, detalle = _coolify_desplegar(destino)
    if not desplegado:
        falla(f"Coolify no desplegó: {detalle}", "redespliega el service a mano desde Coolify")
        return 1
    arrancada, detalle = _esperar_version(destino, version)
    if not arrancada:
        falla(f"la {version} tampoco arranca ({detalle})",
              "mira los registros del contenedor `app` en Coolify")
        return 1
    ok(f"de vuelta en la {version}")
    return 0


def _restaurar_y_volver(destino: _Destino, registro: dict) -> int:
    print("Parando app, worker y beat para que nadie escriba mientras se restaura…")
    if not _srv_parar(destino):
        falla("no se pudieron parar app, worker y beat",
              "páralos desde Coolify y repite `omni.py rollback`")
        return 1
    print(f"Restaurando {registro['respaldo']}…")
    codigo, _, errores = _srv_restaurar(destino, registro["respaldo"])
    if codigo != 0:
        motivo = errores.strip()[-300:] or "sin detalle"
        if "CONEXIONES" in errores:
            motivo = "algo sigue conectado a la base; para lo que quede desde Coolify"
        falla(f"la restauración no se hizo y la base quedó como estaba ({motivo})",
              "app, worker y beat siguen parados; arregla lo de arriba y repite `omni.py rollback`")
        return 1
    revision = _srv_revision(destino)
    if revision != registro["revision"]:
        falla(f"tras restaurar, la base dice {revision} y esperaba {registro['revision']}",
              "no levanto nada sobre una base que no reconozco; revisa la copia")
        return 1
    ok(f"base restaurada (revisión {revision})")
    codigo = _redesplegar(destino, registro["desde"])
    if codigo == 0:
        _comparar_recuento(registro["recuento"], _srv_recuento(destino))
    return codigo


def _volver(destino: _Destino, registro: dict, interactivo: bool) -> int:
    """Deja la instalación en la versión anterior.

    Si la base no cambió (la migración no llegó a aplicarse, o Postgres la
    revirtió entera), basta la imagen anterior. Si cambió, la anterior no
    arrancaría: su `alembic upgrade head` no reconoce la revisión nueva y el
    contenedor se queda reiniciando. Entonces hay que restaurar la copia, y eso
    borra lo que entró después: solo con confirmación escrita.
    """
    anterior = registro["desde"]
    revision = _srv_revision(destino)
    if revision is None:
        falla("no puedo leer la revisión de la base",
              f"mira el contenedor `db` en Coolify; la copia está en {registro['respaldo']}")
        return 1
    if revision == registro["revision"]:
        ok(f"la base no cambió ({revision}): vuelvo a la {anterior} sin tocar datos")
        return _anotar_vuelta(destino, registro, _redesplegar(destino, anterior))
    aviso(f"la {registro['hacia']} ya cambió la base ({registro['revision']} → {revision}) "
          f"y la {anterior} no arranca sobre ella")
    print(f"Para volver hay que restaurar la copia del {registro['fecha']}: se pierde lo "
          "que haya entrado desde entonces.")
    if not interactivo:
        falla("para restaurar hace falta que lo confirmes tú",
              f"en tu terminal: `python3 scripts/omni.py rollback --instance {destino.dominio}`. "
              "Hasta entonces la instalación sigue caída")
        return _anotar_vuelta(destino, registro, 1, "falta rollback")
    if input("Escribe RESTAURAR para continuar:\n> ").strip() != "RESTAURAR":
        print("Cancelado; no se ha tocado nada.")
        return _anotar_vuelta(destino, registro, 1, "falta rollback")
    return _anotar_vuelta(destino, registro, _restaurar_y_volver(destino, registro))


def _anotar_vuelta(destino: _Destino, registro: dict, codigo: int, resultado: str = "") -> int:
    registro["resultado"] = resultado or ("vuelta atrás" if codigo == 0 else "fallo al volver")
    campos = {"ultima_actualizacion": registro}
    if codigo == 0:
        campos["version"] = registro["desde"]
    _registrar(destino.dominio, **campos)
    return codigo


def _cerrar_update(destino: _Destino, registro: dict, changelog: str) -> None:
    """Lo que se hace con la versión nueva ya en pie: contar, contar qué trae y verificar."""
    _comparar_recuento(registro["recuento"], _srv_recuento(destino))
    novedades = _novedades_entre(changelog, registro["desde"], registro["hacia"])
    if novedades:
        lineas = "\n\n".join(novedades).splitlines()
        print(f"\n— Qué trae la {registro['hacia']} —")
        print("\n".join(lineas[:40]))
        if len(lineas) > 40:
            print(_color("  (sigue en el panel: Plataforma → Cambios)", GRIS))
    registro["resultado"] = "ok"
    _registrar(destino.dominio, version=registro["hacia"], ultima_actualizacion=registro)
    print()
    ok(f"{destino.dominio} actualizada a la {registro['hacia']}")
    datos = _leer_env(destino.dominio)
    if not datos.get("INITIAL_ADMIN_EMAIL") or not datos.get("INITIAL_ADMIN_PASSWORD"):
        # En la instalación en un clic el administrador se creó en el panel y
        # aquí no están ni su correo ni su contraseña. verify fallaría por eso,
        # no por la versión nueva, y el aviso de abajo mandaría a hacer rollback.
        aviso("no hago la comprobación completa (verify): aquí no está tu usuario del panel")
        print(_color("  Lo de arriba ya comprueba que la versión nueva arrancó, responde por tu "
                     "dominio y conserva los datos. Para la completa, "
                     + _guarda_el_usuario_del_panel(destino.dominio), GRIS))
        return
    print("\nComprobación completa de la instalación (verify):")
    if cmd_verify(_args_http(destino)) != 0:
        aviso(f"la {registro['hacia']} arrancó bien, pero verify encontró fallos (arriba). "
              "Si son de la versión nueva, en tu terminal: `omni.py rollback`")


def cmd_update(args) -> int:
    """Sube a otra versión, con copia antes y vuelta atrás si falla. Con `--all`,
    todas las instalaciones apuntadas en esta computadora, una detrás de otra."""
    if getattr(args, "todas", False):
        return _update_en_serie(args)
    return _update_una(args)


def _update_en_serie(args) -> int:
    """`update --all`: una instalación detrás de otra, nunca a la vez.

    Una migración rota en dos clínicas a la vez es el peor escenario para una
    agencia. En serie, en cuanto una falla (y se intenta volver atrás) las
    siguientes ni se tocan.
    """
    sueltas = [f"--{n}" for n in ("instance", "service", "ip") if getattr(args, n, None)]
    if sueltas:
        falla(f"`--all` no se combina con {', '.join(sueltas)}",
              "cada instalación usa lo que tiene apuntado; para una sola, quita `--all`")
        return 1
    dominios = sorted(d for d in _indice() if d != RUTA_GLOBAL)
    if not dominios:
        falla("no hay ninguna instalación apuntada",
              f"`--all` recorre las de {INDICE}; para una sola, `--instance <dominio>`")
        return 1
    for n, dominio in enumerate(dominios, 1):
        print(_color(f"\n══ {dominio} ({n} de {len(dominios)}) ══", AMARILLO))
        una = argparse.Namespace(**{**vars(args), "instance": dominio, "todas": False})
        if _update_una(una) != 0:
            sin_tocar = dominios[n:]
            falla(f"me detengo en {dominio}",
                  "revisa lo de arriba antes de seguir"
                  + (f"; sin tocar: {', '.join(sin_tocar)}" if sin_tocar else ""))
            return 1
    print()
    ok(f"las {len(dominios)} instalaciones están en la misma versión")
    return 0


def _update_una(args) -> int:
    """Sube la instalación a otra versión, con respaldo antes y vuelta atrás si falla.

    El orden es lo importante. Todo lo que puede fallar sin consecuencias —la
    versión pedida, Coolify, SSH, que las imágenes existan, el espacio para la
    copia— se comprueba ANTES de tocar nada: redesplegar con una versión que
    aún no estaba en GHCR dejó una instalación sin ningún contenedor (0.3.0).
    La copia se hace fuera de la app, con `pg_dump` en el servidor: una copia
    que depende de la versión que falla no sirve para volver de ella.
    """
    preparado = _preparar_update(args)
    if preparado is None:
        return 1
    destino, contenedores, actual, nueva = preparado
    previa = _indice().get(destino.dominio, {}).get("ultima_actualizacion") or {}
    a_medias = previa.get("resultado") in ("falta rollback", "fallo al volver")
    if a_medias and actual != previa["desde"]:
        # Encima de una actualización a medias, la copia nueva sería la de una
        # base rota, y el `rollback` pendiente perdería la buena.
        falla(f"la actualización del {previa['fecha']} a la {previa['hacia']} quedó a medias",
              "primero deshazla en tu terminal: `python3 scripts/omni.py rollback`")
        return 1
    print(f"{destino.dominio}: de la {actual} a la {nueva}")
    comparacion = _compara_versiones(actual, nueva)
    if comparacion == 0:
        ok(f"ya está en la {nueva}; no hay nada que hacer")
        return 0
    if comparacion == 1:
        falla(f"la {nueva} es anterior a la {actual}",
              "bajar de versión no se hace con update: si la actual ya cambió la base, "
              "la anterior no arranca. Para deshacer la última actualización: `omni.py rollback`")
        return 1
    if comparacion is None:
        aviso(f"la instalación corre «{actual}», que no es una versión: no puedo comprobar "
              f"que la {nueva} sea posterior")

    imagenes = sorted({_con_tag(c["imagen"], nueva)
                       for n, c in contenedores.items() if n in CON_VERSION})
    print(f"Bajando las imágenes de la {nueva} al servidor (aún no se toca nada)…")
    bajadas, detalle = _srv_bajar(destino, imagenes)
    if not bajadas:
        falla(f"el servidor no pudo bajar la {nueva}: {detalle}", _pista_de_pull(detalle))
        return 1
    ok(f"el servidor ya tiene las imágenes de la {nueva}")

    registro = _respaldar(destino, actual, nueva)
    if registro is None:
        return 1
    _registrar(destino.dominio, ultima_actualizacion=registro)
    if not _cambiar_version(destino, nueva, actual):
        registro["resultado"] = "no se tocó"
        _registrar(destino.dominio, ultima_actualizacion=registro)
        return 1

    arrancada, detalle = _esperar_version(destino, nueva)
    changelog = ""
    if arrancada:
        arrancada, detalle, changelog = _comprobar_version_viva(destino, nueva)
    if not arrancada:
        falla(f"la {nueva} no arrancó bien ({detalle})",
              "no hagas nada todavía: vuelvo a la anterior ahora mismo")
        _volver(destino, registro, interactivo=sys.stdin.isatty())
        return 1
    _cerrar_update(destino, registro, changelog)
    return 0


def cmd_rollback(args) -> int:
    """Deshace la última actualización. Solo en tu terminal: puede restaurar la copia."""
    if not sys.stdin.isatty():
        falla("esto no se corre desde el chat",
              "ábrelo en tu terminal: puede restaurar la copia de la base y pide "
              "confirmación escrita")
        return 1
    dominio = _dominio_de(args)
    registro = _indice().get(dominio, {}).get("ultima_actualizacion")
    if not registro:
        falla(f"no hay ninguna actualización de {dominio} apuntada en {INDICE}",
              "rollback solo deshace lo que hizo `omni.py update` desde esta computadora")
        return 1
    ip = getattr(args, "ip", None) or registro.get("ip", "")
    destino = _Destino(dominio, ip, registro["service"], args)
    print(f"Última actualización: de la {registro['desde']} a la {registro['hacia']} "
          f"({registro['fecha']}, {registro['resultado']})")
    print(f"Copia: {registro['respaldo']} (revisión {registro['revision']})")
    contenedores = _srv_contenedores(destino)
    if contenedores is None:
        falla(f"no puedo entrar por SSH a {ip}", "prueba `ssh root@<ip>` y pasa `--ip` si cambió")
        return 1
    if ("app" in contenedores and _tag(contenedores["app"]["imagen"]) == registro["desde"]
            and _srv_revision(destino) == registro["revision"]):
        ok(f"ya está en la {registro['desde']}; no hay nada que deshacer")
        return 0
    return _volver(destino, registro, interactivo=True)


# ---------------------------------------------------------------------------
# Estado y desinstalación
# ---------------------------------------------------------------------------

def cmd_urls(args) -> int:
    dominio = _dominio_de(args)
    datos = _leer_env(dominio)
    base = f"https://{dominio}"
    print(f"Panel:    {base}")
    print(f"Demo:     {base}/demo")
    print(f"Salud:    {base}/health")
    print(f"Usuario:  {datos.get('INITIAL_ADMIN_EMAIL', '(sin definir)')}")
    print("Contraseña: la tuya del panel (aquí no se imprime nunca)")
    return 0


def cmd_status(args) -> int:
    dominio = _dominio_de(args)
    base = f"https://{dominio}" if not args.http else f"http://{dominio}"
    with _dns_a_mano(dominio, _ip_forzada(args, dominio)):
        estado, cuerpo = _get(f"{base}/health")
    if estado != 200:
        falla(f"{dominio} no responde ({estado})",
              _diagnostico_http(estado, cuerpo, dominio) or "mira Coolify")
        return 1
    salud = json.loads(cuerpo)
    for parte in ("db", "redis", "worker"):
        valor = salud.get(parte, "?")
        (ok if valor == "ok" else aviso)(f"{parte}: {valor}")
    datos = _leer_env(dominio)
    print(f"\nVersión desplegada: {datos.get('APP_VERSION', '(la que tenga Coolify)')}")
    print(f"Perfil sembrado:    {datos.get('SEED_PROFILE', 'dental-mx')}")
    return 0


def cmd_uninstall(args) -> int:
    """Borra la instalación. Dos confirmaciones, porque no hay vuelta atrás."""
    if not sys.stdin.isatty():
        falla("esto no se corre desde el chat",
              "ábrelo en tu terminal: borra datos de verdad")
        return 1
    dominio = _dominio_de(args)
    print(_color(f"Vas a borrar la instalación de {dominio}.", ROJO))
    print("Se pierden las conversaciones, los contactos y la base de conocimiento.")
    print("Las copias de seguridad que estén FUERA del servidor no se tocan.")
    print(f"\nEscribe el dominio completo para confirmar ({dominio}):")
    if input("> ").strip() != dominio:
        print("Cancelado.")
        return 1
    print("Escribe BORRAR para confirmar por segunda vez:")
    if input("> ").strip() != "BORRAR":
        print("Cancelado.")
        return 1
    print("\nEsto NO borra nada en Coolify: bórralo tú desde su panel "
          "(proyecto → service → Delete), que pide su propia confirmación.")
    ruta = _ruta_env(dominio)
    if ruta.is_file():
        ruta.unlink()
        ok(f"borrado {ruta}")
    idx = _indice()
    idx.pop(dominio, None)
    _guardar_indice(idx)
    ok("instalación quitada del índice local")
    return 0


# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    _salida_en_utf8()
    p = argparse.ArgumentParser(
        prog="omni.py",
        description="Instalador del CRM Omnicanal. Los comandos que piden llaves van en "
                    "tu segunda terminal; el resto los puede correr el agente.",
    )
    p.add_argument("--instance", help="dominio de la instalación (si tienes varias)")
    p.add_argument("--http", action="store_true",
                   help="hablar por http en vez de https (staging sin certificado)")

    # Las mismas dos opciones, admitidas TAMBIÉN después del subcomando: nadie
    # se acuerda de si van antes o después, y fallar por eso es una tontería.
    # SUPPRESS es lo que evita que, al no escribirlas aquí, machaquen con None
    # el valor que sí se puso delante.
    comunes = argparse.ArgumentParser(add_help=False)
    comunes.add_argument("--instance", default=argparse.SUPPRESS,
                         help="dominio de la instalación (si tienes varias)")
    comunes.add_argument("--http", action="store_true", default=argparse.SUPPRESS,
                         help="hablar por http en vez de https")

    sub = p.add_subparsers(dest="comando", required=True)

    d = sub.add_parser("doctor", parents=[comunes], help="comprueba tu ordenador, el DNS y el servidor")
    d.add_argument("--ip", help="IP del servidor")
    d.add_argument("--user", default="root", help="usuario de SSH (por defecto root)")
    d.set_defaults(func=cmd_doctor)

    s = sub.add_parser("secrets", parents=[comunes], help="genera las llaves que falten (nunca rota)")
    s.add_argument("--check", action="store_true",
                   help="solo dice cuáles están; no imprime valores (se puede usar en el chat)")
    s.add_argument("--ip", help="IP del servidor, para guardarla")
    s.add_argument("--email", help="correo del administrador inicial")
    s.set_defaults(func=cmd_secrets)

    se = sub.add_parser("secret", parents=[comunes], help="guarda o lista una llave concreta")
    se.add_argument("accion", choices=["set", "list"])
    se.add_argument("nombre", nargs="?", default="", help="por ejemplo OPENAI_API_KEY")
    se.set_defaults(func=cmd_secret)

    sk = sub.add_parser("save-key", parents=[comunes], help="enseña la llave y la contraseña una sola vez")
    sk.set_defaults(func=cmd_save_key)

    m = sub.add_parser("mcp-setup", parents=[comunes], help="registra el MCP de Coolify en Claude Code")
    m.set_defaults(func=cmd_mcp_setup)

    lr = sub.add_parser("login-registry", parents=[comunes], help="deja al servidor bajar las imágenes privadas")
    lr.add_argument("--ip", help="IP del servidor")
    lr.add_argument("--user", default="root", help="usuario de SSH")
    lr.add_argument("--github-user", help="tu usuario de GitHub")
    lr.set_defaults(func=cmd_login_registry)

    pe = sub.add_parser("push-env", parents=[comunes], help="sube las llaves a Coolify sin enseñarlas")
    pe.add_argument("--service", required=True, help="uuid del service en Coolify")
    pe.add_argument("--coolify-url", help="URL de Coolify (o COOLIFY_BASE_URL)")
    pe.set_defaults(func=cmd_push_env)

    w = sub.add_parser("wait", parents=[comunes], help="espera a que la instalación responda")
    w.add_argument("--timeout", type=int, default=600, help="segundos (por defecto 600)")
    w.add_argument("--ip", help="hablar con esta IP sin esperar a que el DNS llegue aquí")
    w.set_defaults(func=cmd_wait)

    v = sub.add_parser("verify", parents=[comunes], help="comprueba que la instalación sirve de verdad")
    v.add_argument("--ip", help="hablar con esta IP sin esperar a que el DNS llegue aquí")
    v.set_defaults(func=cmd_verify)

    c = sub.add_parser("connect", parents=[comunes], help="conecta la IA, el chat web, WhatsApp, el correo, Instagram/Messenger o la voz")
    c.add_argument("que", choices=["ai", "widget", "whatsapp", "email", "social", "voice"])
    c.add_argument("--correo", help="dirección del buzón (recepcion@clinica.mx)")
    c.add_argument(
        "--proveedor",
        choices=["gmail", "hostinger", "cpanel", "zoho", "zoho_pro"],
        help="dónde está el buzón (para Otro, conéctalo desde el panel)",
    )
    c.add_argument("--envia-sola", action="store_true",
                   help="Sofía responde sin dejar borrador (por defecto deja borradores)")
    c.add_argument("--dominios", help="dominios permitidos del widget, separados por comas")
    c.add_argument("--numero", help="número de WhatsApp dado de alta en YCloud (+52…); en "
                   "`voice`, el que ya está en Retell o, con --twilio, el de Twilio")
    c.add_argument("--instagram", help="id de la cuenta de Instagram en Zernio (si hay varias)")
    c.add_argument("--facebook", help="id de la página de Facebook en Zernio (si hay varias)")
    c.add_argument("--wa-por-zernio", action="store_true",
                   help="manda también WhatsApp por Zernio en vez de por YCloud")
    c.add_argument("--voz", help="voz de Retell para el agente (por defecto retell-Andrea)")
    c.add_argument("--comprar-numero", action="store_true",
                   help="compra un número de EE. UU. en Retell (se cobra cada mes)")
    c.add_argument("--lada", type=int, help="lada de EE. UU. del número a comprar (ej. 415)")
    c.add_argument("--twilio", action="store_true",
                   help="conecta por troncal SIP el número de Twilio de --numero (México)")
    c.add_argument("--troncal", help="prefijo de la troncal de Twilio (por defecto, tu dominio)")
    c.add_argument("--transferir-a", help="teléfono de recepción para pasar llamadas (+52…)")
    c.add_argument("--esperar", type=int, default=300,
                   help="segundos esperando el mensaje de prueba (0 = no esperar; por defecto 300)")
    c.set_defaults(func=cmd_connect)

    up = sub.add_parser("update", parents=[comunes],
                        help="sube a una versión nueva, con copia previa y vuelta atrás si falla")
    up.add_argument("--version",
                    help="versión a instalar (por defecto, la del CHANGELOG de este repositorio)")
    up.add_argument("--service",
                    help="uuid del service en Coolify (si no, se busca por el dominio)")
    up.add_argument("--all", dest="todas", action="store_true",
                    help="todas las instalaciones apuntadas, una detrás de otra")
    up.add_argument("--ip", help="IP del servidor (si no, la que guardaste)")
    up.add_argument("--user", default="root", help="usuario de SSH (por defecto root)")
    up.add_argument("--coolify-url", help="URL de Coolify (o COOLIFY_BASE_URL)")
    up.add_argument("--timeout", type=int, default=600,
                    help="segundos esperando a que arranque (por defecto 600)")
    up.set_defaults(func=cmd_update)

    rb = sub.add_parser(
        "rollback", parents=[comunes],
        help="deshace la última actualización (en tu terminal: puede restaurar la copia)")
    rb.add_argument("--ip", help="IP del servidor (si no, la de la última actualización)")
    rb.add_argument("--user", default="root", help="usuario de SSH (por defecto root)")
    rb.add_argument("--coolify-url", help="URL de Coolify (o COOLIFY_BASE_URL)")
    rb.add_argument("--timeout", type=int, default=600,
                    help="segundos esperando a que arranque (por defecto 600)")
    rb.set_defaults(func=cmd_rollback)

    u = sub.add_parser("urls", parents=[comunes], help="enseña las direcciones de la instalación")
    u.set_defaults(func=cmd_urls)

    st = sub.add_parser("status", parents=[comunes], help="salud de la instalación")
    st.add_argument("--ip", help="hablar con esta IP sin esperar a que el DNS llegue aquí")
    st.set_defaults(func=cmd_status)

    un = sub.add_parser("uninstall", parents=[comunes], help="quita la instalación de tu ordenador")
    un.set_defaults(func=cmd_uninstall)

    args = p.parse_args(argv)
    try:
        _traer_llaves_de_antes()
    except OSError as e:
        # Que un permiso raro en la carpeta vieja no deje sin instalador.
        aviso(f"no pude copiar las llaves de {DIR_ANTIGUO} a {DIR_INSTALACION}: {e}")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

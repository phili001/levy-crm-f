"""Pruebas del instalador. `python3 scripts/test_omni.py`, sin dependencias.

Van con unittest de la biblioteca estándar y no con pytest a propósito: esto
tiene que poder correrse en el ordenador de quien instala, que no monta ningún
entorno de Python.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# En Windows no hay permisos 600: lo que protege las llaves es la carpeta del
# usuario, que ya es privada. Esas dos comprobaciones solo valen en Mac y Linux.
SIN_PERMISOS_POSIX = "Windows no tiene permisos POSIX"

_spec = importlib.util.spec_from_file_location("omni", Path(__file__).parent / "omni.py")
omni = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(omni)
# Lo que hace main() antes que nada. Aquí los comandos se llaman directamente, y
# en la CI de Windows la salida va en cp1252: el primer ✓ tumbaba la prueba.
omni._salida_en_utf8()


class _Args:
    def __init__(self, **kw):
        self.instance = kw.pop("instance", None)
        self.check = kw.pop("check", False)
        self.ip = kw.pop("ip", None)
        self.email = kw.pop("email", None)
        for k, v in kw.items():
            setattr(self, k, v)


class DirectorioTemporal(unittest.TestCase):
    """Cada prueba trabaja sobre su propia carpeta de llaves, no sobre la real
    (ni sobre la vieja de junto al script, que en tu máquina tiene llaves de verdad)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir_previo, self.indice_previo = omni.DIR_INSTALACION, omni.INDICE
        self.antiguo_previo = omni.DIR_ANTIGUO
        omni.DIR_INSTALACION = Path(self.tmp.name) / ".omnicanal"
        omni.INDICE = omni.DIR_INSTALACION / "instances.json"
        omni.DIR_ANTIGUO = Path(self.tmp.name) / "repo" / ".instalacion"

    def tearDown(self) -> None:
        omni.DIR_INSTALACION, omni.INDICE = self.dir_previo, self.indice_previo
        omni.DIR_ANTIGUO = self.antiguo_previo
        self.tmp.cleanup()


class LasLlavesNoSeRotan(DirectorioTemporal):
    def test_la_segunda_corrida_no_cambia_ninguna_llave(self):
        """Rotar una ENCRYPTION_KEY ya usada deja ilegible lo cifrado."""
        omni.cmd_secrets(_Args(instance="crm.ejemplo.mx"))
        primera = omni._leer_env("crm.ejemplo.mx")
        omni.cmd_secrets(_Args(instance="crm.ejemplo.mx"))
        segunda = omni._leer_env("crm.ejemplo.mx")
        for nombre in omni.SECRETOS:
            self.assertEqual(primera[nombre], segunda[nombre], nombre)

    def test_solo_rellena_lo_que_falta(self):
        omni.cmd_secrets(_Args(instance="crm.ejemplo.mx"))
        datos = omni._leer_env("crm.ejemplo.mx")
        del datos["JWT_SECRET"]
        omni._escribir_env("crm.ejemplo.mx", datos)
        anterior = datos["ENCRYPTION_KEY"]
        omni.cmd_secrets(_Args(instance="crm.ejemplo.mx"))
        nuevos = omni._leer_env("crm.ejemplo.mx")
        self.assertTrue(nuevos["JWT_SECRET"])
        self.assertEqual(anterior, nuevos["ENCRYPTION_KEY"])

    @unittest.skipIf(os.name == "nt", SIN_PERMISOS_POSIX)
    def test_el_archivo_de_llaves_no_lo_lee_nadie_mas(self):
        omni.cmd_secrets(_Args(instance="crm.ejemplo.mx"))
        modo = os.stat(omni._ruta_env("crm.ejemplo.mx")).st_mode & 0o777
        self.assertEqual(modo, 0o600, f"permisos {oct(modo)}")


class LasLlavesGeneradasValen(unittest.TestCase):
    def test_la_contrasena_del_admin_cumple_la_politica_del_panel(self):
        """Sin mezcla de tipos, el sembrado la rechaza y no hay administrador."""
        for _ in range(50):
            c = omni._contrasena_admin()
            self.assertGreaterEqual(len(c), 12)
            self.assertTrue(any(x.isdigit() for x in c))
            self.assertTrue(any(x.isalpha() for x in c))

    def test_la_clave_de_cifrado_es_fernet(self):
        from base64 import urlsafe_b64decode
        self.assertEqual(len(urlsafe_b64decode(omni._clave_fernet())), 32)

    def test_la_contrasena_de_postgres_no_lleva_barras(self):
        """Viaja dentro de una URL de conexión: un "/" la parte por la mitad."""
        for _ in range(20):
            valor = omni._generadores()["POSTGRES_PASSWORD"]()
            self.assertNotIn("/", valor)
            self.assertNotIn("+", valor)
            self.assertNotIn(":", valor)


class LaSalidaNoFiltraSecretos(DirectorioTemporal):
    def test_check_imprime_nombres_y_nunca_valores(self):
        """`secrets --check` es el único que el agente corre desde el chat."""
        omni.cmd_secrets(_Args(instance="crm.ejemplo.mx"))
        datos = omni._leer_env("crm.ejemplo.mx")
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            omni.cmd_secrets(_Args(instance="crm.ejemplo.mx", check=True))
        salida = buffer.getvalue()
        for nombre in omni.SECRETOS:
            self.assertIn(nombre, salida)
            self.assertNotIn(datos[nombre], salida)

    def test_list_tampoco_ensena_las_claves_de_los_servicios(self):
        """Antes `secret list` imprimía en claro la clave de OpenAI, la de YCloud
        y cualquier otra que no fuera de las cuatro de la instalación."""
        import io
        from contextlib import redirect_stdout

        omni.cmd_secrets(_Args(instance="crm.ejemplo.mx"))
        datos = omni._leer_env("crm.ejemplo.mx")
        datos.update({"OPENAI_API_KEY": "sk-muy-secreta", "EMAIL_APP_PASSWORD": "abcd efgh ijkl"})
        omni._escribir_env("crm.ejemplo.mx", datos)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            omni.cmd_secret(_Args(instance="crm.ejemplo.mx", accion="list", nombre=""))
        salida = buffer.getvalue()
        self.assertIn("OPENAI_API_KEY: guardado", salida)
        self.assertNotIn("sk-muy-secreta", salida)
        self.assertNotIn("abcd efgh ijkl", salida)


class ElDominioSeLimpia(unittest.TestCase):
    def test_quita_el_esquema_y_la_barra(self):
        for entrada in ("https://crm.ejemplo.mx", "http://crm.ejemplo.mx/",
                        "crm.ejemplo.mx/", "  CRM.Ejemplo.MX "):
            self.assertEqual(omni._normaliza_dominio(entrada), "crm.ejemplo.mx")


class ElDiagnosticoExplicaLoQueSePuedeArreglar(unittest.TestCase):
    def test_el_404_de_traefik_apunta_a_las_imagenes(self):
        """Con las imágenes públicas el login ya no es la causa de siempre: lo
        normal es una versión sin publicar. La privada se sigue diciendo."""
        pista = omni._diagnostico_http(404, "404 page not found", "crm.ejemplo.mx")
        self.assertIn("no pudo bajar las imágenes", pista)
        self.assertIn("no está publicada", pista)
        self.assertIn("login-registry", pista)

    def test_sin_respuesta_apunta_al_dns(self):
        self.assertIn("DNS", omni._diagnostico_http(0, "timeout", "crm.ejemplo.mx"))

    def test_el_certificado_no_se_confunde_con_el_dns(self):
        """En la prueba en frío mandaba a revisar un DNS que estaba perfecto."""
        pista = omni._diagnostico_http(0, "SSL: CERTIFICATE_VERIFY_FAILED", "crm.ejemplo.mx")
        self.assertIn("certificado", pista)
        self.assertNotIn("revisa el DNS", pista)

    def test_el_502_dice_que_aun_arranca(self):
        self.assertIn("arranca", omni._diagnostico_http(502, "", "crm.ejemplo.mx"))




class LosTokensTuyosNoSonDeUnaInstalacion(DirectorioTemporal):
    """GHCR y Coolify valen para todas: pedirlos por dominio obligaba a
    guardarlos ANTES de saber el dominio, y a repetirlos después."""

    def test_un_token_global_se_ve_desde_cualquier_instalacion(self):
        omni._escribir_env(omni.RUTA_GLOBAL, {"GHCR_TOKEN": "ghp_de_prueba"})
        omni.cmd_secrets(_Args(instance="crm.uno.mx"))
        omni.cmd_secrets(_Args(instance="crm.dos.mx"))
        for dominio in ("crm.uno.mx", "crm.dos.mx"):
            self.assertEqual(omni._leer_env(dominio)["GHCR_TOKEN"], "ghp_de_prueba")

    def test_lo_de_la_instalacion_manda_sobre_lo_global(self):
        omni._escribir_env(omni.RUTA_GLOBAL, {"GHCR_USER": "global"})
        omni._escribir_env("crm.uno.mx", {"GHCR_USER": "solo-aqui"})
        self.assertEqual(omni._leer_env("crm.uno.mx")["GHCR_USER"], "solo-aqui")


class SinElUsuarioDelPanelSeDiceClaro(DirectorioTemporal):
    """El fallo que costó veinte minutos: `verify` sin el archivo de la
    instalación decía "el administrador no puede entrar" y mandaba a revisar la
    contraseña. No había contraseña que revisar: el archivo no estaba. En la
    instalación en un clic es lo normal hasta que la persona guarda su usuario."""

    def test_sin_archivo_dice_donde_busca_y_como_guardar_el_usuario(self) -> None:
        import contextlib
        import io

        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            falta = omni._faltan_datos_de_instalacion("crm.ejemplo.mx", {})
        texto = salida.getvalue()
        self.assertTrue(falta)
        self.assertIn("aquí no hay datos de crm.ejemplo.mx", texto)
        # Lo importante es que diga DÓNDE busca y qué guardar, que es lo que desatasca.
        self.assertIn(str(omni.DIR_INSTALACION), texto)
        self.assertIn("secret set INITIAL_ADMIN_EMAIL --instance crm.ejemplo.mx", texto)
        self.assertNotIn("no vale", texto, "no se culpa a una contraseña que no existe")

    def test_con_archivo_a_medias_se_dice_que_falta_el_administrador(self) -> None:
        import contextlib
        import io

        omni._escribir_env("crm.ejemplo.mx", {"DOMINIO": "crm.ejemplo.mx"})
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            falta = omni._faltan_datos_de_instalacion(
                "crm.ejemplo.mx", omni._leer_env("crm.ejemplo.mx")
            )
        self.assertTrue(falta)
        self.assertIn("no tiene el administrador", salida.getvalue())

    def test_con_los_datos_puestos_no_estorba(self) -> None:
        import contextlib
        import io

        omni._escribir_env("crm.ejemplo.mx", {
            "DOMINIO": "crm.ejemplo.mx",
            "INITIAL_ADMIN_EMAIL": "admin@ejemplo.mx",
            "INITIAL_ADMIN_PASSWORD": "UnaClaveLargaDeVerdad2026",
        })
        with contextlib.redirect_stdout(io.StringIO()):
            falta = omni._faltan_datos_de_instalacion(
                "crm.ejemplo.mx", omni._leer_env("crm.ejemplo.mx")
            )
        self.assertFalse(falta)


class ElCorreoDelPanelSeVeAlEscribirlo(DirectorioTemporal):
    """En la instalación en un clic la persona guarda su usuario del panel con
    `secret set`. El correo no es un secreto: tecleado a ciegas sale mal sin que
    se note hasta que el panel no deja entrar. La contraseña sigue a ciegas."""

    def _guardar(self, nombre: str, a_la_vista, a_ciegas) -> int:
        import contextlib
        import io
        from unittest import mock

        with mock.patch("builtins.input", a_la_vista), \
                mock.patch.object(omni, "_terminal_de_llaves_en_windows", lambda: True), \
                mock.patch.object(omni.getpass, "getpass", a_ciegas), \
                contextlib.redirect_stdout(io.StringIO()):
            return omni.cmd_secret(_Args(instance="crm.ejemplo.mx", accion="set", nombre=nombre))

    def test_el_correo_a_la_vista_y_la_contrasena_a_ciegas(self):
        def no_se_usa(*_a):
            raise AssertionError("se pidió por el lado equivocado")

        self.assertEqual(
            self._guardar("INITIAL_ADMIN_EMAIL", lambda *_a: "ana@clinica.mx", no_se_usa), 0)
        self.assertEqual(
            self._guardar("INITIAL_ADMIN_PASSWORD", no_se_usa,
                          lambda *_a: "UnaClaveLargaDeVerdad2026"), 0)
        datos = omni._leer_env("crm.ejemplo.mx")
        self.assertEqual(datos["INITIAL_ADMIN_EMAIL"], "ana@clinica.mx")
        self.assertEqual(datos["INITIAL_ADMIN_PASSWORD"], "UnaClaveLargaDeVerdad2026")


class LasLlavesSobrevivenAUnZipNuevo(DirectorioTemporal):
    """Desde la 0.12.0 las llaves viven en la carpeta personal (`~/.omnicanal`).

    Cada versión del curso llega como un zip nuevo, que es una carpeta nueva: con
    las llaves junto al script, la carpeta nueva no traía el token de Coolify ni
    el registro que usa `rollback`. Las de antes se copian la primera vez.
    """

    def _vieja(self, archivos: dict) -> Path:
        vieja = omni.DIR_ANTIGUO
        vieja.mkdir(parents=True)
        for nombre, contenido in archivos.items():
            (vieja / nombre).write_text(contenido, encoding="utf-8")
        return vieja

    def _traer(self) -> str:
        import contextlib
        import io

        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            omni._traer_llaves_de_antes()
        return salida.getvalue()

    def test_viven_en_la_carpeta_personal_y_no_junto_al_script(self):
        self.assertEqual(self.dir_previo, Path.home() / ".omnicanal")
        self.assertEqual(self.antiguo_previo, omni.RAIZ / ".instalacion")

    @unittest.skipIf(os.name == "nt", SIN_PERMISOS_POSIX)
    def test_la_primera_vez_se_copian_sin_borrar_las_viejas_ni_imprimirlas(self):
        vieja = self._vieja({
            "global.env": "COOLIFY_TOKEN=tok_no_imprimir\n",
            "crm.ejemplo.mx.env": "ENCRYPTION_KEY=llave_no_imprimir\n",
            "instances.json": json.dumps({"crm.ejemplo.mx": {"service": "zrfkscu3powqu574xzotfo6d"}}),
        })
        texto = self._traer()
        datos = omni._leer_env("crm.ejemplo.mx")
        self.assertEqual(datos["COOLIFY_TOKEN"], "tok_no_imprimir")
        self.assertEqual(datos["ENCRYPTION_KEY"], "llave_no_imprimir")
        self.assertEqual(omni._indice()["crm.ejemplo.mx"]["service"], "zrfkscu3powqu574xzotfo6d")
        modo = os.stat(omni._ruta_env("crm.ejemplo.mx")).st_mode & 0o777
        self.assertEqual(modo, 0o600, f"permisos {oct(modo)}")
        self.assertTrue((vieja / "crm.ejemplo.mx.env").is_file(), "la vieja no se borra")
        self.assertTrue((vieja / omni.MARCA_TRAIDA).is_file(), "queda dicho que ya no se usa")
        self.assertIn(str(omni.DIR_INSTALACION), texto)
        self.assertNotIn("tok_no_imprimir", texto)
        self.assertNotIn("llave_no_imprimir", texto)

    def test_lo_que_ya_esta_en_la_carpeta_nueva_manda(self):
        omni._escribir_env("crm.ejemplo.mx", {"INITIAL_ADMIN_EMAIL": "nueva@ejemplo.mx"})
        self._vieja({"crm.ejemplo.mx.env": "INITIAL_ADMIN_EMAIL=vieja@ejemplo.mx\n",
                     "global.env": "COOLIFY_BASE_URL=https://coolify.ejemplo.mx\n"})
        texto = self._traer()
        self.assertEqual(omni._leer_env("crm.ejemplo.mx")["INITIAL_ADMIN_EMAIL"], "nueva@ejemplo.mx")
        self.assertEqual(omni._leer_env("crm.ejemplo.mx")["COOLIFY_BASE_URL"],
                         "https://coolify.ejemplo.mx", "lo que faltaba sí se copia")
        self.assertIn("crm.ejemplo.mx.env", texto.split("sin copiar")[1])

    def test_el_indice_se_junta_sin_pisar_ninguna_instalacion(self):
        """La `ultima_actualizacion` de la carpeta nueva puede ser más reciente, y
        es la que usa `rollback`."""
        omni._registrar("crm.beta.mx", ultima_actualizacion={"resultado": "ok"})
        self._vieja({"instances.json": json.dumps({
            "crm.alfa.mx": {"service": "alfauuid0000000001"},
            "crm.beta.mx": {"ultima_actualizacion": {"resultado": "falta rollback"}},
        })})
        self._traer()
        idx = omni._indice()
        self.assertEqual(idx["crm.alfa.mx"]["service"], "alfauuid0000000001")
        self.assertEqual(idx["crm.beta.mx"]["ultima_actualizacion"]["resultado"], "ok")

    def test_la_segunda_vez_no_vuelve_a_copiar_ni_dice_nada(self):
        self._vieja({"crm.ejemplo.mx.env": "INITIAL_ADMIN_EMAIL=vieja@ejemplo.mx\n"})
        self._traer()
        omni._ruta_env("crm.ejemplo.mx").unlink()
        self.assertEqual(self._traer(), "")
        self.assertFalse(omni._ruta_env("crm.ejemplo.mx").exists())


class FuncionaEnWindows(DirectorioTemporal):
    """Lo que en Windows se rompía, simulado aquí. La CI corre además omni.py en
    un Windows de verdad, con la salida por una tubería como la lee Claude Code."""

    def test_los_simbolos_no_revientan_con_la_salida_de_windows(self):
        import io

        with self.assertRaises(UnicodeEncodeError, msg="la premisa: cp1252 no tiene ✓"):
            "✓".encode("cp1252")
        crudo = io.BytesIO()
        flujo = io.TextIOWrapper(crudo, encoding="cp1252")
        omni._salida_en_utf8(flujo)
        flujo.write("✓ copiada → ✗")
        flujo.flush()
        self.assertEqual(crudo.getvalue().decode("utf-8"), "✓ copiada → ✗")

    def test_mcp_setup_llama_a_npx_y_a_claude_por_su_ruta(self):
        """En Windows `npx` es un `.cmd`: por su nombre a secas subprocess no lo encuentra."""
        import contextlib
        import io
        from unittest import mock

        rutas = {"claude": "C:\\Users\\ana\\.local\\bin\\claude.exe",
                 "npx": "C:\\Program Files\\nodejs\\npx.cmd"}
        llamadas = []

        def correr(cmd, *_a, **_k):
            llamadas.append(cmd)
            return mock.Mock(returncode=0, stdout="Coolify responde", stderr="")

        with mock.patch.object(omni.shutil, "which", rutas.get), \
                mock.patch("builtins.input", lambda *_a: "https://coolify.ejemplo.mx"), \
                mock.patch.object(omni.getpass, "getpass", lambda *_a: "tok_de_prueba"), \
                mock.patch.object(omni, "_terminal_de_llaves_en_windows", lambda: True), \
                mock.patch.object(omni.subprocess, "run", correr), \
                contextlib.redirect_stdout(io.StringIO()):
            codigo = omni.cmd_mcp_setup(_Args())
        self.assertEqual(codigo, 0)
        # Primero el doctor (npx) y después Claude Code (quitar el viejo y añadir).
        self.assertEqual([c[0] for c in llamadas], [rutas["npx"], rutas["claude"], rutas["claude"]])

    def test_sin_node_lo_dice_en_vez_de_reventar(self):
        import contextlib
        import io
        from unittest import mock

        salida = io.StringIO()
        with mock.patch.object(omni.shutil, "which",
                               lambda n: "C:\\claude.exe" if n == "claude" else None), \
                contextlib.redirect_stdout(salida):
            codigo = omni.cmd_mcp_setup(_Args())
        self.assertEqual(codigo, 1)
        self.assertIn("Node.js", salida.getvalue())

    def test_en_git_bash_no_deja_escribir_una_llave_a_la_vista(self):
        """Git Bash no es una consola de Windows: getpass enseñaría la llave."""
        import contextlib
        import io
        from unittest import mock

        def no_se_usa(*_a):
            raise AssertionError("se pidió la llave donde se vería")

        salida = io.StringIO()
        with mock.patch.object(omni, "_en_windows", lambda: True), \
                mock.patch("sys.stdin", mock.Mock(isatty=lambda: False)), \
                mock.patch.object(omni.getpass, "getpass", no_se_usa), \
                contextlib.redirect_stdout(salida):
            codigo = omni.cmd_secret(_Args(instance="crm.ejemplo.mx", accion="set",
                                           nombre="INITIAL_ADMIN_PASSWORD"))
        self.assertEqual(codigo, 1)
        self.assertIn("PowerShell", salida.getvalue())

    def test_el_correo_si_se_puede_escribir_en_git_bash(self):
        import contextlib
        import io
        from unittest import mock

        with mock.patch.object(omni, "_en_windows", lambda: True), \
                mock.patch("sys.stdin", mock.Mock(isatty=lambda: False)), \
                mock.patch("builtins.input", lambda *_a: "ana@clinica.mx"), \
                contextlib.redirect_stdout(io.StringIO()):
            codigo = omni.cmd_secret(_Args(instance="crm.ejemplo.mx", accion="set",
                                           nombre="INITIAL_ADMIN_EMAIL"))
        self.assertEqual(codigo, 0)
        self.assertEqual(omni._leer_env("crm.ejemplo.mx")["INITIAL_ADMIN_EMAIL"], "ana@clinica.mx")

    def test_sin_colores_en_la_consola_clasica_de_windows(self):
        from unittest import mock

        with mock.patch.object(omni, "_en_windows", lambda: True), \
                mock.patch("sys.stdout", mock.Mock(isatty=lambda: True)), \
                mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(omni._color("✓", omni.VERDE), "✓")
            os.environ["WT_SESSION"] = "sesion-de-windows-terminal"
            self.assertIn("\033[", omni._color("✓", omni.VERDE))

    def test_lo_que_va_por_ssh_llega_sin_retornos_de_carro(self):
        """En Windows, mandar el script por la entrada en modo texto cambiaba cada
        `\\n` por `\\r\\n`, y el bash del servidor rechazaba las líneas: `update`
        decía «no puedo entrar por SSH» con la llave bien puesta. Aquí, en vez de
        ssh, corre un Python que devuelve los bytes que le llegaron. En el job de
        Windows de la CI, el modo texto no pasa esta prueba."""
        from unittest import mock

        eco = [sys.executable, "-c", "import sys; sys.stdout.write(repr(sys.stdin.buffer.read()))"]
        script = "set -e\ndocker ps --format '{{.Names}}'\necho LISTO\n"
        destino = omni._Destino("crm.ejemplo.mx", "203.0.113.7", "zrfkscu3powqu574xzotfo6d", _Args())
        with mock.patch.object(omni, "_cmd_ssh", lambda *_a: eco):
            codigo, salida, _ = omni._remoto(destino, script)
            del_login = omni._ssh("203.0.113.7", "docker login", _Args(), entrada="token\n")
        self.assertEqual(codigo, 0)
        self.assertEqual(salida, repr(script.encode("utf-8")), "al servidor le llegó otra cosa")
        self.assertEqual(del_login, repr(b"token\n"), "al docker login le llegó otra cosa")

    def test_la_entrada_de_ssh_va_en_bytes(self):
        """La misma regla, comprobable en cualquier sistema: si alguien vuelve al
        modo texto, en Mac y Linux no se nota, pero en Windows rompe `update`."""
        from unittest import mock

        recibido = {}

        def correr(cmd, **kw):
            recibido.update(kw)
            return mock.Mock(returncode=0, stdout=b"ok\n", stderr=b"")

        with mock.patch.object(omni.subprocess, "run", correr):
            codigo, salida, _ = omni._remoto(
                omni._Destino("crm.ejemplo.mx", "203.0.113.7", "zrf", _Args()), "echo hola\n")
        self.assertEqual((codigo, salida), (0, "ok\n"))
        self.assertIsInstance(recibido["input"], bytes)
        self.assertNotIn("text", recibido)
        self.assertNotIn("encoding", recibido)

    def test_el_changelog_de_la_raiz_convertido_en_texto_no_pierde_la_version(self):
        """En Windows git deja el enlace `CHANGELOG.md` como un texto con la ruta."""
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "CHANGELOG.md").write_text("backend/CHANGELOG.md", encoding="utf-8")
            Path(tmp, "backend").mkdir()
            Path(tmp, "backend", "CHANGELOG.md").write_text(
                "# Novedades\n\n## [0.12.2]\n\n- Sí.\n", encoding="utf-8")
            previa = omni.RAIZ
            omni.RAIZ = Path(tmp)
            try:
                self.assertEqual(omni._version_del_repo(), "0.12.2")
            finally:
                omni.RAIZ = previa


class ElWidgetSePruebaComoLaPaginaDemo(unittest.TestCase):
    """El perfil siembra el chat con el dominio del CRM como único permitido, y el
    CRM rechaza con 403 lo que llega sin origen. `verify` hablaba como un script y
    en una instalación nueva fallaba siempre (salió al verificar crm2)."""

    def test_abre_la_sesion_y_escribe_con_el_origen_del_crm(self):
        import contextlib
        import io
        from unittest import mock

        llamadas = []

        def post(url, cuerpo, cabeceras=None, **_k):
            llamadas.append((url, cabeceras or {}))
            if url.endswith("/webchat/sessions"):
                return 200, {"visitor_id": "v1", "session_token": "t1"}
            return 200, {"conversation_id": "c1"}

        def get(url, timeout=10, cabeceras=None):
            if url.endswith("/webchat/demo"):
                return 200, json.dumps({"api_key": "clave-publica-del-widget"})
            return 200, json.dumps({"messages": [
                {"role": "assistant", "text": "Atendemos de lunes a viernes de 8:00 a 17:00"}]})

        with mock.patch.object(omni, "_post", post), mock.patch.object(omni, "_get", get), \
                mock.patch.object(omni.time, "sleep", lambda _s: None), \
                mock.patch.object(omni, "_borrar_conversacion", lambda *_a: None), \
                contextlib.redirect_stdout(io.StringIO()):
            fallos = omni._verify_widget("https://crm.ejemplo.mx", "https://crm.ejemplo.mx/api/v1", {})
        self.assertEqual(fallos, 0)
        self.assertEqual([u.rsplit("/", 1)[-1] for u, _c in llamadas], ["sessions", "messages"])
        for url, cabeceras in llamadas:
            self.assertEqual(cabeceras.get("Origin"), "https://crm.ejemplo.mx", url)


class ElDnsSePuedeSaltar(unittest.TestCase):
    """El DNS público puede estar bien y tu computadora seguir con la caché
    vieja: sin esto, verify decía "no responde" con el servidor sano."""

    def test_solo_se_toca_el_dominio_indicado_y_se_deja_como_estaba(self):
        import socket
        original = socket.getaddrinfo
        with omni._dns_a_mano("crm.ejemplo.mx", "203.0.113.10"):
            resuelto = socket.getaddrinfo("crm.ejemplo.mx", 80)[0][4][0]
            self.assertEqual(resuelto, "203.0.113.10")
        self.assertIs(socket.getaddrinfo, original)

    def test_sin_ip_no_toca_nada(self):
        import socket
        original = socket.getaddrinfo
        with omni._dns_a_mano("crm.ejemplo.mx", None):
            self.assertIs(socket.getaddrinfo, original)


class ConectarSocialPorZernio(unittest.TestCase):
    """`connect social` (ZN-3): valida la llave, conecta y NUNCA la imprime."""

    def _correr(self, respuestas: dict, **kw):
        import contextlib
        import io

        llamadas = []

        def _post(url, cuerpo, cabeceras=None, **_):
            llamadas.append((url.rsplit("/api/v1", 1)[-1], cuerpo))
            return respuestas[url.rsplit("/api/v1", 1)[-1]]

        previo = omni._post
        omni._post = _post
        salida = io.StringIO()
        try:
            args = _Args(instagram=None, facebook=None, wa_por_zernio=False, **kw)
            with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(salida):
                codigo = omni._connect_social(
                    args, "https://crm.x/api/v1", "https://crm.x",
                    {"ZERNIO_API_KEY": "sk_secreta_no_imprimir"}, {},
                )
        finally:
            omni._post = previo
        return codigo, llamadas, salida.getvalue()

    def test_prueba_la_llave_antes_de_conectar_y_no_la_imprime(self):
        codigo, llamadas, salida = self._correr({
            "/admin/zernio/probar": (200, [
                {"id": "a1", "platform": "instagram", "username": "clinica"},
                {"id": "a2", "platform": "facebook", "display_name": "Clínica"},
            ]),
            "/admin/zernio": (200, {
                "canales": [{"tipo": "instagram_dm"}, {"tipo": "messenger"}],
                "webhook_registrado": True, "historial_encolado": 2,
            }),
        })
        self.assertEqual(codigo, 0)
        self.assertEqual([u for u, _ in llamadas], ["/admin/zernio/probar", "/admin/zernio"])
        self.assertNotIn("sk_secreta_no_imprimir", salida)
        self.assertIn("Messenger", salida)

    def test_llave_mala_para_sin_conectar(self):
        codigo, llamadas, salida = self._correr({
            "/admin/zernio/probar": (400, {"error": '{"detail": "Zernio no acepta esa API key."}'}),
        })
        self.assertEqual(codigo, 1)
        self.assertEqual(len(llamadas), 1)
        self.assertIn("Zernio no acepta esa API key.", salida)

    def test_sin_webhook_por_api_explica_como_pegarlo_sin_imprimir_el_secreto(self):
        codigo, _, salida = self._correr({
            "/admin/zernio/probar": (200, [{"id": "a1", "platform": "instagram"}]),
            "/admin/zernio": (200, {
                "canales": [{"tipo": "instagram_dm"}], "webhook_registrado": False,
                "webhook_url": "https://crm.x/api/v1/webhooks/zernio",
                "webhook_secret_manual": "whsec_no_imprimir",
            }),
        })
        self.assertEqual(codigo, 0)
        self.assertIn("/webhooks/zernio", salida)
        self.assertNotIn("whsec_no_imprimir", salida)


class ConectarLaVozPorRetell(unittest.TestCase):
    """`connect voice` (US-19): el CRM crea el agente; las llaves no se imprimen."""

    LLAVES = {
        "RETELL_API_KEY": "key_retell_no_imprimir",
        "TWILIO_ACCOUNT_SID": "AC_sid_no_imprimir",
        "TWILIO_AUTH_TOKEN": "tok_twilio_no_imprimir",
    }
    AGENTE = (200, {
        "agent_created": True, "agent_id_retell": "agent_1", "phone_number": "",
        "checks": [{"name": "agente_publicado", "ok": True, "detail": "versión 0"}],
    })

    def _correr(self, respuestas: dict, datos: dict | None = None, **kw):
        import contextlib
        import io

        llamadas = []

        def _post(url, cuerpo, cabeceras=None, **_):
            ruta = url.rsplit("/api/v1", 1)[-1]
            llamadas.append((ruta, cuerpo))
            return respuestas[ruta]

        previo = omni._post
        omni._post = _post
        salida = io.StringIO()
        opciones = dict(voz=None, comprar_numero=False, lada=None, twilio=False,
                        troncal=None, transferir_a=None, numero=None)
        opciones.update(kw)
        try:
            with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(salida):
                codigo = omni._connect_voice(
                    _Args(**opciones), "https://crm.x/api/v1",
                    self.LLAVES if datos is None else datos, {},
                )
        finally:
            omni._post = previo
        return codigo, llamadas, salida.getvalue()

    def _sin_llaves(self, salida: str) -> None:
        for valor in self.LLAVES.values():
            self.assertNotIn(valor, salida)

    def test_pide_crear_el_agente_y_no_imprime_la_llave(self):
        codigo, llamadas, salida = self._correr(
            {"/admin/channels/retell/provision": self.AGENTE}, transferir_a="+529981112233"
        )
        self.assertEqual(codigo, 0)
        ((ruta, cuerpo),) = llamadas
        self.assertEqual(ruta, "/admin/channels/retell/provision")
        self.assertTrue(cuerpo["create_agent"])
        self.assertIsNone(cuerpo["buy_number"], "comprar número nunca va por defecto")
        self.assertEqual(cuerpo["transfer_number"], "+529981112233")
        self.assertIn("todavía no tiene número", salida)
        self._sin_llaves(salida)

    def test_sin_llave_de_retell_no_llama_al_crm(self):
        codigo, llamadas, salida = self._correr({}, datos={})
        self.assertEqual(codigo, 1)
        self.assertEqual(llamadas, [])
        self.assertIn("secret set RETELL_API_KEY", salida)

    def test_comprar_numero_avisa_del_cobro(self):
        comprado = (200, {**self.AGENTE[1], "phone_number": "+13055551000"})
        codigo, llamadas, salida = self._correr(
            {"/admin/channels/retell/provision": comprado}, comprar_numero=True, lada=305
        )
        self.assertEqual(codigo, 0)
        self.assertEqual(llamadas[0][1]["buy_number"], "us")
        self.assertIn("se cobra cada mes", salida)
        self.assertIn("+13055551000", salida)

    def test_con_twilio_cablea_la_troncal_sin_imprimir_su_token(self):
        codigo, llamadas, salida = self._correr({
            "/admin/channels/retell/provision": self.AGENTE,
            "/admin/channels/retell/twilio": (200, {
                "phone_number": "+529981234567", "trunk_domain": "crm-x.pstn.twilio.com",
                "trunk_created": True,
                "checks": [{"name": "entrada_hacia_retell", "ok": True, "detail": ""}],
            }),
        }, twilio=True, numero="+529981234567")
        self.assertEqual(codigo, 0)
        self.assertEqual([r for r, _ in llamadas],
                         ["/admin/channels/retell/provision", "/admin/channels/retell/twilio"])
        self.assertEqual(llamadas[0][1]["phone_number"], "", "el de Twilio lo ata el 2.º paso")
        self.assertIn("crm-x.pstn.twilio.com", salida)
        self._sin_llaves(salida)

    def test_con_twilio_y_sin_numero_para_antes_de_llamar(self):
        codigo, llamadas, _ = self._correr({}, twilio=True)
        self.assertEqual(codigo, 1)
        self.assertEqual(llamadas, [])

    def test_una_comprobacion_que_falla_se_ve_y_da_error(self):
        malo = (200, {**self.AGENTE[1], "checks": [
            {"name": "idioma_es_419", "ok": False, "detail": "es-ES"},
        ]})
        codigo, _, salida = self._correr({"/admin/channels/retell/provision": malo})
        self.assertEqual(codigo, 1)
        self.assertIn("es-ES", salida)

    def test_el_error_del_crm_se_explica(self):
        codigo, _, salida = self._correr({
            "/admin/channels/retell/provision": (
                400, {"error": '{"detail": "Retell no acepta la API key (401)."}'}
            ),
        })
        self.assertEqual(codigo, 1)
        self.assertIn("Retell no acepta la API key", salida)

    def test_con_numero_deja_el_telefono_para_la_espera(self):
        import contextlib
        import io

        comprado = (200, {**self.AGENTE[1], "phone_number": "+13055551000"})
        previo = omni._post
        omni._post = lambda url, cuerpo, cabeceras=None, **_: comprado
        resultado: dict = {}
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                omni._connect_voice(
                    _Args(voz=None, comprar_numero=True, lada=None, twilio=False,
                          troncal=None, transferir_a=None, numero=None),
                    "https://crm.x/api/v1", self.LLAVES, {}, resultado,
                )
        finally:
            omni._post = previo
        self.assertEqual(resultado, {"telefono": "+13055551000"})


class EsperarElMensajeDePrueba(unittest.TestCase):
    """Tras conectar, `connect` espera a que entre un mensaje de verdad (Fase 10.2).

    El alta puede salir en verde y el canal no recibir nada: la prueba de que
    funciona es un mensaje que entra, mirado con la hora del servidor.
    """

    INICIO = "2026-09-26T16:00:00.000000+00:00"
    INICIO_MS = 1790438400000

    def _estado(self, llegada=None, enabled=True, paused=False, errores=None):
        return (200, omni.json.dumps({
            "ahora": self.INICIO,
            "ahora_ms": self.INICIO_MS,
            "channels": [
                {"canal": "whatsapp", "enabled": enabled, "paused": paused,
                 "last_inbound_at": llegada},
                {"canal": "email", "enabled": True, "paused": False,
                 "last_inbound_at": "2026-09-26T17:00:00+00:00"},
            ],
            "recent_errors": errores or [],
        }))

    def _correr(self, respuestas: list, esperar: int = 300):
        import contextlib
        import io

        pendientes = list(respuestas)
        llamadas = []

        def _get(url, cabeceras=None, **_):
            llamadas.append(url.rsplit("/api/v1", 1)[-1])
            return pendientes.pop(0) if len(pendientes) > 1 else pendientes[0]

        reloj = [0.0]

        def _time():
            reloj[0] += 30
            return reloj[0]

        previos = omni._get, omni.time.sleep, omni.time.time
        omni._get, omni.time.sleep, omni.time.time = _get, (lambda _s: None), _time
        salida = io.StringIO()
        try:
            with contextlib.redirect_stdout(salida):
                codigo = omni._esperar_si_toca(
                    _Args(esperar=esperar), "https://crm.x/api/v1", {}, "whatsapp"
                )
        finally:
            omni._get, omni.time.sleep, omni.time.time = previos
        return codigo, llamadas, salida.getvalue()

    def test_cuando_entra_el_mensaje_sale_en_verde(self):
        codigo, llamadas, salida = self._correr([
            self._estado(llegada="2026-09-26T15:00:00+00:00"),
            self._estado(llegada="2026-09-26T15:00:00+00:00"),
            self._estado(llegada="2026-09-26T16:00:05.123456+00:00"),
        ])
        self.assertEqual(codigo, 0)
        self.assertEqual(llamadas, ["/admin/channels/estado"] * 3)
        self.assertIn("en verde", salida)

    def test_un_mensaje_viejo_o_de_otro_canal_no_cuenta(self):
        codigo, _, salida = self._correr(
            [self._estado(llegada="2026-09-26T15:59:59+00:00")], esperar=60
        )
        self.assertEqual(codigo, 1)
        self.assertIn("YCloud", salida, "sin mensaje, dice qué revisar")

    def test_avisa_si_el_canal_esta_apagado_o_en_pausa(self):
        _, _, salida = self._correr([self._estado(enabled=False, paused=True)], esperar=30)
        self.assertIn("está apagado", salida)
        self.assertIn("Pausado", salida)

    def test_un_error_del_crm_en_ese_canal_se_ve_una_vez(self):
        error = {"ts": self.INICIO_MS + 1000, "event": "ycloud.webhook.firma_invalida",
                 "canal": "whatsapp", "level": "error"}
        _, _, salida = self._correr(
            [self._estado(), self._estado(errores=[error])], esperar=90
        )
        self.assertEqual(salida.count("firma_invalida"), 1)

    def test_con_esperar_cero_no_pregunta_nada(self):
        codigo, llamadas, _ = self._correr([self._estado()], esperar=0)
        self.assertEqual(codigo, 0)
        self.assertEqual(llamadas, [])


class _ServidorFalso:
    """Un VPS con Coolify, de mentira: contenedores, base y copia en memoria.

    `al_desplegar` dice qué hace cada versión al levantarse: `sana` arranca,
    `sana_migrada` arranca y cambia la base, `rota` se queda reiniciando sin
    tocar la base y `rota_migrada` cambia la base y se queda reiniciando.
    """

    UUID = "zrfkscu3powqu574xzotfo6d"

    def __init__(self) -> None:
        self.version = "0.10.0"           # la que corren los contenedores
        self.app_version = "0.10.0"       # APP_VERSION en Coolify
        self.revision = "0061_unificacion_fichas"
        self.publicadas = {"0.10.0", "0.11.0"}
        self.al_desplegar: dict = {}
        self.sana = True
        self.parados = False
        self.recuento = {"contacts": 12, "conversations": 30, "messages": 400}
        self.hay_espacio = True
        self.permiso_deploy = True
        self.copia = None
        self.pasos: list = []             # lo que se hizo, en orden

    def contenedores(self, destino):
        contenedores = {}
        for nombre in omni.CON_VERSION:
            imagen = "frontend" if nombre == "frontend" else "backend"
            parado = self.parados and nombre in omni.ESCRIBEN_EN_LA_BASE
            contenedores[nombre] = {
                "imagen": f"ghcr.io/dueno/crm-{imagen}:{self.version}",
                "estado": "exited" if parado else ("running" if self.sana else "restarting"),
                "salud": "healthy" if self.sana else "unhealthy",
            }
        contenedores["db"] = {"imagen": "pgvector/pgvector:pg16", "estado": "running",
                              "salud": "healthy"}
        return contenedores

    def leer_revision(self, destino):
        return self.revision

    def contar(self, destino):
        return dict(self.recuento)

    def bajar(self, destino, imagenes):
        self.pasos.append("bajar")
        faltan = {omni._tag(i) for i in imagenes} - self.publicadas
        return not faltan, "manifest unknown" if faltan else ""

    def respaldar(self, destino, version):
        self.pasos.append("respaldar")
        if not self.hay_espacio:
            return 3, "", "SIN_ESPACIO libre_mb=300 base_mb=2000\n"
        self.copia = (self.revision, dict(self.recuento))
        ruta = f"/root/omnicanal-respaldos/{destino.dominio}/20260926-180000-v{version}.sql.gz"
        return 0, f"{ruta}|2097152\n", ""

    def parar(self, destino):
        self.pasos.append("parar")
        self.parados = True
        return True

    def restaurar(self, destino, respaldo):
        self.pasos.append("restaurar")
        self.revision, self.recuento = self.copia[0], dict(self.copia[1])
        return 0, "RESTAURADO\n", ""

    def changelog(self, destino):
        return (f"# Novedades\n\n## [{self.version}]\n\n### Añadido\n"
                f"- Lo nuevo de la {self.version}.\n\n## [0.10.0]\n\n- Lo de antes.\n")

    def api(self, args, metodo, ruta, cuerpo=None):
        self.pasos.append(f"{metodo} {ruta.split('?')[0]}")
        if metodo == "PATCH" and ruta == f"/services/{self.UUID}/envs":
            self.app_version = cuerpo["value"]
            return 200, {}
        if ruta.startswith("/deploy"):
            if not self.permiso_deploy:
                return 200, {"deployments": [{"message": "Unauthorized to deploy this service."}]}
            self._desplegar()
            return 200, {"deployments": [{"message": "Service crm started."}]}
        if ruta == "/services":
            return 200, [{"uuid": "otro00000000"}, {"uuid": self.UUID}]
        if ruta == f"/services/{self.UUID}":
            return 200, {"applications": [{"fqdn": "https://crm.ejemplo.mx"}]}
        return 200, {"applications": [{"fqdn": "https://otra.ejemplo.mx"}]}

    def cambios(self) -> list:
        """Lo que se hizo sin contar las lecturas a Coolify."""
        return [p for p in self.pasos if not p.startswith("GET ")]

    def _desplegar(self) -> None:
        self.version, self.parados = self.app_version, False
        que_pasa = self.al_desplegar.get(self.version, "sana")
        self.sana = que_pasa.startswith("sana")
        if que_pasa.endswith("migrada"):
            self.revision = "0062_rota"
            self.recuento["messages"] += 1  # lo que entró después de la copia


class _Flota:
    """Varias instalaciones, cada una con su servidor de mentira (para `--all`)."""

    UUID = None

    def __init__(self, servidores: dict) -> None:
        self.servidores = servidores  # dominio → _ServidorFalso

    def _de(self, destino_o_args):
        dominio = getattr(destino_o_args, "dominio", None) or destino_o_args.instance
        return self.servidores[dominio]

    def contenedores(self, d):
        return self._de(d).contenedores(d)

    def leer_revision(self, d):
        return self._de(d).leer_revision(d)

    def contar(self, d):
        return self._de(d).contar(d)

    def bajar(self, d, imagenes):
        return self._de(d).bajar(d, imagenes)

    def respaldar(self, d, version):
        return self._de(d).respaldar(d, version)

    def parar(self, d):
        return self._de(d).parar(d)

    def restaurar(self, d, respaldo):
        return self._de(d).restaurar(d, respaldo)

    def changelog(self, d):
        return self._de(d).changelog(d)

    def api(self, args, metodo, ruta, cuerpo=None):
        return self._de(args).api(args, metodo, ruta, cuerpo)


class _ConServidorFalso(DirectorioTemporal):
    """Corre `update` o `rollback` contra servidores y un Coolify de mentira."""

    def _correr(self, servidor, comando=None, terminal=False, respuesta="", verify=None, **kw):
        import contextlib
        import io
        from unittest import mock

        reemplazos = {
            "_srv_contenedores": servidor.contenedores, "_srv_bajar": servidor.bajar,
            "_srv_revision": servidor.leer_revision, "_srv_recuento": servidor.contar,
            "_srv_respaldar": servidor.respaldar, "_srv_parar": servidor.parar,
            "_srv_restaurar": servidor.restaurar, "_srv_changelog": servidor.changelog,
            "_api_coolify": servidor.api,
            "cmd_wait": lambda a: 0, "cmd_verify": verify or (lambda a: 0),
        }
        opciones = {
            "instance": "crm.ejemplo.mx", "version": "0.11.0", "service": servidor.UUID,
            "ip": "203.0.113.10", "user": "root", "coolify_url": None, "timeout": 600,
            "http": False,
        }
        opciones.update(kw)
        entrada = mock.Mock()
        entrada.isatty.return_value = terminal
        reloj = [0.0]

        def _ahora() -> float:
            reloj[0] += 30
            return reloj[0]

        salida = io.StringIO()
        with contextlib.ExitStack() as pila:
            for nombre, falso in reemplazos.items():
                pila.enter_context(mock.patch.object(omni, nombre, falso))
            pila.enter_context(mock.patch.object(omni.time, "sleep", lambda _s: None))
            pila.enter_context(mock.patch.object(omni.time, "time", _ahora))
            pila.enter_context(mock.patch("sys.stdin", entrada))
            pila.enter_context(mock.patch("builtins.input", lambda *_a: respuesta))
            pila.enter_context(contextlib.redirect_stdout(salida))
            codigo = (comando or omni.cmd_update)(_Args(**opciones))
        return codigo, salida.getvalue()

    def _registro(self) -> dict:
        return omni._indice()["crm.ejemplo.mx"]


class ActualizarSinMiedo(_ConServidorFalso):
    """`update` y `rollback` (Fase 10.3): copia antes de tocar nada y vuelta atrás.

    Lo que se prueba es el ORDEN: nada se toca hasta que la versión está en el
    servidor y la copia hecha, y la base solo se restaura con confirmación.
    """

    def test_copia_antes_de_tocar_nada_y_deja_la_version_nueva(self):
        servidor = _ServidorFalso()
        codigo, salida = self._correr(servidor)
        self.assertEqual(codigo, 0, salida)
        self.assertEqual(servidor.version, "0.11.0")
        self.assertEqual(servidor.cambios()[:4], [
            "bajar", "respaldar", f"PATCH /services/{servidor.UUID}/envs", "POST /deploy",
        ])
        self.assertEqual(self._registro()["version"], "0.11.0")
        self.assertEqual(self._registro()["ultima_actualizacion"]["resultado"], "ok")
        self.assertIn("Lo nuevo de la 0.11.0", salida)
        self.assertNotIn("Lo de antes", salida, "solo lo que trae la versión nueva")
        self.assertIn("datos intactos", salida)

    def test_una_version_sin_publicar_no_toca_nada(self):
        """Redesplegar antes de que estén las imágenes dejó la 0.3.0 sin contenedores."""
        servidor = _ServidorFalso()
        codigo, salida = self._correr(servidor, version="0.12.0")
        self.assertEqual(codigo, 1)
        self.assertEqual(servidor.cambios(), ["bajar"])
        self.assertIn("no está publicada", salida)

    def test_no_baja_de_version_ni_acepta_latest(self):
        casos = (("0.9.0", "anterior a la 0.10.0"), ("latest", "no es una versión"))
        for version, mensaje in casos:
            servidor = _ServidorFalso()
            codigo, salida = self._correr(servidor, version=version)
            self.assertEqual(codigo, 1, version)
            self.assertEqual(servidor.cambios(), [], version)
            self.assertIn(mensaje, salida)

    def test_en_la_misma_version_no_hace_nada(self):
        servidor = _ServidorFalso()
        codigo, salida = self._correr(servidor, version="v0.10.0")
        self.assertEqual(codigo, 0)
        self.assertEqual(servidor.cambios(), [])
        self.assertIn("no hay nada que hacer", salida)

    def test_sin_espacio_para_la_copia_no_se_actualiza(self):
        servidor = _ServidorFalso()
        servidor.hay_espacio = False
        codigo, salida = self._correr(servidor)
        self.assertEqual(codigo, 1)
        self.assertEqual(servidor.cambios(), ["bajar", "respaldar"])
        self.assertIn("libera disco", salida)

    def test_si_coolify_no_despliega_la_variable_vuelve_a_la_anterior(self):
        """Coolify contesta 200 aunque el token no tenga permiso de deploy."""
        servidor = _ServidorFalso()
        servidor.permiso_deploy = False
        codigo, salida = self._correr(servidor)
        self.assertEqual(codigo, 1)
        self.assertEqual(servidor.app_version, "0.10.0")
        self.assertIn("permiso de deploy", salida)

    def test_si_no_arranca_y_la_base_no_cambio_vuelve_sola(self):
        servidor = _ServidorFalso()
        servidor.al_desplegar = {"0.11.0": "rota"}
        codigo, salida = self._correr(servidor)
        self.assertEqual(codigo, 1)
        self.assertEqual(servidor.version, "0.10.0")
        self.assertTrue(servidor.sana)
        self.assertNotIn("restaurar", servidor.pasos)
        self.assertEqual(self._registro()["version"], "0.10.0")
        self.assertEqual(self._registro()["ultima_actualizacion"]["resultado"], "vuelta atrás")

    def test_si_la_base_cambio_desde_el_chat_no_restaura_y_dice_como(self):
        servidor = _ServidorFalso()
        servidor.al_desplegar = {"0.11.0": "rota_migrada"}
        codigo, salida = self._correr(servidor, terminal=False)
        self.assertEqual(codigo, 1)
        self.assertNotIn("parar", servidor.pasos)
        self.assertNotIn("restaurar", servidor.pasos)
        self.assertIn("omni.py rollback --instance crm.ejemplo.mx", salida)
        self.assertEqual(self._registro()["ultima_actualizacion"]["resultado"], "falta rollback")

    def test_encima_de_una_actualizacion_a_medias_no_se_actualiza(self):
        """La copia nueva sería de la base rota y el rollback perdería la buena."""
        servidor = _ServidorFalso()
        servidor.al_desplegar = {"0.11.0": "rota_migrada"}
        self._correr(servidor, terminal=False)
        servidor.pasos.clear()
        codigo, salida = self._correr(servidor, version="0.11.0")
        self.assertEqual(codigo, 1)
        self.assertEqual(servidor.cambios(), [])
        self.assertIn("quedó a medias", salida)

    def test_si_la_base_cambio_en_tu_terminal_restaura_y_vuelve(self):
        servidor = _ServidorFalso()
        servidor.al_desplegar = {"0.11.0": "rota_migrada"}
        codigo, salida = self._correr(servidor, terminal=True, respuesta="RESTAURAR")
        self.assertEqual(codigo, 1, "la actualización falló, aunque la vuelta saliera bien")
        pasos = servidor.pasos
        self.assertLess(pasos.index("parar"), pasos.index("restaurar"))
        ultimo_despliegue = len(pasos) - 1 - pasos[::-1].index("POST /deploy")
        self.assertLess(pasos.index("restaurar"), ultimo_despliegue)
        self.assertEqual((servidor.version, servidor.revision),
                         ("0.10.0", "0061_unificacion_fichas"))
        self.assertEqual(self._registro()["ultima_actualizacion"]["resultado"], "vuelta atrás")

    def test_sin_escribir_restaurar_no_se_toca_la_base(self):
        servidor = _ServidorFalso()
        servidor.al_desplegar = {"0.11.0": "rota_migrada"}
        codigo, salida = self._correr(servidor, terminal=True, respuesta="si")
        self.assertEqual(codigo, 1)
        self.assertNotIn("parar", servidor.pasos)
        self.assertEqual(servidor.revision, "0062_rota")
        self.assertIn("Cancelado", salida)

    def test_rollback_deshace_una_actualizacion_que_cambio_la_base(self):
        servidor = _ServidorFalso()
        servidor.al_desplegar = {"0.11.0": "sana_migrada"}
        self.assertEqual(self._correr(servidor)[0], 0)
        codigo, salida = self._correr(servidor, omni.cmd_rollback, terminal=True,
                                      respuesta="RESTAURAR")
        self.assertEqual(codigo, 0, salida)
        self.assertEqual((servidor.version, servidor.revision),
                         ("0.10.0", "0061_unificacion_fichas"))
        self.assertEqual(self._registro()["version"], "0.10.0")

    def test_rollback_no_se_corre_desde_el_chat(self):
        servidor = _ServidorFalso()
        codigo, salida = self._correr(servidor, omni.cmd_rollback, terminal=False)
        self.assertEqual(codigo, 1)
        self.assertEqual(servidor.pasos, [])
        self.assertIn("no se corre desde el chat", salida)

    def test_encuentra_el_service_por_el_dominio_y_lo_apunta(self):
        servidor = _ServidorFalso()
        codigo, _ = self._correr(servidor, service=None, version="0.10.0")
        self.assertEqual(codigo, 0)
        self.assertIn("GET /services", servidor.pasos)
        self.assertEqual(self._registro()["service"], servidor.UUID)

    def test_sin_el_usuario_del_panel_no_corre_verify_y_dice_como(self):
        """Instalación en un clic: el administrador se creó en el panel. verify
        fallaría por eso, y su aviso mandaría a deshacer una versión sana."""
        servidor = _ServidorFalso()
        llamadas = []
        codigo, salida = self._correr(servidor, verify=lambda a: llamadas.append(a) or 1)
        self.assertEqual(codigo, 0, salida)
        self.assertEqual(llamadas, [])
        self.assertIn("no hago la comprobación completa", salida)
        self.assertIn("secret set INITIAL_ADMIN_EMAIL --instance crm.ejemplo.mx", salida)
        self.assertNotIn("rollback", salida.split("actualizada a la")[1])

    def test_con_el_usuario_del_panel_si_corre_verify(self):
        omni._escribir_env("crm.ejemplo.mx", {"INITIAL_ADMIN_EMAIL": "ana@clinica.mx",
                                              "INITIAL_ADMIN_PASSWORD": "UnaClaveLargaDeVerdad2026"})
        servidor = _ServidorFalso()
        llamadas = []
        codigo, salida = self._correr(servidor, verify=lambda a: llamadas.append(a.instance) or 0)
        self.assertEqual(codigo, 0, salida)
        self.assertEqual(llamadas, ["crm.ejemplo.mx"])


class ActualizarTodasEnSerie(_ConServidorFalso):
    """`update --all`: una detrás de otra; si una falla, las siguientes ni se tocan."""

    def _flota(self):
        alfa, beta = _ServidorFalso(), _ServidorFalso()
        beta.UUID = "betauuid000000000000001"
        omni._registrar("crm.alfa.mx", service=alfa.UUID, ip="203.0.113.11")
        omni._registrar("crm.beta.mx", service=beta.UUID, ip="203.0.113.12")
        return alfa, beta, _Flota({"crm.alfa.mx": alfa, "crm.beta.mx": beta})

    def _todas(self, flota):
        return self._correr(flota, instance=None, service=None, ip=None, todas=True)

    def test_pone_todas_al_dia_una_detras_de_otra(self):
        alfa, beta, flota = self._flota()
        codigo, salida = self._todas(flota)
        self.assertEqual(codigo, 0, salida)
        self.assertEqual((alfa.version, beta.version), ("0.11.0", "0.11.0"))
        self.assertLess(salida.index("crm.alfa.mx (1 de 2)"), salida.index("crm.beta.mx (2 de 2)"))

    def test_se_detiene_en_la_primera_que_falla(self):
        alfa, beta, flota = self._flota()
        alfa.al_desplegar = {"0.11.0": "rota"}
        codigo, salida = self._todas(flota)
        self.assertEqual(codigo, 1)
        self.assertEqual(alfa.version, "0.10.0", "la que falla vuelve a su versión")
        self.assertEqual(beta.cambios(), [], "la siguiente ni se toca")
        self.assertIn("sin tocar: crm.beta.mx", salida)

    def test_no_se_mezcla_con_una_instalacion_concreta(self):
        servidor = _ServidorFalso()
        codigo, salida = self._correr(servidor, todas=True)
        self.assertEqual(codigo, 1)
        self.assertEqual(servidor.pasos, [])
        self.assertIn("no se combina con --instance", salida)


class ElInstaladorApuntaLoQueUpdateNecesita(DirectorioTemporal):
    """`update` necesita el service de Coolify y su token: se apuntan al instalar."""

    def test_push_env_apunta_el_service(self):
        import contextlib
        import io
        from unittest import mock

        omni.cmd_secrets(_Args(instance="crm.ejemplo.mx", email="admin@ejemplo.mx"))
        with mock.patch.object(omni, "_api_coolify", lambda *a, **k: (201, {})), \
                contextlib.redirect_stdout(io.StringIO()):
            codigo = omni.cmd_push_env(_Args(instance="crm.ejemplo.mx",
                                              service="zrfkscu3powqu574xzotfo6d",
                                              coolify_url=None))
        self.assertEqual(codigo, 0)
        self.assertEqual(omni._indice()["crm.ejemplo.mx"]["service"], "zrfkscu3powqu574xzotfo6d")

    def test_mcp_setup_guarda_el_token_para_omni_sin_imprimirlo(self):
        import contextlib
        import io
        from unittest import mock

        proceso = mock.Mock(returncode=0, stdout="Coolify responde", stderr="")
        salida = io.StringIO()
        with mock.patch.object(omni, "_hay", lambda _p: True), \
                mock.patch("builtins.input", lambda *_a: "https://coolify.ejemplo.mx/"), \
                mock.patch.object(omni.getpass, "getpass", lambda *_a: "tok_coolify_no_imprimir"), \
                mock.patch.object(omni, "_terminal_de_llaves_en_windows", lambda: True), \
                mock.patch.object(omni.subprocess, "run", lambda *a, **k: proceso), \
                contextlib.redirect_stdout(salida):
            codigo = omni.cmd_mcp_setup(_Args())
        self.assertEqual(codigo, 0)
        globales = omni._leer_archivo(omni._ruta_env(omni.RUTA_GLOBAL))
        self.assertEqual(globales["COOLIFY_BASE_URL"], "https://coolify.ejemplo.mx")
        self.assertEqual(globales["COOLIFY_TOKEN"], "tok_coolify_no_imprimir")
        self.assertNotIn("tok_coolify_no_imprimir", salida.getvalue())


class CoolifyConUnSoloParDeLlaves(DirectorioTemporal):
    """Salió preparando la Mac para actualizar el CRM del curso (2026-10-22): una URL
    equivocada en mcp-setup dejaba el MCP registrado y el reintento chocaba con
    «already exists»; y un COOLIFY_ACCESS_TOKEN viejo en la terminal se usaba con
    la URL guardada, Coolify decía 401 y omni.py decía «no encuentro el service»."""

    def _mcp_setup(self, respuestas: dict):
        import contextlib
        import io
        from unittest import mock

        llamadas = []

        def correr(cmd, *_a, **_k):
            llamadas.append(cmd)
            clave = "doctor" if "doctor" in cmd else ("remove" if "remove" in cmd else "add")
            return mock.Mock(returncode=respuestas.get(clave, 0), stdout="", stderr="ya existe")

        salida = io.StringIO()
        with mock.patch.object(omni, "_hay", lambda _p: True), \
                mock.patch("builtins.input", lambda *_a: "https://coolify.ejemplo.mx/"), \
                mock.patch.object(omni.getpass, "getpass", lambda *_a: "tok_nuevo"), \
                mock.patch.object(omni, "_terminal_de_llaves_en_windows", lambda: True), \
                mock.patch.object(omni.subprocess, "run", correr), \
                contextlib.redirect_stdout(salida):
            codigo = omni.cmd_mcp_setup(_Args())
        return codigo, llamadas, salida.getvalue()

    def test_si_el_doctor_no_pasa_no_se_registra_ni_se_guarda_nada(self):
        codigo, llamadas, salida = self._mcp_setup({"doctor": 1})
        self.assertEqual(codigo, 1)
        self.assertEqual(len(llamadas), 1, "solo el doctor: Claude Code ni se toca")
        self.assertFalse(omni._ruta_env(omni.RUTA_GLOBAL).exists())
        self.assertIn("no la de tu CRM", salida)

    def test_un_mcp_viejo_se_reemplaza_en_vez_de_fallar(self):
        codigo, llamadas, salida = self._mcp_setup({"remove": 0})
        self.assertEqual(codigo, 0)
        self.assertEqual([("remove" if "remove" in c else "add" if "add" in c else "doctor")
                          for c in llamadas], ["doctor", "remove", "add"])
        self.assertIn("lo reemplazo", salida)
        globales = omni._leer_archivo(omni._ruta_env(omni.RUTA_GLOBAL))
        self.assertEqual(globales["COOLIFY_BASE_URL"], "https://coolify.ejemplo.mx")

    def test_sin_mcp_previo_tampoco_avisa_de_reemplazo(self):
        codigo, _, salida = self._mcp_setup({"remove": 1})
        self.assertEqual(codigo, 0)
        self.assertNotIn("lo reemplazo", salida)

    def test_un_token_suelto_en_la_terminal_no_se_mezcla_con_la_url_guardada(self):
        from unittest import mock

        omni._escribir_env(omni.RUTA_GLOBAL, {"COOLIFY_BASE_URL": "https://coolify.nuevo.mx/",
                                              "COOLIFY_TOKEN": "tok_guardado"})
        args = _Args(instance="crm.ejemplo.mx", coolify_url=None)
        with mock.patch.dict(os.environ, {"COOLIFY_ACCESS_TOKEN": "tok_viejo"}, clear=False):
            os.environ.pop("COOLIFY_BASE_URL", None)
            base, token, origen = omni._credenciales_coolify(args)
        self.assertEqual((base, token), ("https://coolify.nuevo.mx", "tok_guardado"))
        self.assertIn("guardado", origen)

    def test_un_par_completo_en_la_terminal_manda_sobre_el_guardado(self):
        from unittest import mock

        omni._escribir_env(omni.RUTA_GLOBAL, {"COOLIFY_BASE_URL": "https://coolify.nuevo.mx",
                                              "COOLIFY_TOKEN": "tok_guardado"})
        args = _Args(instance="crm.ejemplo.mx", coolify_url=None)
        with mock.patch.dict(os.environ, {"COOLIFY_ACCESS_TOKEN": "tok_terminal",
                                          "COOLIFY_BASE_URL": "https://coolify.otro.mx"}):
            base, token, _ = omni._credenciales_coolify(args)
        self.assertEqual((base, token), ("https://coolify.otro.mx", "tok_terminal"))

    def test_un_401_se_dice_como_401_y_no_como_service_perdido(self):
        import contextlib
        import io
        from unittest import mock

        omni._escribir_env(omni.RUTA_GLOBAL, {"COOLIFY_BASE_URL": "https://coolify.nuevo.mx",
                                              "COOLIFY_TOKEN": "tok_caducado"})
        salida = io.StringIO()
        with mock.patch.object(omni, "_api_coolify", lambda *a, **k: (401, {"error": "Unauthenticated."})), \
                mock.patch.dict(os.environ, {}, clear=False), \
                contextlib.redirect_stdout(salida):
            os.environ.pop("COOLIFY_ACCESS_TOKEN", None)
            servicio = omni._servicio_de(_Args(instance="crm.ejemplo.mx", coolify_url=None,
                                               service=None), "crm.ejemplo.mx")
        self.assertIsNone(servicio)
        self.assertIn("HTTP 401", salida.getvalue())
        self.assertIn("rechazó el token", salida.getvalue())
        self.assertNotIn("no encuentro un único service", salida.getvalue())


class ElComposeDeCoolifyEsDeUnClic(unittest.TestCase):
    """La instalación en un clic: se pega el compose en Coolify y no se teclea nada.

    Una variable con `:?` hace que Coolify la cree con el texto del error como
    valor y el despliegue arranque con eso; y un secreto que no sale de una
    variable mágica obliga a inventarlo a mano.
    """

    def _compose(self) -> str:
        return (omni.RAIZ / "deploy" / "coolify.compose.yml").read_text(encoding="utf-8")

    def test_no_pide_ninguna_variable_a_mano(self):
        self.assertNotIn(":?", self._compose())

    def test_los_secretos_los_genera_coolify(self):
        compose = self._compose()
        for secreto, magica in (
            ("POSTGRES_PASSWORD", "SERVICE_PASSWORD_POSTGRES"),
            ("JWT_SECRET", "SERVICE_PASSWORD_64_JWT"),
            ("ENCRYPTION_KEY", "SERVICE_PASSWORD_64_CIFRADO"),
            ("SETUP_CODE", "SERVICE_PASSWORD_ARRANQUE"),
        ):
            self.assertIn(f"{secreto}: ${{{magica}}}", compose, secreto)

    def test_nunca_instala_latest(self):
        """Con `latest` Docker reutiliza la imagen vieja y el despliegue sale en
        verde con el código de antes."""
        self.assertNotIn("APP_VERSION:-latest", self._compose())

    def test_la_version_por_defecto_es_la_del_changelog(self):
        """Quien pega el compose instala esa versión: si no es la última del
        CHANGELOG, arranca una vieja, o una que aún no se ha publicado."""
        import re

        versiones = set(re.findall(r"APP_VERSION:-([^}]+)\}", self._compose()))
        self.assertEqual(versiones, {omni._version_del_repo()})

    def test_solo_trae_imagenes_que_se_publican(self):
        """El compose se pega tal cual. Un servicio cuya imagen no se publica (el
        servidor MCP) había que quitarlo a mano, y en un clic nadie edita el YAML."""
        import re

        propias = [i for i in re.findall(r"^\s*image:\s*(\S+)", self._compose(), re.M)
                   if "crm-omnicanal-imperial" in i]
        self.assertTrue(propias)
        for imagen in propias:
            self.assertRegex(imagen, r"crm-omnicanal-imperial-(backend|frontend):")


class LasVersionesSeLeenBien(unittest.TestCase):
    def test_la_version_por_defecto_es_la_primera_publicada_del_changelog(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "CHANGELOG.md").write_text(
                "# Novedades\n\n## [Sin publicar]\n\n- Aún no.\n\n## [0.11.0]\n\n- Sí.\n",
                encoding="utf-8")
            previa = omni.RAIZ
            omni.RAIZ = Path(tmp)
            try:
                self.assertEqual(omni._version_del_repo(), "0.11.0")
            finally:
                omni.RAIZ = previa

    def test_una_preliminar_va_antes_que_la_final(self):
        self.assertEqual(omni._compara_versiones("1.0.0-a", "1.0.0"), -1)
        self.assertEqual(omni._compara_versiones("0.10.0", "0.9.0"), 1)
        self.assertIsNone(omni._compara_versiones("latest", "0.11.0"))

    def test_el_puerto_de_un_registro_no_es_un_tag(self):
        self.assertEqual(omni._tag("ghcr.io/dueno/crm-backend:0.10.0"), "0.10.0")
        self.assertEqual(omni._tag("localhost:5000/crm-backend"), "latest")
        self.assertEqual(omni._con_tag("localhost:5000/crm-backend:0.10.0", "0.11.0"),
                         "localhost:5000/crm-backend:0.11.0")

    def test_la_restauracion_es_todo_o_nada(self):
        """El DROP del esquema va en la misma transacción que la copia: si algo
        falla, Postgres deshace también el DROP y la base queda como estaba."""
        script = omni._RESTAURAR
        self.assertIn("--single-transaction", script)
        self.assertIn("ON_ERROR_STOP=1", script)
        tirar = script.index("DROP SCHEMA public CASCADE")
        self.assertLess(tirar, script.index("gunzip -c"))
        self.assertLess(script.index("gunzip -c"), script.index("--single-transaction"))


class LosPermisosDelMiembroSoloDejanMirar(unittest.TestCase):
    """`entrega/settings.json` viaja a cada miembro como `.claude/settings.json`.

    El agente lee registros en los que cualquiera escribe (la ruta y el
    navegador de cada visita): lo que corre sin preguntar no puede sacar datos,
    leer llaves ni escribir en Coolify. Salió en el dry-run 2 (SEC-14): había
    `curl`, lectura de todo el disco y herramientas del MCP que crean y borran.
    En la copia del miembro no hay `entrega/`: ahí no se comprueba nada.
    """

    def setUp(self):
        ruta = omni.RAIZ / "entrega" / "settings.json"
        if not ruta.exists():
            self.skipTest("copia entregable: no trae entrega/")
        self.permisos = json.loads(ruta.read_text(encoding="utf-8"))["permissions"]

    def test_nada_que_saque_datos_o_lea_todo_el_disco_corre_sin_preguntar(self):
        for regla in self.permisos["allow"]:
            self.assertNotIn("curl", regla)
            self.assertNotIn("Read(//", regla)
            self.assertNotIn("Read(~", regla)
        self.assertNotIn("Bash(python3 scripts/omni.py:*)", self.permisos["allow"])

    def test_del_mcp_solo_corren_solas_las_herramientas_que_leen(self):
        escriben = ("projects", "environments", "deployment", "get_service", "deploy",
                    "control", "env_vars", "application", "service", "database")
        for regla in self.permisos["allow"]:
            if regla.startswith("mcp__coolify__"):
                self.assertNotIn(regla.removeprefix("mcp__coolify__"), escriben, regla)

    def test_las_llaves_no_se_leen(self):
        for regla in ("Read(~/.omnicanal/**)", "Read(~/.claude.json)", "Read(~/.ssh/**)"):
            self.assertIn(regla, self.permisos["deny"])

    def test_actualizar_y_conectar_piden_permiso(self):
        self.assertIn("Bash(python3 scripts/omni.py update:*)", self.permisos["ask"])
        self.assertIn("Bash(python3 scripts/omni.py connect:*)", self.permisos["ask"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

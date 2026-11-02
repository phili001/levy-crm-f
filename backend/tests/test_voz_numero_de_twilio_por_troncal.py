"""Un número de Twilio (México) llega a Retell por troncal SIP (Fase 8, US-19 AC2).

Twilio va detrás de un transporte falso en memoria que contesta con las rutas
y los nombres de campo de su API REST (Elastic SIP Trunking v1 y la API
2010-04-01). Lo que se comprueba es lo que deja el teléfono mudo si falla: que
la troncal mande las llamadas a Retell, que el número cuelgue de ella, que se
pueda repetir sin duplicar nada y que no se toque una troncal ajena.

Sin base de datos.
"""
from __future__ import annotations

import base64
import itertools
import re
from urllib.parse import parse_qs

import httpx
import pytest

from app.providers.voice import twilio_sip as ts

SID, TOKEN = "AC" + "1" * 32, "token-de-pruebas-123"
MX = "+529981234567"


class _TwilioFalso:
    def __init__(self) -> None:
        self.numeros: dict[str, dict] = {MX: {"sid": "PN1", "phone_number": MX, "trunk_sid": None}}
        self.troncales: dict[str, dict] = {}
        self.originaciones: dict[str, list[dict]] = {}
        self.acls: dict[str, dict] = {}
        self.acls_de_troncal: dict[str, list[str]] = {}
        self.dominios_de_otros = {"sofia.pstn.twilio.com"}
        self.escrituras: list[str] = []
        self._ids = itertools.count(1)

    # ------------------------------------------------------------- estado
    def troncal(self, dominio: str, originaciones: list[str]) -> str:
        sid = f"TK{next(self._ids)}"
        self.troncales[sid] = {"sid": sid, "domain_name": dominio, "friendly_name": dominio}
        self.originaciones[sid] = [
            {"sid": f"OU{next(self._ids)}", "sip_url": u, "enabled": True} for u in originaciones
        ]
        return sid

    def transporte(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._contestar)

    # ------------------------------------------------------------- HTTP
    def _contestar(self, req: httpx.Request) -> httpx.Response:
        esperado = "Basic " + base64.b64encode(f"{SID}:{TOKEN}".encode()).decode()
        if req.headers.get("authorization") != esperado:
            return httpx.Response(401, json={"code": 20003, "message": "Authenticate"})
        datos = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        if req.method == "POST":
            self.escrituras.append(req.url.path)
        ruta, metodo = req.url.path, req.method

        if m := re.fullmatch(rf"/2010-04-01/Accounts/{SID}/IncomingPhoneNumbers\.json", ruta):
            n = self.numeros.get(req.url.params.get("PhoneNumber"))
            return httpx.Response(200, json={"incoming_phone_numbers": [n] if n else []})
        if m := re.fullmatch(rf"/2010-04-01/Accounts/{SID}/IncomingPhoneNumbers/(\w+)\.json", ruta):
            n = next(n for n in self.numeros.values() if n["sid"] == m[1])
            n["trunk_sid"] = datos["TrunkSid"]
            return httpx.Response(200, json=n)
        if ruta == "/v1/Trunks":
            if metodo == "GET":
                return httpx.Response(200, json={"trunks": list(self.troncales.values())})
            if datos["DomainName"] in self.dominios_de_otros:
                return httpx.Response(
                    400, json={"code": 21248, "message": "Domain name is already in use"}
                )
            sid = self.troncal(datos["DomainName"], [])
            return httpx.Response(201, json=self.troncales[sid])
        if m := re.fullmatch(r"/v1/Trunks/(\w+)", ruta):
            troncal = self.troncales[m[1]]
            if metodo == "POST":
                troncal["transfer_mode"] = datos.get("TransferMode", troncal.get("transfer_mode"))
                troncal["transfer_caller_id"] = datos.get(
                    "TransferCallerId", troncal.get("transfer_caller_id")
                )
            return httpx.Response(200, json=troncal)
        if m := re.fullmatch(r"/v1/Trunks/(\w+)/OriginationUrls", ruta):
            if metodo == "GET":
                return httpx.Response(200, json={"origination_urls": self.originaciones[m[1]]})
            nueva = {"sid": f"OU{next(self._ids)}", "sip_url": datos["SipUrl"],
                     "enabled": datos["Enabled"] == "true"}
            self.originaciones[m[1]].append(nueva)
            return httpx.Response(201, json=nueva)
        if m := re.fullmatch(r"/v1/Trunks/(\w+)/OriginationUrls/(\w+)", ruta):
            o = next(o for o in self.originaciones[m[1]] if o["sid"] == m[2])
            o["enabled"] = datos["Enabled"] == "true"
            return httpx.Response(200, json=o)
        if ruta == f"/2010-04-01/Accounts/{SID}/SIP/IpAccessControlLists.json":
            if metodo == "GET":
                return httpx.Response(200, json={"ip_access_control_lists": list(self.acls.values())})
            sid = f"AL{next(self._ids)}"
            self.acls[sid] = {"sid": sid, "friendly_name": datos["FriendlyName"], "ips": []}
            return httpx.Response(201, json=self.acls[sid])
        if m := re.fullmatch(
            rf"/2010-04-01/Accounts/{SID}/SIP/IpAccessControlLists/(\w+)/IpAddresses\.json", ruta
        ):
            if metodo == "GET":
                return httpx.Response(200, json={"ip_addresses": self.acls[m[1]]["ips"]})
            ip = {"ip_address": datos["IpAddress"], "cidr_prefix_length": int(datos["CidrPrefixLength"])}
            self.acls[m[1]]["ips"].append(ip)
            return httpx.Response(201, json=ip)
        if m := re.fullmatch(r"/v1/Trunks/(\w+)/IpAccessControlLists", ruta):
            colgadas = self.acls_de_troncal.setdefault(m[1], [])
            if metodo == "GET":
                return httpx.Response(
                    200, json={"ip_access_control_lists": [self.acls[s] for s in colgadas]}
                )
            if datos["IpAccessControlListSid"] in colgadas:
                return httpx.Response(400, json={"code": 21231, "message": "Already associated"})
            colgadas.append(datos["IpAccessControlListSid"])
            return httpx.Response(201, json=self.acls[datos["IpAccessControlListSid"]])
        return httpx.Response(404, json={"message": f"ruta no simulada: {metodo} {ruta}"})


@pytest.fixture
def twilio():
    falso = _TwilioFalso()
    return falso, ts.TwilioSip(SID, TOKEN, transport=falso.transporte())


async def _conectar(cliente, prefijo="crm-clinica-mx"):
    return await ts.conectar_numero(cliente, telefono=MX, prefijo=prefijo, nombre="Sofía")


async def test_un_numero_sin_troncal_queda_en_una_troncal_nueva_que_manda_a_retell(twilio):
    falso, cliente = twilio
    lista = await _conectar(cliente)
    assert lista.creada and lista.movido
    assert lista.dominio == "crm-clinica-mx.pstn.twilio.com"
    assert falso.numeros[MX]["trunk_sid"] == lista.trunk_sid
    assert [o["sip_url"] for o in falso.originaciones[lista.trunk_sid]] == ["sip:sip.retellai.com"]
    (acl,) = falso.acls.values()
    assert {f"{i['ip_address']}/{i['cidr_prefix_length']}" for i in acl["ips"]} == set(
        ts.RETELL_SIP_CIDRS
    )
    assert falso.acls_de_troncal[lista.trunk_sid] == [acl["sid"]]
    troncal = falso.troncales[lista.trunk_sid]
    assert troncal["transfer_mode"] == "enable-all", "sin esto Sofía no puede pasar la llamada"
    assert troncal["transfer_caller_id"] == "from-transferee", "recepción ve quién llama"
    assert all(c.ok for c in await ts.verificar(cliente, MX))


async def test_repetirlo_no_duplica_nada(twilio):
    falso, cliente = twilio
    await _conectar(cliente)
    antes = len(falso.escrituras)
    otra = await _conectar(cliente)
    assert not otra.creada and not otra.movido
    assert len(falso.escrituras) == antes, falso.escrituras[antes:]
    assert len(falso.troncales) == 1


async def test_si_el_numero_ya_va_a_retell_por_otra_troncal_se_usa_esa(twilio):
    falso, cliente = twilio
    curso = falso.troncal("sofia-voz.pstn.twilio.com", ["sip:sip.retellai.com"])
    falso.numeros[MX]["trunk_sid"] = curso
    lista = await _conectar(cliente)
    assert lista.trunk_sid == curso
    assert not lista.creada and not lista.movido
    assert len(falso.troncales) == 1, "no hace falta otra troncal"
    assert falso.troncales[curso]["transfer_mode"] == "enable-all"


async def test_una_troncal_que_manda_a_otro_sitio_no_se_toca(twilio):
    falso, cliente = twilio
    ajena = falso.troncal("otro-proveedor.pstn.twilio.com", ["sip:otro.ejemplo.com"])
    falso.numeros[MX]["trunk_sid"] = ajena
    lista = await _conectar(cliente)
    assert lista.trunk_sid != ajena and lista.movido
    assert [o["sip_url"] for o in falso.originaciones[ajena]] == ["sip:otro.ejemplo.com"], (
        "sumarle Retell a una troncal ajena repartiría sus llamadas entre los dos"
    )
    assert falso.numeros[MX]["trunk_sid"] == lista.trunk_sid


async def test_una_originacion_apagada_se_vuelve_a_encender(twilio):
    falso, cliente = twilio
    lista = await _conectar(cliente)
    falso.originaciones[lista.trunk_sid][0]["enabled"] = False
    assert not {c.nombre: c.ok for c in await ts.verificar(cliente, MX)}["entrada_hacia_retell"]
    await _conectar(cliente)
    assert falso.originaciones[lista.trunk_sid][0]["enabled"] is True


async def test_un_numero_que_no_esta_en_la_cuenta_se_explica(twilio):
    falso, cliente = twilio
    del falso.numeros[MX]
    with pytest.raises(ts.TwilioSipError, match="no está en esta cuenta de Twilio"):
        await _conectar(cliente)


async def test_un_dominio_que_ya_usa_otra_cuenta_se_explica(twilio):
    _, cliente = twilio
    with pytest.raises(ts.TwilioSipError, match="ya lo usa otra cuenta de Twilio"):
        await _conectar(cliente, prefijo="sofia")


async def test_una_credencial_mala_se_explica(twilio):
    falso, _ = twilio
    malo = ts.TwilioSip(SID, "otro-token-cualquiera", transport=falso.transporte())
    with pytest.raises(ts.TwilioSipError) as e:
        await _conectar(malo)
    assert e.value.status == 401
    assert "Auth Token" in str(e.value)


def test_el_prefijo_de_la_troncal_sale_del_dominio_de_la_instalacion():
    assert ts.prefijo_para("crm.Clinica-Dental.mx") == "crm-clinica-dental-mx"
    assert ts.prefijo_para("") == "sofia-voz"

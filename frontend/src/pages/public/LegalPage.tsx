import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { useMarca } from "@/store/apariencia";
import { getLegal, type Legal } from "@/services/legal";

/**
 * Páginas públicas /privacidad y /terminos (Fase 7, T-7.3.6).
 *
 * Meta pide una URL de privacidad para los canales de mensajería, y la ley
 * mexicana de datos personales obliga a decir quién trata los datos, para qué
 * y cómo ejercer los derechos ARCO. El texto es una plantilla: los datos que
 * cambian de una clínica a otra (responsable, domicilio, correo) se rellenan
 * en Preferencias → Aviso de privacidad y términos. Mientras falten, la página lo dice en vez
 * de publicar un aviso incompleto.
 */

type Tipo = "privacidad" | "terminos";

function Hueco({ valor, falta }: { valor: string; falta: string }) {
  return valor ? <>{valor}</> : <span className="text-state-warn">[{falta}]</span>;
}

function Privacidad({ d, nombre }: { d: Legal; nombre: string }) {
  return (
    <>
      <p>
        <b><Hueco valor={d.responsable} falta="responsable pendiente" /></b>, con domicilio en{" "}
        <Hueco valor={d.domicilio} falta="domicilio pendiente" />, es responsable del tratamiento de
        los datos personales que nos compartes cuando escribes o llamas a {nombre} por WhatsApp,
        Instagram, Messenger, correo electrónico, el chat de nuestra página web o por teléfono.
      </p>

      <h2>Qué datos tratamos</h2>
      <p>
        Tu nombre, teléfono, correo electrónico, usuario de redes sociales, el contenido de los
        mensajes y, si llamas, la grabación y la transcripción de la llamada. Si nos cuentas algo de
        tu salud para agendar o preparar una cita, lo tratamos como dato sensible y solo para
        atenderte.
      </p>

      <h2>Para qué los usamos</h2>
      <ul>
        <li>Responder tus preguntas y agendar, confirmar o cambiar tus citas.</li>
        <li>Dar seguimiento a tu atención y mantener el historial de la conversación.</li>
        <li>Mejorar la calidad de la atención (revisión de conversaciones por nuestro equipo).</li>
      </ul>
      <p>
        Parte de la atención la da un asistente virtual con inteligencia artificial, supervisado por
        personas del equipo. Siempre puedes pedir hablar con una persona.
      </p>

      <h2>Con quién se comparten</h2>
      <p>
        Solo con los proveedores que hacen posible el servicio, que actúan por cuenta nuestra: la
        plataforma de mensajería de WhatsApp; Zernio (Zernio Software SL, con sede en España), por
        donde pasan los mensajes de Instagram y Messenger; el proveedor de inteligencia artificial
        que redacta las respuestas; y el de telefonía que atiende las llamadas. No vendemos ni
        cedemos tus datos a terceros para fines propios de ellos.
      </p>

      <h2>Tus derechos (ARCO)</h2>
      <p>
        Puedes acceder a tus datos, rectificarlos, cancelarlos u oponerte a su uso, y revocar tu
        consentimiento, escribiendo a <Hueco valor={d.correo} falta="correo pendiente" />
        {d.telefono ? <> o llamando al {d.telefono}</> : null}. Indica tu nombre, el medio por el
        que nos contactaste y lo que solicitas; te responderemos en los plazos que marca la Ley
        Federal de Protección de Datos Personales en Posesión de los Particulares.
      </p>

      <h2>Cambios a este aviso</h2>
      <p>Cualquier cambio se publicará en esta misma página.</p>
    </>
  );
}

function Terminos({ d, nombre }: { d: Legal; nombre: string }) {
  return (
    <>
      <p>
        Estos términos aplican a la atención por mensajes y llamadas de {nombre} (
        <Hueco valor={d.responsable} falta="responsable pendiente" />).
      </p>
      <h2>El asistente virtual</h2>
      <p>
        Un asistente con inteligencia artificial responde dudas generales y ayuda a agendar citas.
        Su información es orientativa: no es un diagnóstico ni sustituye la valoración de un
        profesional. Los precios que menciona son aproximados y se confirman en la clínica.
      </p>
      <h2>Citas</h2>
      <p>
        Una cita queda confirmada cuando la confirmamos por el mismo medio por el que la pediste.
        Si no puedes asistir, avísanos con la mayor anticipación posible.
      </p>
      <h2>Urgencias</h2>
      <p>
        Este canal no es para urgencias médicas. Ante una urgencia, acude al servicio de urgencias
        más cercano o llama al 911.
      </p>
      <h2>Datos personales</h2>
      <p>
        El tratamiento de tus datos se rige por nuestro{" "}
        <Link to="/privacidad" className="underline">aviso de privacidad</Link>.
      </p>
      <h2>Contacto</h2>
      <p>
        <Hueco valor={d.correo} falta="correo pendiente" />
        {d.telefono ? <> · {d.telefono}</> : null}
      </p>
    </>
  );
}

export default function LegalPage({ tipo }: { tipo: Tipo }) {
  const { nombre: marca } = useMarca();
  const [datos, setDatos] = useState<Legal | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    getLegal()
      .then(setDatos)
      .catch(() => setError(true));
  }, []);

  const nombre = datos?.nombre || marca || "la clínica";
  const titulo = tipo === "privacidad" ? "Aviso de privacidad" : "Términos de uso";

  return (
    <div className="min-h-full bg-paper text-ink">
      <header className="border-b border-line bg-paper2">
        <div className="max-w-3xl mx-auto px-4 py-4 flex items-center justify-between gap-4">
          <div className="font-semibold truncate">{nombre}</div>
          <nav className="text-xs text-ink3 flex gap-3">
            <Link to="/privacidad" className="hover:text-ink">Privacidad</Link>
            <Link to="/terminos" className="hover:text-ink">Términos</Link>
          </nav>
        </div>
      </header>
      <main className="max-w-3xl mx-auto px-4 py-10 space-y-4 text-ink2 leading-relaxed [&_h2]:text-lg [&_h2]:font-semibold [&_h2]:text-ink [&_h2]:pt-4 [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:space-y-1">
        <h1 className="text-2xl font-semibold text-ink">{titulo}</h1>
        {error && <p>No se pudo cargar esta página. Vuelve a intentarlo en un momento.</p>}
        {datos && !datos.completo && (
          <p className="text-sm text-state-warn bg-state-warn/10 border border-state-warn/30 rounded-coro-sm px-3 py-2">
            Faltan datos de la clínica en este aviso. Si administras la clínica, complétalos en
            Preferencias → Aviso de privacidad y términos.
          </p>
        )}
        {datos && (tipo === "privacidad" ? <Privacidad d={datos} nombre={nombre} /> : <Terminos d={datos} nombre={nombre} />)}
        {datos?.actualizado && (
          <p className="text-xs text-ink3 pt-6">Última actualización: {datos.actualizado}</p>
        )}
      </main>
    </div>
  );
}

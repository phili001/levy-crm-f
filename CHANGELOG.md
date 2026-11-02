# Novedades

Todas las novedades de esta instalación, de lo más reciente a lo más antiguo.
El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y
las versiones, [Versionado Semántico](https://semver.org/lang/es/).

Se lee desde el panel, en **Plataforma → Cambios**: el servidor sirve este mismo
archivo, así que lo que escribas aquí es lo que ve quien usa el CRM. Escríbelo
para esa persona, contando qué cambia en su día a día, no qué función se tocó.

Vive en `backend/` y no en la raíz porque tiene que entrar en la imagen del
backend, cuyo contexto de construcción es esa carpeta. En la raíz queda un
enlace a este mismo archivo, que es donde la gente lo busca.

## [1.3.3]

Tres arreglos de privacidad que salieron de una revisión de seguridad que hizo
un miembro de la comunidad.

### Corregido
- **Borrar a un paciente ya borra su cita en Google Calendar.** Con Google
  Calendar conectado, sus citas se quedaban allí con su nombre y el servicio.
  Ahora se borran también. Si Google no contesta en ese momento, el CRM lo
  reintenta cada 10 minutos hasta conseguirlo, y en Auditoría queda cuándo
  terminó.
- **Borrar a un paciente ya borra los archivos que mandó por correo.** Con el
  correo conectado por IMAP (contraseña de aplicación), los PDF e imágenes que
  venían adjuntos se quedaban en el servidor aunque borraras al paciente, y la
  limpieza automática de adjuntos (180 días) tampoco los recogía. Ahora entran
  en las dos.
- **La cuenta de revisor de Meta ya solo ve Instagram, también en tiempo
  real.** Con la bandeja abierta, a esa cuenta le llegaban los avisos de los
  mensajes de WhatsApp, con el teléfono del cliente, aunque no pudiera abrir
  esas conversaciones. Ahora solo le llega lo de Instagram, y los avisos de la
  bandeja ya no llevan el teléfono ni el correo de nadie.

## [1.3.2]

Un detalle del panel que salió al probar los recordatorios en el CRM del curso.

### Corregido
- **Encender y apagar ya se ve como un interruptor.** En Seguimientos,
  Preferencias → Módulos, la transcripción de notas de voz (Rendimiento) y la
  demo pública del chat web, el «Apagado» era una etiqueta que no parecía un
  botón y no se adivinaba que había que pulsarla. Ahora es un interruptor con
  su palanca, y al lado sigue diciendo si está encendido o apagado.

## [1.3.1]

Arreglos de los comandos que corre Claude Code en tu computadora (`omni.py`). El
panel y Sofía no cambian.

### Corregido
- **Volver a conectar Coolify ya no se atora.** Si en «conectar Coolify»
  (`omni.py mcp-setup`) pegabas una dirección equivocada (por ejemplo, la de tu
  CRM en vez de la de tu panel de Coolify), quedaba guardada y el siguiente
  intento fallaba con «already exists». Ahora primero comprueba la dirección y
  el token, y si no responden no guarda nada; si ya habías conectado otro
  Coolify, lo reemplaza.
- **Si Coolify no acepta el token, te lo dice.** Al actualizar con un token
  caducado (o con uno viejo que se quedó en tu terminal) el mensaje decía «no
  encuentro un único service». Ahora dice que Coolify rechazó el token, cuál se
  usó y cómo guardar el nuevo. Y la dirección y el token se usan siempre en
  pareja: un token viejo en la terminal ya no se mezcla con la dirección
  guardada.

## [1.3.0]

La más votada de la primera encuesta de la comunidad: recordatorios y
seguimientos. Todo viene **apagado**: lo enciendes en Asistente → Seguimientos.

### Nuevo
- **Plantillas de WhatsApp desde el panel.** En Canales → Plantillas ves las de
  tu cuenta con su estado en Meta y, si la rechazó, por qué. «Nueva» abre un
  editor con vista previa como la ve el paciente; «Generar con IA» la escribe
  entera a partir de una frase, y «Enviar a aprobación» la manda a revisión por
  YCloud (Meta tarda de minutos a 24 h). En «Sugeridas» están listas las que
  usan los recordatorios: recordatorio de cita, cita perdida, cita agendada y
  seguimiento. Una rechazada se corrige con «Corregir», que crea la versión
  `_v2`. Solo con YCloud; con la API de Meta se siguen creando en Meta.
- **Sofía le recuerda la cita al paciente.** Sale sola antes de la cita (24 h por
  defecto) con la plantilla que elijas. Si el paciente toca «Confirmo», Sofía la
  marca como confirmada y la Agenda lo enseña con «✓»; si pide cambiarla, se la
  mueve.
- **Si no llegó, Sofía le escribe.** Al marcar «No asistió» en la Agenda, le
  escribe para buscarle otro horario. En Chats → Para hacer, «Citas de hoy»
  tiene ahora «Vino» y «No vino» en las citas que ya pasaron, para marcarlas sin
  entrar a la Agenda.
- **Sofía retoma a quien preguntó y no agendó.** Si alguien preguntó por un
  precio o un tratamiento, Sofía le contestó y no volvió a escribir, a las 20 h
  Sofía relee la conversación y decide si le escribe un mensaje corto para
  retomarla. A quien solo pidió la dirección o dijo que no le interesa no le
  escribe. Una vez por conversación y dentro de las 24 h, así que no necesita
  plantilla.
- **Siempre con frenos.** Nunca de noche (8:00 a 20:00, lo cambias), nunca a
  quien pidió la baja o está bloqueado, nunca con el canal en pausa o en
  Entrenamiento y una sola vez por cita. Lo que sale queda en la conversación,
  en «Últimos avisos» de Seguimientos y en la ficha; si algo no salió, la Agenda
  y la ficha dicen por qué.

### Corregido
- **Tocar un botón de una plantilla ya le llega a Sofía.** Con YCloud, cuando el
  paciente tocaba un botón de respuesta rápida, el mensaje llegaba vacío.

## [1.2.2]

### Corregido
- **El equipo ya no ve «Conocimiento» en el menú.** Con el rol Cliente, la
  entrada salía en el menú pero la pantalla no cargaba nada: la base de
  conocimiento es solo del administrador, también para leerla, porque ahí van
  los precios y los procesos internos. Ahora no aparece, y un enlace guardado te
  regresa a Chats.
- **«Errores» de Asistente → Actividad cuenta los fallos.** Se quedaba siempre en
  0. Ahora suma cada vez que un fallo deja al paciente sin respuesta: el modelo
  no contesta, un envío se rechaza o n8n no responde. El detalle sigue en
  Plataforma → Registros.
- **Auditoría dice quién hizo cada cambio.** En Plataforma → Registros →
  Auditoría, la columna nueva «Quién» trae el nombre de la persona, el token que
  lo hizo (por ejemplo, Claude conectado por MCP) o «Sistema».
- **Respaldos dice la hora de tu negocio.** Las copias diarias ya corrían a las
  04:00 en la zona de tu instalación, pero el panel decía «Madrid». Ahora enseña
  la zona de verdad, por ejemplo «hora de Cancun».

## [1.2.1]

### Corregido
- **Las opciones de una herramienta de n8n se pueden teclear con comas.** En
  Conexiones → n8n → la herramienta → «Datos que pide», con un dato de tipo
  «Opciones», la coma se borraba al escribirla, y «blanqueamiento, ortodoncia,
  implantes» acababa guardado como una sola opción. Pegado sí funcionaba. Si
  diste de alta una herramienta tecleando las opciones, ábrela con «Editar» y
  revisa que salgan separadas.

## [1.2.0]

### Nuevo
- **Conecta Claude a tu CRM por MCP.** Claude Code (o cualquier asistente que
  hable MCP) puede consultar tu CRM y trabajar en él sin que entres al panel:
  cómo van las conversaciones, cuánto llevas gastado en IA, si algún canal
  está parado, buscar y corregir la base de conocimiento o ajustar las
  instrucciones de Sofía. Cómo: Conexiones → API / MCP → crea un token con lo
  que le quieras dejar hacer → en «Conectar por MCP» copia el comando y pégalo
  en tu terminal. La dirección es la de tu propio CRM terminada en `/mcp`; no
  hay nada más que instalar. Hasta ahora esa tarjeta enseñaba una dirección que
  no contestaba.
- **Claude solo hace lo que el token le deja.** Un token de solo lectura no
  puede cambiar nada, y todo lo que se cambie por esta vía queda en Auditoría
  con el nombre del token. Si dejas de usarlo, revócalo y deja de funcionar al
  momento.

### Cambiado
- **El comando de conexión registra el CRM como «crm»** (antes «chatbot»). Si
  ya lo habías añadido con el nombre viejo, quítalo con
  `claude mcp remove chatbot` y vuelve a copiar el comando del panel.

## [1.1.3]

### Corregido
- **«Probar» un agente en n8n vuelve a mostrar la respuesta.** En Asistentes →
  el agente → Motor, «Probar» salía siempre «sin respuesta» si tu flujo busca
  en la base de conocimiento del CRM (como «Sofía en n8n»): la prueba no le
  decía al flujo la dirección del CRM y la búsqueda fallaba. Los mensajes
  reales de los pacientes no se veían afectados. Si un flujo sale sin
  respuesta, el aviso ahora te dice que mires en n8n → Executions qué nodo
  quedó en rojo.

## [1.1.2]

### Corregido
- **Los archivos por WhatsApp vuelven a salir.** Con YCloud, mandar una foto,
  un documento o un audio por WhatsApp fallaba siempre: la foto de una
  herramienta de n8n (Sofía decía «falló el envío de la imagen»), los adjuntos
  que mandas desde el inbox y la imagen de la cabecera de una difusión. El CRM
  subía el archivo a una dirección de YCloud que no existe. Los mensajes de
  texto no se veían afectados.

## [1.1.1]

### Corregido
- **`/actualizar` funciona desde Windows.** Desde una computadora con Windows,
  actualizar decía «no puedo entrar por SSH» aunque la llave estuviera bien
  puesta: los comandos le llegaban al servidor con un carácter de más al final
  de cada línea. Pasaba lo mismo al volver a la versión anterior y con las
  copias de la base. En Mac y Linux no pasaba.

### Nuevo
- **Tres flujos de n8n listos para importar**, en `docs/flujos-n8n/`: mandar la
  foto de un tratamiento, consultar un precio en tu hoja de Google y «Sofía en
  n8n». Cada uno trae notas con lo que hay que cambiar.

### Cambiado
- **Los avisos de n8n dicen «Publish»**, que es como n8n llama ahora a activar
  un flujo.

## [1.1.0]

n8n con Sofía: tus flujos de n8n como herramientas de Sofía, o como su cerebro.
La guía para armarlos está en `docs/n8n.md`.

### Nuevo
- **Herramientas de n8n para Sofía.** En Conexiones → n8n das de alta un flujo
  tuyo como herramienta: buscar un pedido, mandar la foto de un tratamiento,
  apuntar algo en una hoja… Sofía la usa cuando toca y, si el flujo devuelve
  imágenes, audios, videos o documentos, se los manda al paciente por WhatsApp,
  Messenger, Instagram (sin documentos) y el chat de la web. Se activa en cada
  agente, en Tools disponibles (grupo «De n8n»), y se prueba desde el panel sin
  escribirle a nadie.
- **«Sofía en n8n».** Un agente de texto puede pensar en un flujo de n8n:
  Asistentes → el agente → Motor → «Un flujo de n8n». Cada mensaje del paciente
  le llega a tu flujo y lo que conteste es lo que sale, con el formato de cada
  canal, el modo Entrenamiento y las reglas del correo. Tu flujo también puede
  pasar la conversación a una persona, cambiar la etapa, las etiquetas o el
  nombre de la ficha y contestar más tarde. Si n8n falla o no contesta a tiempo,
  la conversación pasa a una persona. Las llamadas siguen con Sofía.
- **Un token «Para n8n».** En Conexiones → API / MCP, el atajo «Para n8n» crea
  un token que puede leer y contestar conversaciones y cambiar fichas, para que
  tu flujo conteste más tarde. Los demás tokens siguen sin ver mensajes.
- **En el inbox se ve lo que vino de n8n:** una marca «n8n» junto a «Bot», y en
  Actividad del agente una línea por turno con cuánto tardó n8n y cómo contestó.

### Corregido
- **«Probar aviso» de Zernio ya no falla.** Zernio decía «Test webhook failed»
  aunque la conexión estaba bien; ahora la prueba sale en verde. Los mensajes de
  Instagram y Messenger no cambian.

## [1.0.0]

La versión del curso. Junta lo que salió al grabar los primeros videos.

### Corregido
- **La base de conocimiento encuentra lo que se parece.** En las instalaciones
  nuevas, la búsqueda por significado perdía casi todo: «Probar» devolvía uno de
  cinco fragmentos parecidos, o ninguno, y Sofía contestaba sin la información
  que tenía. Al actualizar se cambia el índice de la búsqueda y no hay que hacer
  nada más.
- **Subir el tope de gasto vuelve a encender a Sofía.** Si el tope la había
  apagado, ahora basta con subirlo en Asistentes → Tarifas y límites (o con que
  empiece el mes), como decía el aviso. Si la pausaste tú, se queda pausada.
- **Conectar WhatsApp avisa si YCloud no activó un evento.** Pasó con el de los
  mensajes que contestas desde el celular: el canal salía en verde y esos
  mensajes no llegaban al inbox. La tarjeta de WhatsApp lo dice, y después de
  marcarlo a mano en YCloud se puede «Volver a comprobar».
- **Los correos de Sofía llegan con el nombre de tu clínica**, el de
  Apariencia, y no con «CRM Omnicanal».
- **El código del chat para tu web sale con tu título, tu saludo y tu color**,
  los mismos que ves en `/demo`.

### Cambiado
- **`/demo` se enciende y se apaga desde Conexiones → Chat en tu web.** Antes
  solo la encendía la instalación.
- **«Probar» en la base de conocimiento dice qué pasa:** si falta la clave de
  OpenAI, si OpenAI no responde o si no encuentra nada.
- **Los textos del panel llevan a donde toca.** El paso de la clave de IA abre
  Proveedores LLM, el checklist dice que los canales nacen en Activo y las
  rutas dicen Preferencias y Asistentes, que es como se llaman en el menú.
- **La ayuda de Zernio usa los nombres de su panel de hoy:** Connections, y
  API keys → Create API key.
- **Ejemplos de México** en lugar de España: teléfonos, la firma del correo y
  la nota de la base de conocimiento. Y dice «el borrador», no «la borrador».

## [0.13.3]

### Cambiado
- **Por teléfono, Sofía saluda por su nombre a quien ya conoce y le pregunta por
  su cita.** Si llama alguien con ficha y nombre, el saludo empieza con «Hola,
  Carlos.» y, si tiene una cita próxima, termina con «¿Llamas por tu cita de
  mañana a las cuatro?». El aviso de grabación no cambia, y si no se sabe quién
  llama el saludo es el de siempre.

## [0.13.2]

### Corregido
- **Lo que un paciente escribe después de llamar ya no se pierde en la
  conversación de la llamada.** El CRM guardaba cada mensaje en la última
  conversación abierta de la ficha, fuera del canal que fuera: tras una llamada,
  lo que la persona escribía por WhatsApp (o por Instagram después de unir sus
  fichas) caía en la conversación de voz y Sofía no contestaba. Ahora cada canal
  sigue en su propia conversación.
- **El chat de la web ya no enseña las sugerencias de Entrenamiento.** Al
  recargar, el visitante veía la respuesta que Sofía dejaba para revisar, aunque
  nadie la hubiera enviado.
- **Un dato sin rellenar ya no le llega al paciente.** Si el teléfono o la
  dirección de la clínica seguían como «{{PENDIENTE…}}», Sofía podía copiarlo tal
  cual. Ahora dice que ese dato lo confirma una persona del equipo.
- **Los registros enseñan entero el id de cada conversación** (el filtro de
  teléfonos tapaba sus últimos dígitos) y el worker arranca sin el aviso de
  Celery que parecía un error.

### Cambiado
- **En las instalaciones nuevas, Sofía no confirma tratamientos que no están en
  su lista ni adelanta posibles causas de un síntoma.** En una instalación que ya
  existe se puede añadir a mano: en Preferencias → «Sobre el negocio» y en el
  prompt del Agente de Texto.

## [0.13.0]

### Seguridad
- **Los registros ya no guardan secretos ni datos de pacientes.** El panel y el
  servidor apuntaban cada dirección completa, y ahí iban el enlace para
  restablecer la contraseña, el acceso al chat de la web y lo que se busca en
  Clientes. El trabajo en segundo plano (mensajes, correos, llamadas) apuntaba
  teléfonos y correos sin tapar, y un error de la base llevaba el texto de la
  consulta. Ahora se apunta solo lo que sirve para encontrar un fallo.
- **El correo del sistema comprueba con quién habla.** Los enlaces para
  restablecer la contraseña salían cifrados, pero sin comprobar el certificado
  del servidor de correo.
- **Por teléfono, Sofía ya no lee las notas internas ni el correo del
  paciente.** El número de quien llama se puede falsificar. El nombre y las
  citas sí, que es lo que necesita para atender.
- **Con «Sofía envía sola», un correo que pide contestar a otra dirección queda
  de borrador.** Así llegaría uno que se hace pasar por un paciente para
  llevarse sus datos. En Registros se ve por qué.
- **Librerías al día.** Se cierran los fallos de seguridad conocidos en las
  librerías del servidor y del panel, entre ellas la que limpia el HTML de los
  correos antes de enseñarlos.
- **El asistente de Claude Code que viene con el CRM solo mira sin preguntar.**
  Conectar un canal, actualizar o salir a internet piden tu permiso, y no puede
  leer tus llaves.

### Corregido
- **Un fallo momentáneo de la base ya no hace perder un mensaje de WhatsApp.**
  Si fallaba justo al guardarlo, el reintento de YCloud se tiraba como repetido
  y el mensaje del paciente no llegaba nunca.
- **El arranque sale limpio.** Desaparecen los avisos que salían en cada
  arranque y parecían errores, y el trabajo normal en segundo plano ya no sale
  marcado como aviso en los registros.

## [0.12.3]

### Corregido
- **La comprobación completa ya no da un falso fallo en una instalación nueva.**
  El chat de la web solo acepta su propio dominio, y `verify` lo probaba sin
  decir de dónde venía: el CRM lo rechazaba y `verify` avisaba de un fallo que
  no existía, también al final de `/actualizar`. Ahora lo prueba como la página
  `/demo`.

## [0.12.2]

### Corregido
- **El instalador funciona en Windows.** `omni.py` se caía al primer ✓ cuando
  lo corría Claude Code en Windows (la salida iba en la codificación de
  Windows), `mcp-setup` no encontraba `npx`, y `update` no encontraba la versión
  porque en Windows el `CHANGELOG.md` de la raíz llega como un texto con una
  ruta. La revisión automática lo corre ahora también en un Windows de verdad.
- **Dos reservas del mismo horario a la vez ya no acaban en error.** Si dos
  personas pedían el mismo hueco en el mismo instante, por canales distintos,
  la que perdía recibía un error en vez de «ese horario se acaba de ocupar» con
  otros horarios libres. Ahora se reintenta una vez y recibe los horarios.

### Cambiado
- **La guía dice qué necesitas en tu computadora para operar con Claude Code**
  (Claude Code, Python, Node.js para el MCP y una llave SSH para actualizar) y
  trae una sección para Windows. Ahí `omni.py` no te pide llaves en Git Bash,
  que no puede ocultarlas: se escriben en PowerShell.

## [0.12.1]

### Corregido
- **Abrir el panel justo después del Deploy ya no te deja en un login sin
  salida.** La primera vez la API tarda unos minutos en arrancar, y el login
  preguntaba una sola vez si faltaba el administrador: si aún no le contestaban,
  se quedaba en un login con el que no entra nadie. Ahora avisa de que el
  servidor está terminando de arrancar y, en cuanto contesta, te lleva a crear
  el administrador.
- **Los registros ya no muestran `RuntimeError: Event loop is closed`.** Salía
  en el primer arranque y en el worker después de las tareas. No rompía nada,
  pero se leía como un error: ahora cada tarea cierra sus conexiones a Redis
  antes de terminar.

## [0.12.0]

### Añadido
- **Instalar en un clic, sin terminal.** En un VPS con Coolify pegas el compose
  (`deploy/coolify.compose.yml`), pones tu dominio y le das a Deploy. Coolify
  genera solo la contraseña de la base, la de las sesiones, la llave de cifrado
  y un código de arranque: nadie teclea secretos. La guía es `INSTALAR.md`.
- **El panel te pide crear el administrador.** La primera vez que abres tu
  dominio, el CRM te pide el código de arranque (está en Coolify, en las
  variables del service), tu correo y tu contraseña. Después te enseña la llave
  de cifrado una sola vez y no te deja entrar hasta que confirmes que la
  guardaste. El código deja de servir en cuanto existe el administrador.

### Cambiado
- **Las imágenes son públicas.** Tu servidor las baja sin token de GitHub, que
  era el paso en el que más se atoraba la instalación.
- **Instalar con Claude Code, más corto.** Sigue siendo la alternativa y usa el
  mismo compose: ya no genera ni sube secretos, y el administrador se crea en
  la misma pantalla del panel.
- **Tus llaves viven en tu carpeta personal.** `omni.py` guarda el token de
  Coolify y el registro de cada actualización en `~/.omnicanal/`, no junto al
  script, así que no se pierden al bajar el zip de una versión nueva. La
  primera vez copia las que tuvieras en `.instalacion/` y deja esa carpeta como
  estaba.
- **Actualizar ya no usa git.** Bajas el zip de la versión nueva, lo abres con
  Claude Code y le pides `/actualizar`. Si no tiene tu usuario del panel, se
  salta la comprobación completa y te dice cómo activarla, en vez de dar por
  mala una versión que arrancó bien.

## [0.11.0]

### Añadido
- **Actualizar con un comando, sin miedo.** Pídele a Claude Code `/actualizar`
  (o corre `python3 scripts/omni.py update`). Antes de tocar nada comprueba que
  la versión nueva esté publicada y deja una copia de la base en tu servidor;
  después espera a que todo arranque sano y comprueba que tus fichas,
  conversaciones, mensajes y citas siguen ahí. Si la versión nueva no arranca,
  vuelve sola a la anterior; si para volver hay que restaurar la copia, te pide
  que lo confirmes tú en tu terminal con `omni.py rollback`.
- **Varias clínicas a la vez, una detrás de otra.** `update --all` actualiza
  todas tus instalaciones en fila y se para en la primera que falle, sin tocar
  las siguientes.

### Cambiado
- **El instalador recuerda lo que `update` necesita.** `mcp-setup` guarda
  también el token de Coolify para el instalador y `push-env` apunta el service
  de cada instalación: no hay que volver a pegar nada para actualizar.
- **Claude Code te pregunta antes de actualizar.** En la copia que se entrega,
  `update` ya no va aprobado de antemano, porque despliega de verdad; `rollback`
  solo se puede correr en tu terminal.

### Corregido
- **La guía para actualizar prometía una vuelta atrás que no siempre
  funcionaba.** Decía que bastaba con volver a poner la versión anterior, y eso
  falla cuando la nueva ya cambió la base: la anterior no arranca sobre ella.
  `/actualizar` y el prompt «Actualizar» de Plataforma → Prompts usan ahora
  `omni.py update`, que distingue los dos casos.

## [0.10.0]

### Añadido
- **Prompts para Claude Code.** Una página nueva en Plataforma → Prompts con 12
  guiones para pegar en Claude Code: instalar, revisar la instalación, conectar
  la IA, WhatsApp, Instagram y Messenger, el correo, el chat de la web y la voz,
  actualizar, reportar un bug y dos rutas avanzadas (Meta directo y Google).
  Cada tarjeta de Canales → Servicios tiene su botón **Copiar prompt**, y al
  copiarlo ya lleva el dominio de tu instalación.
- **Conectar un canal espera la prueba.** Al conectar WhatsApp, Instagram y
  Messenger, el correo o la voz con el instalador, se queda hasta 5 minutos
  esperando el mensaje (o la llamada) de prueba y dice si el canal quedó en
  verde o qué revisar. Avisa si el canal está apagado o en Pausado.

### Cambiado
- **Cada canal contesta a su ritmo.** El chat de la web contesta tras 3 segundos
  (antes esperaba 8, como WhatsApp); el correo no espera; WhatsApp, Instagram y
  Messenger siguen esperando 8 segundos para juntar los mensajes seguidos. El
  campo «Buffer» de Asistentes desaparece: ahora lo pone cada canal.
- **Cada canal recibe el formato que entiende.** En WhatsApp las negritas llegan
  como negritas; en Instagram, Messenger, el chat de la web y el correo, el texto
  llega limpio, sin asteriscos ni almohadillas. Sofía sabe en qué canal escribe:
  corto en Instagram, con saludo y despedida en el correo.

### Arreglado
- Las páginas de Prompts y Novedades ya no quedan pegadas al menú.

## [0.9.0]

### Añadido
- **«¿Es el mismo paciente?»** Cuando alguien da su teléfono por Instagram,
  Messenger, el chat web o el correo, Sofía lo apunta en su ficha. Si ese
  teléfono ya es de otra ficha (la de su WhatsApp o la de una llamada), aparece
  un aviso en el chat, en la ficha y en Para hacer, con la etiqueta «¿Mismo
  paciente?». También al revés: cuando llama o escribe por WhatsApp alguien
  desde un número que otra persona dio por Instagram, y cuando alguien escribe
  desde un correo que ya figura en otra ficha.
- **Unir las fichas desde el aviso.** Un administrador las une con un clic (pide
  confirmación) o dice «No es la misma» y ese par no se vuelve a proponer. Queda
  una sola ficha con las conversaciones, las notas y las citas de las dos, con
  su teléfono de verdad, y la persona se sigue reconociendo por Instagram,
  Messenger o el chat web: su siguiente mensaje cae en la misma ficha y la
  respuesta sale por donde escribió.
- La ficha enseña **«Teléfono que dio»** y **«También escribe por»**.

### Cambiado
- **El CRM nunca une fichas solo con un teléfono escrito en un chat:**
  cualquiera puede escribir el de otra persona. Lo decide un administrador.
- En Clientes, Messenger se reconoce como canal: antes su identificador salía
  como si fuera un teléfono.
- Una llamada desde un celular que llega con el «1» de antes (+521…) cae en la
  misma ficha que su WhatsApp.

### Arreglado
- **Unir dos fichas borraba las citas de la que desaparecía.** Pasaba con el
  botón Fusionar y cuando el CRM unía solo las dos fichas de un mismo WhatsApp.
  Ahora las citas pasan a la ficha que queda.

### Importante
- Si quieres que Sofía pida el teléfono cuando escriben por Instagram o
  Messenger (para encontrar su ficha), añade en Asistentes una línea como «Si
  escriben por Instagram o Messenger, pide su teléfono para buscar su ficha».
  Con el prompt que ya tienes lo guarda cuando se lo dan.

## [0.8.0]

### Añadido
- **Agenda.** Una pestaña nueva en Atención con las citas de todos los canales,
  por día o por semana, en la hora de la clínica. Lo que Sofía agenda por
  WhatsApp, Instagram, Messenger, correo, chat web o teléfono aparece al
  momento. Desde ahí se agenda a mano (con un clic en el hueco), se mueve una
  cita, se marca como completada o no asistió, se cancela y se bloquea un
  horario (la hora de comer, unas vacaciones).
- **Sofía mueve y cancela citas.** Si alguien pide cambiar o cancelar su cita,
  Sofía lo hace ella misma después de decir la política de 24 horas. También
  ve las próximas citas y las últimas notas de la persona, y cambia su etapa
  (contacto, cotización, seguimiento, paciente…).
- **Citas de hoy en Para hacer**, arriba de la cola, y la **próxima cita** en
  la cabecera del chat y en la ficha, con una sección «Citas» para agendar
  desde la ficha.

### Cambiado
- **Las citas viven en el CRM, no en Google Calendar.** Sofía agenda aunque no
  haya Google conectado. Solo confirma un horario que ella misma ofreció y que
  sigue libre, y dos citas no se pueden encimar ni aunque lleguen a la vez por
  dos canales. Si Google Calendar está conectado, recibe una copia de cada
  cita; en Asistentes → Horarios se elige si se copia y si lo ocupado en Google
  cuenta.
- **Ya no le llega una invitación de Google al paciente.** La copia en Google
  es para el equipo.
- **Por teléfono, durante un bloqueo de la agenda Sofía toma el recado** en vez
  de pasar la llamada a recepción.
- Las etapas del embudo se llaman como en la clínica: Cotización (antes
  Solicitud presupuesto), Paciente (antes Cliente) y No califica. Se cambia la
  etapa desde la cabecera de la ficha.

### Importante
- Al actualizar, los asistentes que ya agendaban reciben solos las
  herramientas para mover y cancelar citas y para cambiar la etapa. El prompt
  que ya tienes no cambia; si quieres que Sofía lo haga explícito, añade en
  Asistentes una línea como «Si quiere cambiar o cancelar su cita, hazlo tú
  después de decir la política».

## [0.7.2]

### Añadido
- **Voz → «Por devolver».** La página de llamadas marca con «Devolver
  llamada» las que Sofía dejó para una persona (el recado de fuera de horario)
  y tiene un filtro para verlas juntas. Con «Ya la devolví» salen de la lista y
  de Para hacer. Antes solo se veían en Chats, mezcladas con los chats.

### Cambiado
- **Recepción en otro país ya no hace fallar la transferencia.** Al conectar
  el número o guardar el teléfono de recepción, el CRM añade el país de
  recepción a los países a los que el número puede llamar en Retell, y lo
  enseña en las comprobaciones («el número puede llamar a recepción»).
- **Con el recado, Sofía se despide.** Ya no ofrece tomar datos que ya tiene:
  el número queda con la llamada.

### Importante
- Si conectaste tu número antes de esta versión y recepción está en otro país,
  vuelve a guardar el teléfono de recepción (o lanza `omni.py connect voice
  --transferir-a +…`) para que el CRM ajuste la lista de países.

## [0.7.1]

### Cambiado
- **Por teléfono, Sofía pregunta para qué es antes de pasar la llamada.** Si
  alguien pide hablar con recepción sin decir el motivo, Sofía le pregunta en
  qué le pueden ayudar (y su nombre, si no lo sabe) antes de pasarle la llamada
  o de tomar el recado. Antes la pasaba en cuanto oía «con recepción».

### Importante
- Si recepción está en otro país que tu número de voz, revisa en Retell que el
  número pueda llamar a ese país: los números dados de alta en el curso 02
  solo llaman a México y Retell rechaza la transferencia. Guía:
  `docs/avanzado/voz.md`.

## [0.7.0]

### Añadido
- **Sofía pasa la llamada a recepción.** Si quien llama pide hablar con una
  persona, en horario de atención Sofía le pregunta su nombre y el motivo y le
  pasa la llamada al teléfono de recepción que pongas en la conexión de voz.
  Recepción ve el número de quien llama.
- **Fuera de horario, el recado llega a Para hacer.** Sofía toma el recado,
  dice que le devolverán la llamada y cuelga, y la llamada queda en Para hacer
  para que alguien la devuelva. Antes lo prometía pero la llamada se archivaba
  sin que nadie la viera.
- **El resumen de cada llamada, en la ficha.** Al colgar, queda una nota en la
  ficha de quien llamó con lo que quería, el siguiente paso, si quedó cita en
  la agenda, su interés, su urgencia y si Sofía se salió de sus reglas. El
  mismo resumen sale en Voz. Si no se puede hacer, no se inventa nada: el
  motivo queda en Registros. Su gasto aparece en Uso como «Resumen de
  llamadas».
- **Por teléfono, las horas se dicen como se hablan.** Sofía ofrece «mañana a
  las cuatro y media de la tarde» con la hora de tu negocio, en vez de leer una
  hora del sistema, y ya no confunde hoy con mañana.

### Cambiado
- **Al descolgar, Sofía avisa de que la llamada se graba**, como pide la
  LFPDPPP. Si el canal no tiene saludo propio, dice el de tu perfil de
  instalación (con el nombre de tu clínica); antes se guardaba y sonaba uno
  genérico que no avisaba de nada.

### Importante
- En instalaciones anteriores, **activa «Derivar a humano» en el Agente de
  Voz** (Asistentes): las nuevas ya la traen. Sin ella Sofía no pasa llamadas.
- Si tu número es de Twilio, vuelve a lanzar `omni.py connect voice --twilio`
  para que la troncal permita pasar llamadas.
- Pasar llamadas solo funciona en llamadas de teléfono, no en las de prueba
  desde el navegador. Guía: `docs/avanzado/voz.md`.
- Si escribiste tu propio saludo de voz (Conexiones → tarjeta de la voz),
  comprueba que avisa de que la llamada se graba.

## [0.6.0]

### Añadido
- **La voz se conecta sin tocar la consola de Retell.** En Conexiones →
  Servicios → «Llamadas de voz (Retell)» pegas la clave de Retell y el CRM crea
  el agente en tu cuenta, ya apuntando a tu instalación, en español de
  Latinoamérica y con las grabaciones protegidas; lo publica y comprueba que
  quedó bien. Desde ahí mismo puedes comprarle un número de EE. UU. (Retell lo
  cobra cada mes) o atarle uno que ya tengas. Guía: `docs/avanzado/voz.md`.
- **Números de México por Twilio**, desde la terminal: `omni.py connect voice
  --twilio` monta la conexión entre Twilio y Retell. Tus llaves de Twilio se
  usan para eso y no se guardan en el CRM.
- **Teléfono de recepción** en la conexión de voz: se guarda para cuando Sofía
  pueda pasar la llamada a una persona.

### Cambiado
- **Las reglas de seguridad del Agente de Voz ya no se borran por error.** La
  sección «SEGURIDAD — NO TOCAR» de su prompt se puede ampliar, pero no se
  guarda un prompt que quite o cambie una de sus reglas, tampoco restaurando
  una versión vieja ni desde un asistente externo. En las instalaciones nuevas
  esa sección incluye además no diagnosticar, no recomendar medicamentos y no
  confirmar citas que no existen.
- El CRM publica los cambios del agente de voz con el método nuevo de Retell;
  el anterior está obsoleto desde julio.

## [0.5.3]

### Cambiado
- **«Volver a traer el historial» funciona con los canales apagados.** Si
  apagaste Instagram porque sigues usando ManyChat en esa cuenta, puedes
  dejar la bandeja limpia sin encenderlo: el historial nunca hace contestar a
  Sofía.

## [0.5.2]

### Corregido
- **El historial de Instagram ya no se queda a medias.** Al conectar Zernio,
  la importación iba más rápido de lo que permite Zernio, se cortaba y dejaba
  en la bandeja conversaciones viejas sin leer. Ahora va a su ritmo, espera
  cuando Zernio lo pide y deja cada conversación leída y archivada al
  terminarla.
- **Las conversaciones que ya tenías abiertas no se archivan** al importar el
  historial: solo se archivan las que no tienen nada más que mensajes viejos.
- **Los mensajes viejos no cuentan como ráfaga.** Un paciente con muchos
  mensajes antiguos podía quedar marcado como ráfaga (y, con muchos, bloqueado
  un rato). Vale también para el historial de la app de WhatsApp.

### Añadido
- **«Volver a traer el historial»** en Conexiones → Servicios → Instagram y
  Messenger · vía Zernio. No duplica nada y deja bien una importación que se
  cortó.

## [0.5.1]

### Cambiado
- **Conexiones deja claras las dos formas de conectar Instagram.** «Instagram y
  Messenger · vía Zernio» va primero y es la recomendada; «Instagram · vía
  oficial de Meta» queda como avanzada, para quien ya tiene su propia app de
  Meta. Cuando tu Instagram entra por Zernio, la oficial sale como «sin usar» y
  deja de enseñar las direcciones de Meta. En Canales, cada tarjeta dice por
  qué vía entra.

## [0.5.0]

### Añadido
- **Instagram y Messenger por Zernio.** Conecta tu Instagram y la página de
  Facebook de la clínica sin crear una app de Meta ni pasar su revisión: en
  Conexiones → Servicios → «Instagram y Messenger (Zernio)» pegas la API key de
  tu cuenta de Zernio, eliges las cuentas y listo. El CRM registra solo el
  aviso de mensajes y trae las conversaciones que ya tenías, como leídas y
  archivadas. Guía: `docs/avanzado/zernio.md`.
- **Messenger tiene su pestaña en Chats.** Sofía contesta los mensajes a tu
  página igual que en Instagram. Si contestas tú desde la app de Instagram o
  desde Business Suite, tu mensaje aparece como tuyo y Sofía se aparta.
- **Aviso de privacidad y términos públicos** en `/privacidad` y `/terminos`.
  Meta pide esa dirección para Instagram y Messenger. Rellena los datos de la
  clínica en Ajustes → Aviso de privacidad (y que lo revise tu asesor legal).

### Importante
- **Desconecta ManyChat u otro bot** de tu Instagram antes de conectarlo por
  Zernio: sus mensajes llegan como si fueran de una persona y apartarían a
  Sofía.
- Si Zernio no responde, la respuesta no se da por enviada: la conversación
  pasa a una persona y lo nuevo se atiende solo cuando vuelve.

## [0.4.5]

### Cambiado
- **Por teléfono, el "déjame revisar la agenda" suena antes.** Sofía lo dice
  en cuanto decide consultar la agenda o sus datos, sin esperar a tener
  preparada la consulta. Es menos silencio en los turnos que más tardaban.

## [0.4.4]

### Cambiado
- **Por teléfono, Sofía empieza a hablar antes.** Ya no espera a tener la
  respuesta entera: dice las primeras palabras en cuanto las tiene. La
  revisión de contenido sigue haciéndose, pero al mismo tiempo que Sofía piensa
  y no antes, y nada se dice ni se agenda sin su visto bueno.
- **Respuestas más cortas por teléfono.** Una o dos frases por turno y sin
  repetir precios que ya dijo, para que no tengas que interrumpirla.
- **Los datos que dictas por teléfono se guardan.** El correo y el nombre se
  apuntan en la ficha en cuanto los dices; ya no pide el teléfono desde el que
  llamas (lo tiene) y, si le dictas otro, lo repite tal cual, sin inventar la
  lada. Tampoco dice "te agendé" hasta que la cita existe.

## [0.4.3]

### Corregido
- **Sofía ya sabe qué día es.** Antes, a "¿tienen hueco mañana?" podía
  contestar que esa fecha ya había pasado, porque nadie le decía la fecha de
  hoy. Ahora recibe en cada mensaje el día y la hora de la clínica (según la
  zona horaria de **Ajustes → Horarios**), en WhatsApp, en el chat web, en el
  correo y por teléfono.
- **Por teléfono ya no dice dos veces que va a revisar la agenda.** Solo lo
  avisa el sistema y Sofía empieza directamente por la respuesta.

## [0.4.2]

### Cambiado
- **Por teléfono, Sofía ya no se queda callada mientras revisa la agenda.**
  Cuando consulta los huecos o agenda una cita dice enseguida "Déjame revisar
  la agenda." (o "Dame un segundo." en lo demás) y luego sigue con la
  respuesta. Así quien llama no se queda con dos o tres segundos de silencio
  pensando que se cortó. En modo Entrenamiento no dice nada, como siempre.

## [0.4.1]

### Cambiado
- **Al conectar el correo solo entra lo nuevo.** Antes se traía también el
  último día, y con él avisos, recibos y boletines viejos, algunos con
  borrador de Sofía. Ahora el buzón (y Gmail) empieza a contar desde que lo
  conectas.

## [0.4.0]

### Nuevo
- **Conecta el correo de la clínica sin configurar Google.** En **Canales →
  Servicios → Correo** eliges tu proveedor (Gmail, Hostinger, cPanel, Zoho u
  otro), escribes el correo y una contraseña de aplicación y das a **Probar
  conexión**: el panel comprueba que entra y que puede enviar antes de
  guardar, y si algo falla te dice qué hacer. Outlook y Microsoft 365 no
  admiten este acceso, y el panel te avisa antes de intentarlo.
- **Sofía contesta en el mismo hilo.** Por defecto deja un borrador en el chat
  y tú decides si lo mandas (si lo editas, aprende de tu cambio). Con **Sofía
  envía sola** responde directamente. La respuesta aparece también en la
  carpeta de Enviados de tu buzón.
- **Nunca contesta** a listas de correo, respuestas automáticas, remitentes
  «no-reply» ni a las direcciones que pongas en **No responder nunca a**.
- Los adjuntos de los correos se pueden descargar desde el chat.

### Arreglado
- El instalador ya no enseña en claro las claves de OpenAI y de YCloud al
  listar lo guardado.

## [0.3.2]

### Nuevo
- **Sabes de qué anuncio viene cada cliente.** Cuando alguien escribe desde un
  anuncio de Meta que abre WhatsApp, el chat muestra encima de su primer
  mensaje una tarjeta con el anuncio (título, texto, imagen y enlace), la
  conversación lleva la etiqueta **Anuncio** y la ficha del contacto lo anota
  en su actividad. Antes esa información llegaba y se perdía.
- **Sofía sabe qué anuncio vio el cliente** y contesta sobre esa oferta en vez
  de empezar de cero.
- **Ventana gratuita de 72 horas.** Si se contesta a un cliente de anuncio
  dentro de las primeras 24 horas, Meta no cobra nada de lo que se le mande en
  las 72 horas siguientes, plantillas incluidas. El chat te dice hasta cuándo.
  El texto libre sigue limitado a 24 horas desde el último mensaje del
  cliente: esa regla de WhatsApp no cambia.

## [0.3.1]

### Nuevo
- **Contestar por WhatsApp pasadas las 24 horas.** Cuando el cliente lleva más
  de un día sin escribir, WhatsApp solo deja mandarle una plantilla aprobada.
  Ahora el chat te lo dice y te ofrece **Enviar plantilla** ahí mismo: eliges
  la plantilla, rellenas los huecos (los que están enlazados a la ficha salen
  ya rellenos) y el mensaje queda en la conversación como cualquier otro. Lo
  puede hacer cualquier persona del equipo, no solo quien administra.
- Desde la ficha del contacto, la plantilla también queda guardada en su
  conversación de WhatsApp si tiene una abierta.

## [0.3.0]

### Nuevo
- **El panel lleva tu marca.** En **Cuenta → Apariencia** eliges el nombre, el
  logotipo, el color, la letra y el grosor de los iconos. Todo el panel cambia
  mientras editas, así que ves el resultado antes de guardar, y si sales sin
  guardar vuelve a como estaba. Lo ve todo tu equipo, también en la pantalla
  de entrada. Si eliges un color muy claro, el panel te avisa de que los
  botones se van a distinguir poco del fondo; los textos en tu color se ajustan
  solos para que se lean bien.

### Cambiado
- **El panel estrena diseño.** Letra nueva, menú ordenado en cinco grupos, y
  los chats se leen como en el móvil: burbujas, separadores por día y el hilo
  sobre su propio fondo. Registros y Auditoría comparten entrada, con dos
  pestañas.
- Las gráficas del agente usan colores que se distinguen también si no ves
  bien el rojo o el verde, y ya no pintan de rojo lo que no es un error.

### Arreglado
- Reconectar WhatsApp desde el instalador ya no deja de recibir mensajes. Antes
  podía cambiar la clave con la que se comprueba que los mensajes vienen de
  YCloud por una vieja, y desde ahí se rechazaba todo lo que entraba sin avisar.
- En los registros, las horas ya no salen tapadas como si fueran un teléfono.

## [0.2.3]

### Arreglado
- **El aviso de mensajes nuevos de WhatsApp ya se crea de verdad.** La
  dirección que el CRM le daba a YCloud salía sin `https://` cuando el
  servidor la calculaba solo, YCloud la rechazaba y el canal se quedaba
  conectado pero sin recibir nada. Afectaba igual a la dirección de Instagram
  que el panel te dice que pegues en Meta y a los permisos de Google.
- Si YCloud rechaza el aviso, ahora se dice en el momento y con el motivo. Se
  quedaba en silencio y la conexión parecía correcta.

## [0.2.2]

### Arreglado
- **Conectar WhatsApp por primera vez ya funciona.** El panel pedía la clave
  de firma del aviso de mensajes antes de crearlo, y esa clave la entrega
  YCloud justo al crearlo: no había forma de tenerla todavía, así que la
  conexión fallaba siempre la primera vez. Ahora solo se piden la clave de
  API y el número.

## [0.2.1]

### Arreglado
- Cuando falta la clave con la que se comprueba que los mensajes los manda
  YCloud, el panel ya no te manda a pegarla a mano: esa clave solo se ve al
  crearla, así que si conectaste sobre una cuenta que ya la tenía nunca la has
  visto. Ahora te manda al botón que pide una nueva.
- Si al pedir la clave nueva YCloud la cambia pero el CRM no consigue
  guardarla, el panel lo dice y te pide reintentar, en vez de fallar sin
  explicar nada. Hasta que se guarde, los mensajes que entren se rechazan.

## [0.2.0]

### Añadido
- **WhatsApp se conecta en un paso.** Ya no hay que entrar a la consola de
  YCloud a crear el aviso de mensajes nuevos ni a copiar su clave: el CRM lo
  da de alta él solo y se la guarda.
- **Puedes contestar desde tu celular.** Lo que respondes en la app de
  WhatsApp Business aparece en el panel como tuyo, y el agente se aparta de
  esa conversación para no escribir encima. Al conectar, tus conversaciones
  anteriores entran archivadas, sin que el agente las conteste.
- Botón para pedirle a YCloud una clave de firma nueva, en Conexiones →
  WhatsApp. Es lo que hay que usar si conectaste sobre una cuenta que ya tenía
  el aviso creado, o si crees que la clave se filtró.
- Cuando tienes varios números en la misma cuenta de YCloud, solo entran los
  mensajes del tuyo.
- Guion de instalación paso a paso, con un asistente que comprueba el servidor,
  el dominio y el certificado antes de empezar y dice qué falta cuando algo no
  cuadra.

### Cambiado
- **La contraseña con la que se instala ya no se puede quedar puesta**: la
  primera vez que entras, el panel te pide cambiarla.

### Arreglado
- Si WhatsApp reintenta la entrega de un mensaje, ya no sale repetido en la
  bandeja ni hace que el agente conteste dos veces.
- Los mensajes de grupos de WhatsApp no entran al CRM.
- Los ✓✓ se emparejan también cuando el contacto no tiene teléfono guardado.

## [0.1.0]

### Añadido
- El CRM se instala desde imágenes ya construidas: el servidor ya no compila
  nada y la instalación baja de media hora a unos minutos.
- Instalación en un VPS sin panel de control, con un script que genera las
  claves y pide el certificado él solo.
- Esta pantalla de Novedades.

### Cambiado
- El panel y la API viven en un solo dominio. Ya no hay que crear tres
  subdominios ni cuadrar permisos entre ellos.
- El worker arranca con 2 procesos en vez de 4, y se puede subir. En un
  servidor pequeño, eso deja memoria libre para la base de datos.

### Arreglado
- La clave de cifrado que genera Coolify ya no impide arrancar.
- La página de demostración del widget vuelve a cargar el chat.

// Tema ANTES del primer paint: mismo criterio que src/store/theme.ts
// (localStorage "chatbot-theme" y, si no hay, preferencia del sistema). Sin
// esto, quien usa modo oscuro ve un flash blanco mientras carga el bundle,
// porque la clase .dark la pondría el JS de la app.
//
// VA EN UN FICHERO APARTE, NO EN LÍNEA DENTRO DE index.html: la CSP del panel
// (frontend/nginx.conf) declara `script-src 'self'` sin 'unsafe-inline', así
// que el navegador BLOQUEA cualquier <script> en línea — el tema no se aplicaba
// y la consola sacaba un error en rojo en cada carga. Desde un fichero propio
// del mismo origen la política lo permite sin tener que abrir la mano.
(function () {
  try {
    var t = localStorage.getItem("chatbot-theme");
    if (t !== "light" && t !== "dark") {
      t = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    if (t === "dark") document.documentElement.classList.add("dark");
  } catch (e) {
    /* sin localStorage (modo privado): se queda el claro por defecto */
  }
})();

// Apariencia de la instalación (color, tipografía, trazo de iconos y título),
// desde el caché que deja src/store/apariencia.ts. El CSS ya viene calculado:
// aquí no se deriva nada, solo se pega. Mismos ids que usa el store, para que
// al llegar la respuesta del servidor reescriba estos elementos y no duplique.
// Sin caché (primera visita, modo privado) sale la de fábrica y el store la
// cambia en cuanto responde la API.
(function () {
  try {
    var c = JSON.parse(localStorage.getItem("apariencia") || "null");
    if (!c) return;
    if (c.css) {
      var s = document.createElement("style");
      s.id = "apariencia-css";
      s.textContent = c.css;
      document.head.appendChild(s);
    }
    if (c.fuente && /^https:\/\/fonts\.googleapis\.com\//.test(c.fuente)) {
      var l = document.createElement("link");
      l.id = "apariencia-fuente";
      l.rel = "stylesheet";
      l.href = c.fuente;
      document.head.appendChild(l);
    }
    if (c.titulo) document.title = c.titulo;
  } catch (e) {
    /* caché ilegible: se queda la de fábrica */
  }
})();

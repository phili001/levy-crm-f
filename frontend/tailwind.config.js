/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Papeles y tintas. Vinculados a variables CSS de canal RGB
        // (definidas en index.css) para que cambien con la clase `.dark` y a
        // la vez admitan modificadores de opacidad (p. ej. bg-ink/40).
        paper: "rgb(var(--paper-rgb) / <alpha-value>)",
        paper2: "rgb(var(--paper-2-rgb) / <alpha-value>)",
        paper3: "rgb(var(--paper-3-rgb) / <alpha-value>)",
        card: "rgb(var(--card-rgb) / <alpha-value>)",
        ink: "rgb(var(--ink-rgb) / <alpha-value>)",
        ink2: "rgb(var(--ink-2-rgb) / <alpha-value>)",
        ink3: "rgb(var(--ink-3-rgb) / <alpha-value>)",
        ink4: "rgb(var(--ink-4-rgb) / <alpha-value>)",
        // Las líneas ya llevan alpha incorporado y no usan modificadores.
        line: "var(--line)",
        line2: "var(--line-2)",

        // Marca. Igual en claro y oscuro salvo `ink`. Todo sale de variables
        // (index.css) porque cada instalación la cambia desde Apariencia sin
        // recompilar. Los tonos 50/100/500 se quitaron: nadie los usaba y se
        // habrían quedado en cobalto al cambiar la marca.
        brand: {
          600: "rgb(var(--brand-600-rgb) / <alpha-value>)",
          700: "rgb(var(--brand-700-rgb) / <alpha-value>)",
          DEFAULT: "rgb(var(--brand-rgb) / <alpha-value>)",
          ink: "rgb(var(--brand-ink-rgb) / <alpha-value>)",
          on: "rgb(var(--brand-on-rgb) / <alpha-value>)",
          // Fondo suave de marca por tema (var en index.css). Antes esta clave
          // no existía y `bg-brand-soft` (Users/Agents/Outbound) no generaba
          // CSS: los estados activos quedaban sin fondo.
          soft: "var(--brand-soft)",
        },

        // Canal: de dónde viene la conversación. El verde es el de WhatsApp
        // a propósito — es información, no decoración.
        ch: {
          wa: "#25D366",
          web: "#0E9F9F",
          voice: "#C2427A",
        },
        state: {
          ok: "#2DA771",
          warn: "#E58A2F",
          bad: "#DC4B3C",
        },
      },
      // Una sola familia para todo lo que se lee. Figtree es lo más cercano
      // libre al tipo que usa Meta en sus productos: geométrica-humanista,
      // altura de x alta, misma calidez. El serif de display desaparece —
      // era la firma más reconocible del sistema anterior.
      // JetBrains Mono se queda, pero SOLO para logs y trazas.
      fontFamily: {
        // La familia sale de `--font-sans` para que Apariencia la cambie.
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        display: ["var(--font-sans)", "system-ui", "sans-serif"],
        numbers: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      // 18/12 → 10/6. Apretar los radios es lo que más cambia la sensación
      // del panel, por encima del color: pasa de blando a instrumento.
      borderRadius: {
        coro: "10px",
        "coro-sm": "6px",
      },
      // Sombras por tema (vars en index.css). Se usan poco a propósito: la
      // separación entre superficies la hacen los bordes, y la sombra queda
      // para lo que de verdad flota (menús, diálogos, el panel del agente).
      boxShadow: {
        "coro-1": "var(--shadow-coro-1)",
        "coro-2": "var(--shadow-coro-2)",
        "coro-3": "var(--shadow-coro-3)",
      },
    },
  },
  plugins: [],
};

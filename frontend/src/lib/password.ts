// Fuente ÚNICA del mínimo de contraseña en el panel.
//
// Tiene que ser el mismo número que `PASSWORD_MIN_LENGTH` en
// backend/app/core/security.py. Llegó a haber tres cifras distintas para el
// mismo campo (el texto decía 8, el `minLength` del input 10 y el backend
// exigía 12): el formulario dejaba escribir, el backend rechazaba y el usuario
// se comía un error que no cuadraba con lo que acababa de leer.
//
// Si algún día sube el mínimo del backend, se cambia AQUÍ y ya: los tres
// formularios que definen contraseña (perfil, reset por token y alta/cambio
// desde Admin → Usuarios) leen de esta constante.
export const PASSWORD_MIN_LENGTH = 12;

// La política del backend no es solo longitud: exige además letras Y números
// (y rechaza las más usadas, las secuencias de teclado y las que contienen el
// email o el nombre). El texto que ve el usuario dice la regla entera, no la
// mitad; los motivos concretos los devuelve el backend en el error.
export const PASSWORD_HINT = `Mín. ${PASSWORD_MIN_LENGTH} caracteres, con letras y números`;

export const PASSWORD_RULE_ERROR =
  `La contraseña debe tener al menos ${PASSWORD_MIN_LENGTH} caracteres ` +
  `y combinar letras y números.`;

---
titulo: Conectar el correo de la clínica (IMAP/SMTP)
canal: correo
grupo: principal
resumen: Sofía lee el buzón y deja borradores (o responde sola) con una contraseña de aplicación.
---
Conecta el buzón de correo de la clínica a mi CRM (dominio: <dominio>) con una contraseña de aplicación.

1. Pregúntame la dirección del buzón y dónde está: gmail, hostinger, cpanel, zoho o zoho_pro.
2. Explícame cómo saco una contraseña de aplicación en ese proveedor y pídeme que la guarde en mi segunda terminal: `python3 scripts/omni.py secret set EMAIL_APP_PASSWORD --instance <dominio>`. No me la pidas por el chat.
3. Pregúntame si Sofía deja borradores para revisar (lo normal) o si responde sola (`--envia-sola`).
4. Corre `python3 scripts/omni.py connect email --instance <dominio> --correo <dirección> --proveedor <proveedor>` con un tiempo de espera de 10 minutos.
5. Cuando te lo pida, mando un correo de prueba al buzón desde otra cuenta.

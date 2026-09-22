import os
import re
import time
import logging
import asyncio
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from iqoptionapi.stable_api import IQ_Option
from telegram import Update, constants
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

# ================= 1. CONFIGURACIÓN =================
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
IQ_USER = os.getenv("IQ_USER")
IQ_PASS = os.getenv("IQ_PASS")
IQ_ACCOUNT_TYPE = os.getenv("IQ_ACCOUNT_TYPE", "PRACTICE").upper()
TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", "1.0"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# ================= 2. SERVIDOR KEEPALIVE HTTP =================
class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Atleon Stealth Blitz Executor Live")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_web():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), KeepAliveHandler)
    server.serve_forever()

# ================= 3. CONEXIÓN A IQ OPTION =================
api = None

# IDs comprobados en el broker para Blitz
BLITZ_MAP = {
    "GER": 2046,
    "GER30": 2046,
    "GERMANY": 2046,
    "GER30-OTC": 2046,
    "AU": 2048,
    "AU200": 2048,
    "AUS200": 2048,
    "AUS200-OTC": 2048,
    "TRUMP": 2265,
    "TRUMPUSD": 2265,
    "TRUMPUSD-OTC": 2265,
}

def registrar_ids_blitz():
    """Registra los IDs en el diccionario de bajo nivel api.api.ACTIVES_OPCODE"""
    global api
    if api and hasattr(api, "api") and hasattr(api.api, "ACTIVES_OPCODE"):
        try:
            for nom, aid in BLITZ_MAP.items():
                api.api.ACTIVES_OPCODE[nom] = aid
            logging.info("⚡ [BLITZ] IDs mapeados en api.api.ACTIVES_OPCODE.")
        except Exception as e:
            logging.warning(f"No se pudo mapear ACTIVES_OPCODE: {e}")

def conectar_iq():
    global api
    try:
        logging.info(f"⚡ Conectando motor Blitz a IQ Option ({IQ_USER})...")
        cliente = IQ_Option(IQ_USER.strip(), IQ_PASS.strip())
        ok, reason = cliente.connect()
        if ok:
            cliente.change_balance(IQ_ACCOUNT_TYPE)
            api = cliente
            registrar_ids_blitz()
            logging.info(f"⚡ [BLITZ LISTO] Cuenta: {IQ_ACCOUNT_TYPE} | Saldo: ${api.get_balance():.2f}")
            return True
        else:
            logging.error(f"❌ Error al conectar a IQ: {reason}")
            return False
    except Exception as e:
        logging.error(f"❌ Excepción en conexión: {e}")
        return False

def asegurar_sesion():
    global api
    if api is None or not api.check_connect():
        return conectar_iq()
    return True

# ================= 4. MOTOR DE EJECUCIÓN BLITZ =================
def _disparar_blitz_nativo(activo_raw, dir_iq):
    global api
    if not asegurar_sesion():
        return False, "Broker desconectado"

    raw = activo_raw.upper().replace("/", "").replace(" ", "").strip()
    dir_str = "call" if "call" in dir_iq.lower() or "sube" in dir_iq.lower() else "put"

    # Determinar el ID del activo Blitz
    active_id = BLITZ_MAP.get(raw)
    if not active_id:
        if any(k in raw for k in ["GER", "GERMANY"]):
            active_id = 2046
        elif any(k in raw for k in ["AU", "AUS"]):
            active_id = 2048
        elif "TRUMP" in raw:
            active_id = 2265
        else:
            active_id = 2046  # Fallback a GER 30

    registrar_ids_blitz()
    ultimo_err = "Sin respuesta"

    # Intento 1: Disparo directo por buyv3 pasando el active_id numérico
    try:
        if hasattr(api, "api") and hasattr(api.api, "buyv3"):
            ok, id_op = api.api.buyv3(TRADE_AMOUNT, active_id, dir_str, 1)
            if ok and (isinstance(id_op, int) or id_op):
                return True, f"Blitz #{id_op} en ID `{active_id}`"
            else:
                ultimo_err = str(id_op)
    except Exception as e:
        ultimo_err = str(e)

    # Intento 2: Inyección por api.buy convencional usando el par registrado
    nombres_probar = [k for k, v in BLITZ_MAP.items() if v == active_id]
    for n in nombres_probar:
        try:
            ok, id_op = api.buy(TRADE_AMOUNT, n, dir_str, 1)
            if ok and (isinstance(id_op, int) or id_op):
                return True, f"Blitz #{id_op} en `{n}`"
            else:
                ultimo_err = str(id_op)
        except Exception as e:
            ultimo_err = str(e)

    return False, f"Rechazado ({ultimo_err})"

async def ejecutar_blitz_seguro(activo, direccion):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_disparar_blitz_nativo, activo, direccion),
            timeout=4.0
        )
    except asyncio.TimeoutError:
        return False, "Timeout: Broker no devolvió confirmación en 4s"
    except Exception as e:
        return False, str(e)

# ================= 5. CONTROLADORES TELEGRAM =================
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if asegurar_sesion():
        saldo = api.get_balance()
        await update.message.reply_text(
            f"⚡ **Atleon Stealth Blitz Executor**\n"
            f"• Estado: 🟢 Operativo (Modo Blitz)\n"
            f"• Saldo: `${saldo:.2f}`\n"
            f"• Cuenta: `{IQ_ACCOUNT_TYPE}`\n"
            f"• Monto por Trade: `${TRADE_AMOUNT:.2f}`",
            parse_mode=constants.ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text("❌ Sin conexión con IQ Option.")

async def procesar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    texto = update.message.text.strip().upper()

    # 1. Dirección
    direccion = None
    if any(w in texto for w in ["CALL", "SUBE", "COMPRA", "HIGHER"]):
        direccion = "CALL"
    elif any(w in texto for w in ["PUT", "BAJA", "VENTA", "LOWER"]):
        direccion = "PUT"

    if not direccion:
        return

    # 2. Identificar activo Blitz
    activo = "GER 30"
    if any(k in texto for k in ["GER30", "GERMANY", "GER 30", "GER"]):
        activo = "GER 30"
    elif any(k in texto for k in ["AUS200", "AU200", "AU 200", "AUS", "AU"]):
        activo = "AU 200"
    elif "TRUMP" in texto:
        activo = "TRUMP"

    logging.info(f"⚡ [DISPARO BLITZ]: {activo} {direccion}")
    exito, info = await ejecutar_blitz_seguro(activo, direccion)

    estado = "✅" if exito else "⚠️"
    await update.message.reply_text(
        f"{estado} ⚡ **BLITZ DIRECTO** | `{activo}` {direccion} ➔ {info}",
        parse_mode=constants.ParseMode.MARKDOWN
    )

# ================= 6. ARRANQUE =================
if __name__ == "__main__":
    Thread(target=run_web, daemon=True).start()
    conectar_iq()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_mensaje))

    app.run_polling(drop_pending_updates=True)

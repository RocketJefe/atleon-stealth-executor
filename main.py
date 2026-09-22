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

def registrar_ids_blitz():
    """Inyecta los IDs de activos Blitz directamente en el diccionario interno del broker"""
    global api
    if api:
        mapeo = {
            "GER30-OTC": 2046,
            "GER 30": 2046,
            "GERMANY30": 2046,
            "AUS200-OTC": 2048,
            "AU 200": 2048,
            "TRUMPUSD-OTC": 2265,
            "TRUMP": 2265,
        }
        for nombre, aid in mapeo.items():
            api.ACTIVES_OPCODE[nombre] = aid
        logging.info("⚡ [BLITZ] IDs numéricos mapeados en memoria con éxito.")

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
            logging.info(f"⚡ [BLITZ ENGINE LISTO] Saldo: ${api.get_balance():.2f}")
            return True
        else:
            logging.error(f"❌ Error al conectar a IQ: {reason}")
            return False
    except Exception as e:
        logging.error(f"❌ Excepción: {e}")
        return False

def asegurar_sesion():
    global api
    if api is None or not api.check_connect():
        return conectar_iq()
    return True

# ================= 4. MOTOR DE EJECUCIÓN DIRECTO =================
def _disparar_blitz_nativo(activo_raw, dir_iq):
    global api
    if not asegurar_sesion():
        return False, "Broker desconectado"

    raw = activo_raw.upper().replace("/", "").strip()
    dir_str = "call" if "call" in dir_iq.lower() or "sube" in dir_iq.lower() else "put"

    # Seleccionar activo registrado
    if any(k in raw for k in ["GER", "GERMANY"]):
        par_activo = "GER30-OTC"
    elif any(k in raw for k in ["AU", "AUS"]):
        par_activo = "AUS200-OTC"
    elif "TRUMP" in raw:
        par_activo = "TRUMPUSD-OTC"
    else:
        par_activo = raw

    # Asegurar mapeo en memoria antes de comprar
    registrar_ids_blitz()

    # Disparo directo mediante api.buy nativo con el ID inyectado
    try:
        ok, id_op = api.buy(TRADE_AMOUNT, par_activo, dir_str, 1)
        if ok and (isinstance(id_op, int) or id_op):
            return True, f"Blitz #{id_op} en `{par_activo}`"
        return False, f"Rechazado por broker ({id_op})"
    except Exception as e:
        return False, f"Error ejecución: {e}"

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

    # 2. Identificar activo
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

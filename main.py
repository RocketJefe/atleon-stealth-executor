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

# ================= 1. CONFIGURACIÓN Y ENTORNO =================
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

# ================= 2. SERVIDOR KEEPALIVE HTTP (RENDER) =================
class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Atleon Stealth Executor Live")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_web():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), KeepAliveHandler)
    server.serve_forever()

# ================= 3. CONEXIÓN PERSISTENTE IQ OPTION =================
api = None

def conectar_iq():
    global api
    try:
        logging.info(f"Conectando a IQ Option con {IQ_USER}...")
        cliente = IQ_Option(IQ_USER.strip(), IQ_PASS.strip())
        ok, reason = cliente.connect()
        if ok:
            cliente.change_balance(IQ_ACCOUNT_TYPE)
            api = cliente
            logging.info(f"✅ Conectado a IQ Option ({IQ_ACCOUNT_TYPE}) | Saldo: ${api.get_balance():.2f}")
            return True
        else:
            logging.error(f"❌ Error al conectar a IQ: {reason}")
            return False
    except Exception as e:
        logging.error(f"❌ Excepción durante la conexión a IQ: {e}")
        return False

def asegurar_sesion():
    global api
    if api is None or not api.check_connect():
        return conectar_iq()
    return True

# ================= 4. MOTOR DE DISPARO BLITZ Y 60S =================
def _ejecutar_en_broker(par, dir_iq, duracion):
    global api
    if not asegurar_sesion():
        return False, "Broker desconectado"

    par_limpio = par.upper().replace("/", "").strip()
    
    # Normalización de índices
    if par_limpio in ["GER30", "GER 30", "GERMANY30"]:
        par_limpio = "GERMANY30"
    elif par_limpio in ["AU200", "AU 200", "AUS200"]:
        par_limpio = "AUS200"

    # MODO BLITZ (30s)
    if "30" in str(duracion):
        try:
            # Precarga y suscripción al flujo de strikes requerido por Digital Spot
            api.subscribe_strike_list(par_limpio, 30)
            time.sleep(0.4)
            
            ok, id_op = api.buy_digital_spot(par_limpio, TRADE_AMOUNT, dir_iq, 30)
            if ok and id_op:
                api.unsubscribe_strike_list(par_limpio, 30)
                return True, f"Blitz 30s #{id_op}"
        except Exception as e:
            logging.warning(f"Digital Spot no completado en {par_limpio}: {e}")

        # Fallback a binaria turbo 1m si Digital Spot no está abierto
        try:
            ok, id_op = api.buy(TRADE_AMOUNT, par_limpio, dir_iq, 1)
            if ok and id_op:
                return True, f"Binaria Turbo Fallback #{id_op}"
            return False, str(id_op)
        except Exception as err:
            return False, str(err)

    # MODO BINARIAS 60S (Forex OTC tradicional)
    else:
        try:
            ok, id_op = api.buy(TRADE_AMOUNT, par_limpio, dir_iq, 1)
            if ok and id_op:
                return True, f"Binaria 60s #{id_op}"
            return False, str(id_op)
        except Exception as err:
            return False, str(err)

async def disparar_orden_segura(par, direccion, duracion):
    dir_iq = direccion.lower()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_ejecutar_en_broker, par, dir_iq, duracion),
            timeout=5.0
        )
    except asyncio.TimeoutError:
        return False, "Tiempo de espera agotado en broker (Timeout)"
    except Exception as e:
        return False, str(e)

# ================= 5. CONTROLADORES TELEGRAM =================
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if asegurar_sesion():
        saldo = api.get_balance()
        await update.message.reply_text(
            f"👻 **Atleon Stealth Executor**\n"
            f"• Estado: 🟢 Operativo\n"
            f"• Saldo: `${saldo:.2f}`\n"
            f"• Cuenta: `{IQ_ACCOUNT_TYPE}`",
            parse_mode=constants.ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text("❌ Sin conexión con IQ Option.")

async def procesar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    texto = update.message.text.strip().upper()

    # Identificación de dirección
    direccion = None
    if any(w in texto for w in ["CALL", "SUBE", "COMPRA"]):
        direccion = "CALL"
    elif any(w in texto for w in ["PUT", "BAJA", "VENTA"]):
        direccion = "PUT"

    if not direccion:
        return

    # Identificación de duración
    duracion = "30S" if any(w in texto for w in ["30S", "30 S", "BLITZ", "30"]) else "60S"

    # Identificación de activo
    par = None
    if "GER30" in texto or "GERMANY30" in texto or "GER 30" in texto:
        par = "GERMANY30"
    elif "AU200" in texto or "AUS200" in texto or "AU 200" in texto:
        par = "AUS200"
    elif "TRUMP" in texto:
        par = "TRUMP"
    else:
        match = re.search(r"\b([A-Z0-9]{3,6}(?:-OTC)?)\b", texto)
        ignorar = ["EXEC", "CALL", "PUT", "SUBE", "BAJA", "COMPRA", "VENTA", "STATUS", "BLITZ", "30S", "60S"]
        if match and match.group(1) not in ignorar:
            par = match.group(1)

    if not par:
        return

    logging.info(f"⚡ Disparando orden: {par} {direccion} {duracion}")
    exito, info = await disparar_orden_segura(par, direccion, duracion)

    estado = "✅" if exito else "⚠️"
    await update.message.reply_text(
        f"{estado} `{par}` {direccion} ({duracion}) ➔ {info}",
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

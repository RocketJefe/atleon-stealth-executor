import os
import re
import time
import logging
import asyncio
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from iqoptionapi.stable_api import IQ_Option
from telegram import Update
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

# ================= 2. SERVIDOR KEEPALIVE HTTP (RENDER) =================
class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Atleon Stealth Executor 60S Live")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_web():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), KeepAliveHandler)
    server.serve_forever()

# ================= 3. CONEXIÓN A IQ OPTION =================
api = None

def conectar_iq():
    global api
    try:
        logging.info(f"Conectando a IQ Option ({IQ_USER})...")
        cliente = IQ_Option(IQ_USER.strip(), IQ_PASS.strip())
        ok, reason = cliente.connect()
        if ok:
            cliente.change_balance(IQ_ACCOUNT_TYPE)
            api = cliente
            logging.info(f"✅ Conectado a IQ Option | Cuenta: {IQ_ACCOUNT_TYPE} | Saldo: ${api.get_balance():.2f}")
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

# ================= 4. MOTOR DE EJECUCIÓN A 60 SEGUNDOS =================
def _ejecutar_en_broker(par_solicitado, dir_iq):
    global api
    if not asegurar_sesion():
        return False, "Broker desconectado"

    par = par_solicitado.upper().replace("/", "").replace(" ", "").strip()
    candidatos = [par]
    
    if "-OTC" in par:
        candidatos.append(par.replace("-OTC", ""))
    else:
        candidatos.append(f"{par}-OTC")

    ultimo_error = "No disponible"

    # Ruta 1: Binaria Turbo (Expiración exacta de 1 minuto / 60s)
    for p in candidatos:
        try:
            ok, id_op = api.buy(TRADE_AMOUNT, p, dir_iq, 1)
            if ok and (isinstance(id_op, int) or id_op):
                return True, f"Binaria 60s #{id_op} en {p}"
            elif id_op:
                ultimo_error = str(id_op)
        except Exception as e:
            ultimo_error = str(e)

    # Ruta 2: Fallback a Digital Spot (1 minuto) si Binarias está temporalmente pausado
    for p in candidatos:
        try:
            api.subscribe_strike_list(p, 1)
            time.sleep(0.3)
            ok, id_op = api.buy_digital_spot(p, TRADE_AMOUNT, dir_iq, 1)
            if ok and id_op:
                api.unsubscribe_strike_list(p, 1)
                return True, f"Digital 60s #{id_op} en {p}"
        except Exception as e:
            ultimo_error = str(e)

    return False, f"Rechazado ({ultimo_error})"

async def disparar_orden_segura(par, direccion):
    dir_iq = direccion.lower()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_ejecutar_en_broker, par, dir_iq),
            timeout=4.5
        )
    except asyncio.TimeoutError:
        return False, "Timeout: Broker tardó más de 4.5s"
    except Exception as e:
        return False, str(e)

# ================= 5. CONTROLADORES TELEGRAM =================
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if asegurar_sesion():
        saldo = api.get_balance()
        await update.message.reply_text(
            f"⚡ Atleon Stealth Executor\n"
            f"• Estado: 🟢 Operativo (Modo 60 Segundos)\n"
            f"• Saldo: ${saldo:.2f}\n"
            f"• Cuenta: {IQ_ACCOUNT_TYPE}\n"
            f"• Monto por Trade: ${TRADE_AMOUNT:.2f}"
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

    # 2. Identificar el Par solicitado
    par = None
    if "EURUSD" in texto:
        par = "EURUSD-OTC" if "OTC" in texto else "EURUSD"
    elif "GBPUSD" in texto:
        par = "GBPUSD-OTC" if "OTC" in texto else "GBPUSD"
    elif "USDJPY" in texto:
        par = "USDJPY-OTC" if "OTC" in texto else "USDJPY"
    elif "AUDUSD" in texto:
        par = "AUDUSD-OTC" if "OTC" in texto else "AUDUSD"
    else:
        tokens = re.findall(r"[A-Z0-9\-]+", texto)
        ignorar = ["EXEC", "CALL", "PUT", "SUBE", "BAJA", "COMPRA", "VENTA", "STATUS", "60S", "60"]
        for t in tokens:
            if t not in ignorar and len(t) >= 4:
                par = t
                break

    if not par:
        par = "EURUSD"

    logging.info(f"⚡ [DISPARO 60S]: {par} {direccion}")
    exito, info = await disparar_orden_segura(par, direccion)

    estado = "✅" if exito else "⚠️"
    await update.message.reply_text(f"{estado} 60S | {par} {direccion} ➔ {info}")

# ================= 6. ARRANQUE =================
if __name__ == "__main__":
    Thread(target=run_web, daemon=True).start()
    conectar_iq()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_mensaje))

    app.run_polling(drop_pending_updates=True)

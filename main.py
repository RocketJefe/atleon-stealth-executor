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

# ================= 4. MOTOR DE EJECUCIÓN DIRECTO A 60S =================
def _ejecutar_en_broker(par_solicitado, dir_iq):
    global api
    if not asegurar_sesion():
        return False, "Broker desconectado"

    par_limpio = par_solicitado.upper().replace("/", "").replace(" ", "").strip()
    
    # En días de semana operan los pares reales; los fines de semana o fuera de sesión operan OTC
    candidatos = [par_limpio]
    if "-OTC" in par_limpio:
        candidatos.append(par_limpio.replace("-OTC", ""))
    else:
        candidatos.append(f"{par_limpio}-OTC")

    ultimo_error = "Par no disponible"

    for p in candidatos:
        try:
            # Disparo Turbo directo a 1 minuto (60s) sin esperas de strikes
            ok, id_op = api.buy(TRADE_AMOUNT, p, dir_iq, 1)
            if ok and (isinstance(id_op, int) or id_op):
                return True, f"Binaria 60s #{id_op} en {p}"
            elif id_op:
                ultimo_error = str(id_op)
        except Exception as e:
            ultimo_error = str(e)

    return False, f"Rechazado ({ultimo_error})"

async def disparar_orden_segura(par, direccion):
    dir_iq = direccion.lower()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_ejecutar_en_broker, par, dir_iq),
            timeout=5.0
        )
    except asyncio.TimeoutError:
        return False, "Timeout en conexión con broker (5s)"
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

async def abiertos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not asegurar_sesion():
        await update.message.reply_text("❌ Broker desconectado.")
        return

    msg = await update.message.reply_text("🔍 Consultando pares de 60s abiertos en este momento...")
    try:
        all_actives = api.get_all_init()
        turbo_actives = all_actives.get("result", {}).get("turbo", {}).get("actives", {})
        
        abiertos = []
        for aid, data in turbo_actives.items():
            if data.get("enabled", False) and not data.get("is_suspended", True):
                name = data.get("name", "")
                if "/" in name or "USD" in name or "EUR" in name:
                    abiertos.append(name.replace("/", ""))

        if abiertos:
            resumen = ", ".join(abiertos[:10])
            await msg.edit_text(f"⚡ Pares Turbo 60s Abiertos:\n\n{resumen}")
        else:
            await msg.edit_text("⚠️ No se detectaron pares de 60s abiertos en este segundo.")
    except Exception as e:
        await msg.edit_text(f"❌ Error al consultar: {e}")

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

    # 2. Identificar Activo
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
    app.add_handler(CommandHandler("abiertos", abiertos_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_mensaje))

    app.run_polling(drop_pending_updates=True)

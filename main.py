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

# ================= 3. CONEXIÓN PERSISTENTE IQ OPTION =================
api = None

def conectar_iq():
    global api
    try:
        logging.info(f"⚡ Conectando motor Blitz a IQ Option ({IQ_USER})...")
        cliente = IQ_Option(IQ_USER.strip(), IQ_PASS.strip())
        ok, reason = cliente.connect()
        if ok:
            cliente.change_balance(IQ_ACCOUNT_TYPE)
            api = cliente
            logging.info(f"⚡ [BLITZ ENGINE LISTO] Saldo: ${api.get_balance():.2f}")
            return True
        else:
            logging.error(f"❌ Error al conectar a IQ: {reason}")
            return False
    except Exception as e:
        logging.error(f"❌ Excepción durante la conexión: {e}")
        return False

def asegurar_sesion():
    global api
    if api is None or not api.check_connect():
        return conectar_iq()
    return True

# ================= 4. MOTOR BLITZ POR ID DIRECTO =================
BLITZ_REGISTRY = {
    "GER": {"id": 2046, "name": "GER 30 Blitz"},
    "GERMANY": {"id": 2046, "name": "GER 30 Blitz"},
    "DE": {"id": 2046, "name": "GER 30 Blitz"},
    "AU": {"id": 2048, "name": "AU 200 Blitz"},
    "AUS": {"id": 2048, "name": "AU 200 Blitz"},
    "TRUMP": {"id": 2265, "name": "TRUMP Coin Blitz"},
}

def _disparar_blitz_nativo(activo_raw, dir_iq):
    global api
    if not asegurar_sesion():
        return False, "Broker desconectado"

    raw = activo_raw.upper().replace("/", "").strip()
    
    target = None
    for key, data in BLITZ_REGISTRY.items():
        if key in raw:
            target = data
            break

    if not target:
        target = {"id": 2046, "name": "GER 30 Blitz"}

    active_id = target["id"]
    display_name = target["name"]
    dir_str = "call" if "call" in dir_iq.lower() or "sube" in dir_iq.lower() else "put"

    # Intento 1: Llamada al WebSocket subyacente mediante open_option
    try:
        if hasattr(api.api, "open_option"):
            # Expiración a 30 segundos
            exp_time = int(api.get_server_timestamp()) + 30
            api.api.open_option(active_id, TRADE_AMOUNT, dir_str, 3, exp_time)
            time.sleep(0.35)
            return True, f"Blitz disparado en `{display_name}` (ID `{active_id}`)"
    except Exception as e:
        logging.warning(f"Fallo open_option: {e}")

    # Intento 2: Inyección mediante buy_by_raw_expired con nombre de método exacto
    try:
        if hasattr(api, "buy_by_raw_expired"):
            exp_time = int(api.get_server_timestamp()) + 30
            api.buy_by_raw_expired(TRADE_AMOUNT, active_id, dir_str, exp_time)
            return True, f"Blitz orden colocada en `{display_name}`"
    except Exception as e:
        logging.warning(f"Fallo buy_by_raw_expired: {e}")

    # Intento 3: Disparo por buy_order de la API interna
    try:
        api.api.buy_order(active_id, TRADE_AMOUNT, dir_str, 1)
        return True, f"Blitz buy_order enviado a `{display_name}`"
    except Exception as e:
        return False, f"Rechazado en broker: {e}"

async def ejecutar_blitz_seguro(activo, direccion):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_disparar_blitz_nativo, activo, direccion),
            timeout=3.5
        )
    except asyncio.TimeoutError:
        return False, "Timeout en conexión"
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

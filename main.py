import os
import re
import time
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
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

# Pool dedicado para llamadas bloqueantes al broker
thread_pool = ThreadPoolExecutor(max_workers=4)

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

# ================= 3. CONEXIÓN PERSISTENTE A IQ OPTION =================
api = None

# Mapeo exacto comprobado en el backend de IQ Option
BLITZ_MAP = {
    "GER": {"name": "GER30-OTC", "id": 2046, "label": "GER 30 Blitz"},
    "GER30": {"name": "GER30-OTC", "id": 2046, "label": "GER 30 Blitz"},
    "GERMANY": {"name": "GER30-OTC", "id": 2046, "label": "GER 30 Blitz"},
    "AU": {"name": "AUS200-OTC", "id": 2048, "label": "AU 200 Blitz"},
    "AU200": {"name": "AUS200-OTC", "id": 2048, "label": "AU 200 Blitz"},
    "AUS200": {"name": "AUS200-OTC", "id": 2048, "label": "AU 200 Blitz"},
    "TRUMP": {"name": "TRUMPUSD-OTC", "id": 2265, "label": "TRUMP Coin Blitz"},
}

def registrar_ids_en_libreria():
    """Inyecta los nombres e IDs en el diccionario interno para que las funciones nativas los reconozcan"""
    global api
    if api and hasattr(api, "api") and hasattr(api.api, "ACTIVES_OPCODE"):
        try:
            for _, data in BLITZ_MAP.items():
                api.api.ACTIVES_OPCODE[data["name"]] = data["id"]
                api.api.ACTIVES_OPCODE[data["label"]] = data["id"]
        except Exception as e:
            logging.warning(f"Aviso registrando activos: {e}")

def conectar_iq():
    global api
    try:
        logging.info(f"⚡ Conectando motor Blitz a IQ Option ({IQ_USER})...")
        cliente = IQ_Option(IQ_USER.strip(), IQ_PASS.strip())
        ok, reason = cliente.connect()
        if ok:
            cliente.change_balance(IQ_ACCOUNT_TYPE)
            api = cliente
            registrar_ids_en_libreria()
            logging.info(f"⚡ [BLITZ ENGINE LISTO] Cuenta: {IQ_ACCOUNT_TYPE} | Saldo: ${api.get_balance():.2f}")
            return True
        else:
            logging.error(f"❌ Error al conectar a IQ: {reason}")
            return False
    except Exception as e:
        logging.error(f"❌ Excepción durante conexión: {e}")
        return False

def asegurar_sesion():
    global api
    if api is None or not api.check_connect():
        return conectar_iq()
    return True

# ================= 4. MOTOR DE EJECUCIÓN BLITZ CON buy_by_raw_expirations =================
def _disparar_blitz_worker(activo_raw, dir_iq):
    global api
    if not asegurar_sesion():
        return False, "Broker desconectado"

    raw = activo_raw.upper().replace("/", "").strip()
    target = None
    for key, data in BLITZ_MAP.items():
        if key in raw:
            target = data
            break

    if not target:
        target = BLITZ_MAP["GER"]

    par_broker = target["name"]
    etiqueta = target["label"]
    dir_str = "call" if "call" in dir_iq.lower() or "sube" in dir_iq.lower() else "put"
    
    # Calcular expiración: tiempo actual del broker + 30 segundos
    try:
        server_ts = int(api.get_server_timestamp())
    except Exception:
        server_ts = int(time.time())
    exp_blitz = server_ts + 30

    registrar_ids_en_libreria()
    ultimo_err = "Sin confirmación"

    # RUTA 1: buy_by_raw_expirations (función oficial para expiración exacta)
    try:
        ok, id_op = api.buy_by_raw_expirations(TRADE_AMOUNT, par_broker, dir_str, "turbo", exp_blitz)
        if ok and id_op:
            return True, f"Blitz 30s #{id_op} en `{etiqueta}`"
        elif id_op:
            ultimo_err = str(id_op)
    except Exception as e:
        ultimo_err = str(e)

    # RUTA 2: buy con expiración 1m si la opción turbo está en modo 1m
    try:
        ok, id_op = api.buy(TRADE_AMOUNT, par_broker, dir_str, 1)
        if ok and (isinstance(id_op, int) or id_op):
            return True, f"Blitz #{id_op} en `{etiqueta}`"
        elif id_op:
            ultimo_err = str(id_op)
    except Exception as e:
        ultimo_err = str(e)

    return False, f"Rechazado ({ultimo_err})"

async def ejecutar_blitz_seguro(activo, direccion):
    loop = asyncio.get_running_loop()
    try:
        # Aislamiento en thread_pool con timeout estricto de 3.5 segundos
        return await asyncio.wait_for(
            loop.run_in_executor(thread_pool, _disparar_blitz_worker, activo, direccion),
            timeout=3.5
        )
    except asyncio.TimeoutError:
        return False, "Timeout: Petición enviada (esperando confirmación en broker)"
    except Exception as e:
        return False, str(e)

# ================= 5. CONTROLADORES TELEGRAM =================
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if asegurar_sesion():
        saldo = api.get_balance()
        await update.message.reply_text(
            f"⚡ **Atleon Stealth Blitz Executor**\n"
            f"• Estado: 🟢 Operativo (Modo Blitz 30s)\n"
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

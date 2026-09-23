import os
import re
import time
import json
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

BLITZ_REGISTRY = {
    "GER": {"id": 2046, "name": "GER 30 Blitz", "act_name": "GER30-OTC"},
    "GER30": {"id": 2046, "name": "GER 30 Blitz", "act_name": "GER30-OTC"},
    "GERMANY": {"id": 2046, "name": "GER 30 Blitz", "act_name": "GER30-OTC"},
    "AU": {"id": 2048, "name": "AU 200 Blitz", "act_name": "AUS200-OTC"},
    "AU200": {"id": 2048, "name": "AU 200 Blitz", "act_name": "AUS200-OTC"},
    "AUS200": {"id": 2048, "name": "AU 200 Blitz", "act_name": "AUS200-OTC"},
    "TRUMP": {"id": 2265, "name": "TRUMP Coin Blitz", "act_name": "TRUMPUSD-OTC"},
}

def conectar_iq():
    global api
    try:
        logging.info(f"⚡ Conectando motor Blitz a IQ Option ({IQ_USER})...")
        cliente = IQ_Option(IQ_USER.strip(), IQ_PASS.strip())
        ok, reason = cliente.connect()
        if ok:
            cliente.change_balance(IQ_ACCOUNT_TYPE)
            api = cliente
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

# ================= 4. MOTOR BLITZ DIRECTO =================
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
        target = {"id": 2046, "name": "GER 30 Blitz", "act_name": "GER30-OTC"}

    active_id = target["id"]
    display_name = target["name"]
    par_tecnico = target["act_name"]
    dir_str = "call" if "call" in dir_iq.lower() or "sube" in dir_iq.lower() else "put"
    
    # 1. Registro directo en ACTIVES_OPCODE interno para compatibilidad
    if hasattr(api, "api") and hasattr(api.api, "ACTIVES_OPCODE"):
        api.api.ACTIVES_OPCODE[par_tecnico] = active_id
        api.api.ACTIVES_OPCODE[display_name] = active_id

    # 2. Disparo por buy_digital_spot (30s)
    try:
        api.subscribe_strike_list(par_tecnico, 1)
        time.sleep(0.2)
        ok, id_op = api.buy_digital_spot(par_tecnico, TRADE_AMOUNT, dir_str, 1)
        if ok and id_op:
            api.unsubscribe_strike_list(par_tecnico, 1)
            return True, f"Blitz #{id_op} en `{display_name}`"
    except Exception as e:
        logging.warning(f"Fallo Digital Spot: {e}")

    # 3. Disparo nativo por buyv3 con ID numérico
    try:
        if hasattr(api, "api") and hasattr(api.api, "buyv3"):
            exp_time = int(api.get_server_timestamp()) + 30
            ok, id_op = api.api.buyv3(TRADE_AMOUNT, active_id, dir_str, exp_time)
            if ok and (isinstance(id_op, int) or id_op):
                return True, f"Blitz #{id_op} en `{display_name}`"
    except Exception as e:
        logging.warning(f"Fallo buyv3: {e}")

    # 4. Disparo por mensaje WebSocket directo
    try:
        user_balance_id = api.profile.balance_id
        exp_time = int(api.get_server_timestamp()) + 30
        payload = {
            "name": "sendMessage",
            "msg": {
                "name": "binary-options.open-option",
                "version": "1.0",
                "body": {
                    "user_balance_id": user_balance_id,
                    "active_id": active_id,
                    "option_type_id": 3,
                    "direction": dir_str,
                    "expired": exp_time,
                    "refund_value": 0,
                    "price": TRADE_AMOUNT,
                    "value": 0
                }
            },
            "request_id": str(int(time.time() * 1000))
        }
        api.api.send_websocket(json.dumps(payload))
        time.sleep(0.3)
        return True, f"Blitz disparado en `{display_name}` (ID: `{active_id}`)"
    except Exception as e:
        return False, f"Rechazado en broker: {e}"

async def ejecutar_blitz_seguro(activo, direccion):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_disparar_blitz_nativo, activo, direccion),
            timeout=3.8
        )
    except asyncio.TimeoutError:
        return False, "Timeout: Broker no devolvió respuesta en 3.8s"
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

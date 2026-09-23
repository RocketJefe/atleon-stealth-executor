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

# ================= 2. SERVIDOR KEEPALIVE HTTP (RENDER) =================
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

# IDs numéricos verificados de activos Blitz en el broker
BLITZ_MAP = {
    "GER": {"id": 2046, "name": "GER 30 Blitz", "code": "GER30-OTC"},
    "GER30": {"id": 2046, "name": "GER 30 Blitz", "code": "GER30-OTC"},
    "GERMANY": {"id": 2046, "name": "GER 30 Blitz", "code": "GER30-OTC"},
    "AU": {"id": 2048, "name": "AU 200 Blitz", "code": "AUS200-OTC"},
    "AU200": {"id": 2048, "name": "AU 200 Blitz", "code": "AUS200-OTC"},
    "AUS200": {"id": 2048, "name": "AU 200 Blitz", "code": "AUS200-OTC"},
    "TRUMP": {"id": 2265, "name": "TRUMP Coin Blitz", "code": "TRUMPUSD-OTC"},
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
            # Mapear en tabla interna de activos
            if hasattr(api, "api") and hasattr(api.api, "ACTIVES_OPCODE"):
                for _, item in BLITZ_MAP.items():
                    api.api.ACTIVES_OPCODE[item["code"]] = item["id"]
                    api.api.ACTIVES_OPCODE[item["name"]] = item["id"]
            logging.info(f"⚡ [BLITZ ENGINE LISTO] Cuenta: {IQ_ACCOUNT_TYPE} | Saldo: ${api.get_balance():.2f}")
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

# ================= 4. MOTOR DE EJECUCIÓN DIRECTO BLITZ =================
def _disparar_blitz_nativo(activo_raw, dir_iq):
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
        target = BLITZ_MAP["GER30"]

    active_id = target["id"]
    display_name = target["name"]
    code_otc = target["code"]
    dir_str = "call" if "call" in dir_iq.lower() or "sube" in dir_iq.lower() else "put"

    # Inyección 1: Protocolo nativo buyv3 directo sobre el ID Blitz
    try:
        if hasattr(api, "api") and hasattr(api.api, "buyv3"):
            ok, id_op = api.api.buyv3(TRADE_AMOUNT, active_id, dir_str, 1)
            if ok and (isinstance(id_op, int) or id_op):
                return True, f"Blitz 30s #{id_op} en `{display_name}`"
    except Exception as e:
        logging.warning(f"Fallo buyv3: {e}")

    # Inyección 2: Paquete raw WebSocket directo
    try:
        user_balance_id = api.profile.balance_id
        server_time = int(api.get_server_timestamp())
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
                    "expired": server_time + 30,
                    "refund_value": 0,
                    "price": TRADE_AMOUNT,
                    "value": 0
                }
            },
            "request_id": str(int(time.time() * 1000))
        }
        api.api.send_websocket(json.dumps(payload))
        return True, f"Orden enviada a socket para `{display_name}` (ID: `{active_id}`)"
    except Exception as e:
        # Inyección 3: Fallback con buy estándar mapeado
        try:
            ok, id_op = api.buy(TRADE_AMOUNT, code_otc, dir_str, 1)
            if ok and (isinstance(id_op, int) or id_op):
                return True, f"Blitz #{id_op} en `{code_otc}`"
        except Exception:
            pass
        return False, f"Rechazado en broker: {e}"

async def ejecutar_blitz_seguro(activo, direccion):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_disparar_blitz_nativo, activo, direccion),
            timeout=3.0
        )
    except asyncio.TimeoutError:
        return False, "Timeout: Broker no devolvió respuesta en 3s"
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

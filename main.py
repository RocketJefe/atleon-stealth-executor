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

def conectar_iq():
    global api
    try:
        logging.info(f"⚡ Conectando a IQ Option ({IQ_USER})...")
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
        logging.error(f"❌ Excepción: {e}")
        return False

def asegurar_sesion():
    global api
    if api is None or not api.check_connect():
        return conectar_iq()
    return True

# ================= 4. MAPEO EXACTO DE ACTIVOS BLITZ =================
# Mapeo directo a los identificadores verificados en backend
MAPA_BLITZ = {
    "GER30": "GER30-OTC",
    "GER 30": "GER30-OTC",
    "GERMANY30": "GER30-OTC",
    "GER30-OTC": "GER30-OTC",
    "AU200": "AUS200-OTC",
    "AU 200": "AUS200-OTC",
    "AUS200": "AUS200-OTC",
    "AUS200-OTC": "AUS200-OTC",
    "TRUMP": "TRUMPUSD-OTC",
    "TRUMP COIN": "TRUMPUSD-OTC",
    "TRUMPUSD": "TRUMPUSD-OTC",
    "TRUMPUSD-OTC": "TRUMPUSD-OTC",
}

def _disparar_blitz_en_broker(activo_input, dir_iq):
    global api
    if not asegurar_sesion():
        return False, "Broker desconectado"

    raw = activo_input.upper().replace("/", "").strip()
    activo_real = MAPA_BLITZ.get(raw, raw)

    # Lista de variantes a intentar en orden de prioridad
    candidatos = [activo_real]
    if "GER" in raw:
        candidatos = ["GER30-OTC", 2046, "GERMANY30"]
    elif "AU" in raw:
        candidatos = ["AUS200-OTC", 2048, "AUS200:N"]
    elif "TRUMP" in raw:
        candidatos = ["TRUMPUSD-OTC", 2265, "TRUMP"]

    ultimo_err = "Sin respuesta"

    for act in candidatos:
        try:
            # Disparo directo mediante Turbo (duración base de 1 minuto)
            ok, id_op = api.buy(TRADE_AMOUNT, act, dir_iq, 1)
            if ok and (isinstance(id_op, int) or id_op):
                return True, f"Blitz #{id_op} en `{act}`"
            else:
                ultimo_err = str(id_op)
        except Exception as e:
            ultimo_err = str(e)

    return False, f"Rechazado ({ultimo_err})"

async def disparar_blitz_seguro(activo, direccion):
    dir_iq = direccion.lower()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_disparar_blitz_en_broker, activo, dir_iq),
            timeout=4.0
        )
    except asyncio.TimeoutError:
        return False, "Timeout en broker (4s)"
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

    # 2. Identificación del activo
    activo = None
    if any(k in texto for k in ["GER30", "GERMANY", "GER 30", "GER"]):
        activo = "GER30-OTC"
    elif any(k in texto for k in ["AUS200", "AU200", "AU 200", "AUS"]):
        activo = "AUS200-OTC"
    elif "TRUMP" in texto:
        activo = "TRUMPUSD-OTC"
    else:
        tokens = re.findall(r"[A-Z0-9\-]+", texto)
        ignorar = ["EXEC", "CALL", "PUT", "SUBE", "BAJA", "COMPRA", "VENTA", "STATUS", "BLITZ", "30S", "60S", "30", "60"]
        for t in tokens:
            if t not in ignorar and len(t) >= 2:
                activo = t
                break

    if not activo:
        return

    logging.info(f"⚡ [DISPARO BLITZ]: {activo} {direccion}")
    exito, info = await disparar_blitz_seguro(activo, direccion)

    estado = "✅" if exito else "⚠️"
    await update.message.reply_text(
        f"{estado} ⚡ **BLITZ** | `{activo}` {direccion} ➔ {info}",
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

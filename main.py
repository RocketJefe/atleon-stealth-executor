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

# ================= 3. CONEXIÓN PERSISTENTE A IQ OPTION =================
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

# ================= 4. MOTOR EXCLUSIVO BLITZ =================
def _disparar_blitz_en_broker(activo, dir_iq):
    global api
    if not asegurar_sesion():
        return False, "Broker desconectado"

    activo_raw = activo.upper().replace("/", "").replace(" ", "").strip()

    # Nombres reconocidos en IQ Option para Blitz / Índices
    candidatos = [activo_raw]
    if any(k in activo_raw for k in ["GER", "GERMANY"]):
        candidatos = ["GERMANY30", "GER30", "GER_30"]
    elif any(k in activo_raw for k in ["AU", "AUS"]):
        candidatos = ["AUS200", "AU200", "AU_200"]

    ultimo_error = "Sin respuesta de strike"

    for act in candidatos:
        try:
            # Suscripción obligatoria de strikes en escala de 1m (base del motor digital)
            api.subscribe_strike_list(act, 1)
            time.sleep(0.35)

            # Intento 1: Digital Spot estándar
            ok, id_op = api.buy_digital_spot(act, TRADE_AMOUNT, dir_iq, 1)
            if ok and id_op:
                api.unsubscribe_strike_list(act, 1)
                return True, f"Blitz Spot #{id_op} en `{act}`"
        except Exception as e:
            ultimo_error = str(e)

        try:
            # Intento 2: Digital Spot V2
            ok, id_op = api.buy_digital_spot_v2(act, TRADE_AMOUNT, dir_iq, 1)
            if ok and id_op:
                return True, f"Blitz SpotV2 #{id_op} en `{act}`"
        except Exception as e:
            ultimo_error = str(e)

    return False, f"Rechazado en broker ({ultimo_error})"

async def ejecutar_blitz_seguro(activo, direccion):
    dir_iq = direccion.lower()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_disparar_blitz_en_broker, activo, dir_iq),
            timeout=4.5
        )
    except asyncio.TimeoutError:
        return False, "Timeout: Broker no devolvió confirmación en 4.5s"
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

async def blitzinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not asegurar_sesion():
        await update.message.reply_text("❌ Broker desconectado.")
        return

    msg = await update.message.reply_text("🔍 Consultando cotizaciones de activos Blitz...")
    indices = ["GERMANY30", "AUS200", "US30", "TRUMP"]
    activos_encontrados = []
    ahora = time.time()

    for ind in indices:
        try:
            c = api.get_candles(ind, 60, 1, ahora)
            if c and len(c) > 0 and "close" in c[0]:
                activos_encontrados.append(f"`{ind}` ({c[0]['close']})")
        except Exception:
            continue

    if activos_encontrados:
        txt = "⚡ **Activos Blitz con flujo en vivo:**\n\n" + "\n".join(activos_encontrados)
    else:
        txt = "⚠️ No se recibieron velas activas de índices Blitz."

    await msg.edit_text(txt, parse_mode=constants.ParseMode.MARKDOWN)

async def procesar_orden_blitz(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    # 2. Activo
    activo = None
    if any(k in texto for k in ["GER30", "GERMANY", "GER 30", "DE30"]):
        activo = "GERMANY30"
    elif any(k in texto for k in ["AUS200", "AU200", "AU 200", "AUS 200"]):
        activo = "AUS200"
    elif "TRUMP" in texto:
        activo = "TRUMP"
    elif "US30" in texto:
        activo = "US30"
    else:
        tokens = re.findall(r"[A-Z0-9]+", texto)
        ignorar = ["EXEC", "CALL", "PUT", "SUBE", "BAJA", "COMPRA", "VENTA", "STATUS", "BLITZ", "30S", "60S", "30", "60"]
        for t in tokens:
            if t not in ignorar and len(t) >= 3:
                activo = t
                break

    if not activo:
        return

    logging.info(f"⚡ [DISPARO BLITZ]: {activo} {direccion}")
    exito, info = await ejecutar_blitz_seguro(activo, direccion)

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
    app.add_handler(CommandHandler("blitzinfo", blitzinfo_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_orden_blitz))

    app.run_polling(drop_pending_updates=True)

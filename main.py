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
            logging.info(f"⚡ [BLITZ LISTO] Saldo: ${api.get_balance():.2f}")
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

# ================= 4. MOTOR BLITZ =================
def _disparar_blitz_en_broker(activo_input, dir_iq):
    global api
    if not asegurar_sesion():
        return False, "Broker desconectado"

    raw = activo_input.upper().replace("/", "").strip()

    # Variantes exactas usadas en backend e interfaz para índices Blitz
    candidatos = [raw]
    if any(k in raw for k in ["GER", "GERMANY", "DE"]):
        candidatos = ["GER 30", "GER30", "GER_30", "GERMANY30", "DE30"]
    elif any(k in raw for k in ["AU", "AUS"]):
        candidatos = ["AU 200", "AU200", "AU_200", "AUS200"]
    elif "TRUMP" in raw:
        candidatos = ["TRUMP Coin", "TRUMP", "TRUMP_COIN"]

    ultimo_err = "Sin cotización"

    for act in candidatos:
        # Intento A: Suscripción y compra Digital Spot
        try:
            api.subscribe_strike_list(act, 1)
            time.sleep(0.3)
            ok, id_op = api.buy_digital_spot(act, TRADE_AMOUNT, dir_iq, 1)
            if ok and id_op:
                api.unsubscribe_strike_list(act, 1)
                return True, f"Blitz Spot #{id_op} en `{act}`"
        except Exception as e:
            ultimo_err = str(e)

        # Intento B: buy_digital_spot_v2
        try:
            ok, id_op = api.buy_digital_spot_v2(act, TRADE_AMOUNT, dir_iq, 1)
            if ok and id_op:
                return True, f"Blitz SpotV2 #{id_op} en `{act}`"
        except Exception as e:
            ultimo_err = str(e)

        # Intento C: Orden binaria rápida en el par
        try:
            ok, id_op = api.buy(TRADE_AMOUNT, act, dir_iq, 1)
            if ok and (isinstance(id_op, int) or id_op):
                return True, f"Blitz Directo #{id_op} en `{act}`"
        except Exception as e:
            ultimo_err = str(e)

    return False, f"Rechazado ({ultimo_err})"

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

# ================= 5. COMANDOS TELEGRAM =================
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

async def detectar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not asegurar_sesion():
        await update.message.reply_text("❌ Broker desconectado.")
        return

    msg = await update.message.reply_text("🔍 Escaneando activos en Digitales y Blitz...")
    try:
        # Consulta catálogo digital y blitz
        digital_data = api.get_all_digital_open_time()
        encontrados = []

        for nombre in digital_data.keys():
            if any(k in nombre.upper() for k in ["GER", "AU", "DE", "TRUMP", "US", "30", "200"]):
                encontrados.append(f"• `{nombre}`")

        # Si el diccionario digital directo viene vacío, consultar la lista completa de inicialización
        if not encontrados:
            init_data = api.get_all_init()
            if init_data and "result" in init_data:
                for cat in ["turbo", "digital", "binary"]:
                    activos = init_data["result"].get(cat, {}).get("actives", {})
                    for aid, info in activos.items():
                        n = info.get("name", "")
                        if any(k in n.upper() for k in ["GER", "AU", "DE", "TRUMP"]):
                            encontrados.append(f"• `{n}` (ID: `{aid}` | {cat})")

        if encontrados:
            txt = "⚡ **Nombres exactos detectados para Blitz/Digital:**\n\n" + "\n".join(encontrados[:15])
        else:
            txt = "⚠️ No se encontraron activos de índices en los diccionarios digitales."

        await msg.edit_text(txt, parse_mode=constants.ParseMode.MARKDOWN)
    except Exception as e:
        await msg.edit_text(f"❌ Error al consultar: {e}")

async def procesar_orden(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        activo = "GER 30"
    elif any(k in texto for k in ["AUS200", "AU200", "AU 200", "AUS 200"]):
        activo = "AU 200"
    elif "TRUMP" in texto:
        activo = "TRUMP Coin"
    else:
        tokens = re.findall(r"[A-Z0-9\-]+", texto)
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
    app.add_handler(CommandHandler("detectar", detectar_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_orden))

    app.run_polling(drop_pending_updates=True)

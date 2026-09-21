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

# ================= 2. KEEPALIVE HTTP =================
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

# ================= 3. CONEXIÓN IQ OPTION =================
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
            logging.error(f"❌ Error al conectar: {reason}")
            return False
    except Exception as e:
        logging.error(f"❌ Excepción: {e}")
        return False

def asegurar_sesion():
    global api
    if api is None or not api.check_connect():
        return conectar_iq()
    return True

# ================= 4. MOTOR EXCLUSIVO BLITZ =================
def _disparar_blitz(activo_str, dir_iq):
    global api
    if not asegurar_sesion():
        return False, "Broker desconectado"

    raw = activo_str.upper().replace("/", "").strip()

    # Opciones de nombres probables para Blitz en el backend
    candidatos = [raw]
    if any(k in raw for k in ["GER", "GERMANY", "DE"]):
        candidatos = ["GER 30", "GER30", "GER_30", "GERMANY30", "DE30", "1232"]
    elif any(k in raw for k in ["AU", "AUS"]):
        candidatos = ["AU 200", "AU200", "AU_200", "AUS200", "1234"]
    elif "TRUMP" in raw:
        candidatos = ["TRUMP Coin", "TRUMP", "TRUMP_COIN"]

    ultimo_error = "Sin respuesta"

    # Intento 1: Disparo Turbo directo (Blitz se enruta como contrato turbo de corta duración)
    for act in candidatos:
        try:
            ok, id_op = api.buy(TRADE_AMOUNT, act, dir_iq, 1)
            if ok and (isinstance(id_op, int) or id_op):
                return True, f"Blitz Directo #{id_op} en `{act}`"
            else:
                ultimo_error = str(id_op)
        except Exception as e:
            ultimo_error = str(e)

    # Intento 2: Disparo Digital Spot con suscripción previa
    for act in candidatos:
        try:
            api.subscribe_strike_list(act, 1)
            time.sleep(0.35)
            ok, id_op = api.buy_digital_spot(act, TRADE_AMOUNT, dir_iq, 1)
            if ok and id_op:
                api.unsubscribe_strike_list(act, 1)
                return True, f"Blitz Spot #{id_op} en `{act}`"
        except Exception as e:
            ultimo_error = str(e)

    return False, f"Rechazado ({ultimo_error})"

async def disparar_blitz_seguro(activo, direccion):
    dir_iq = direccion.lower()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_disparar_blitz, activo, dir_iq),
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

async def detectar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not asegurar_sesion():
        await update.message.reply_text("❌ Broker desconectado.")
        return

    msg = await update.message.reply_text("🔍 Rastreando activos Blitz en la memoria de IQ...")
    encontrados = []
    
    try:
        # Explorar diccionarios de inicialización de la API
        if hasattr(api, "get_all_init"):
            init = api.get_all_init()
            if init and isinstance(init, dict) and "result" in init:
                for categoria, cat_data in init["result"].items():
                    if isinstance(cat_data, dict) and "actives" in cat_data:
                        for act_id, act_info in cat_data["actives"].items():
                            nombre = act_info.get("name", "")
                            desc = act_info.get("description", "")
                            full_txt = f"{nombre} {desc}".upper()
                            if any(k in full_txt for k in ["GER", "AU 200", "AUS", "TRUMP", "BLITZ"]):
                                encontrados.append(f"• `{nombre}` (ID: `{act_id}` | {categoria})")
    except Exception as e:
        logging.error(f"Error en init: {e}")

    if not encontrados:
        # Escaneo directo en velas de índices
        ahora = time.time()
        candidatos_raw = ["GER 30", "GERMANY30", "GER30", "AU 200", "AUS200", "TRUMP Coin", "TRUMP"]
        for c in candidatos_raw:
            try:
                velas = api.get_candles(c, 60, 1, ahora)
                if velas and len(velas) > 0 and "close" in velas[0]:
                    encontrados.append(f"• `{c}` (Cotizando a {velas[0]['close']})")
            except Exception:
                continue

    if encontrados:
        txt = "⚡ **Activos Blitz Detectados en Vivo:**\n\n" + "\n".join(encontrados[:15])
    else:
        txt = "⚠️ No se detectaron identificadores Blitz en la sesión actual."

    await msg.edit_text(txt, parse_mode=constants.ParseMode.MARKDOWN)

async def procesar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    texto = update.message.text.strip().upper()

    # Dirección
    direccion = None
    if any(w in texto for w in ["CALL", "SUBE", "COMPRA", "HIGHER"]):
        direccion = "CALL"
    elif any(w in texto for w in ["PUT", "BAJA", "VENTA", "LOWER"]):
        direccion = "PUT"

    if not direccion:
        return

    # Activo Blitz
    activo = None
    if any(k in texto for k in ["GER30", "GERMANY", "GER 30", "DE30", "GER"]):
        activo = "GER 30"
    elif any(k in texto for k in ["AUS200", "AU200", "AU 200", "AUS 200", "AU"]):
        activo = "AU 200"
    elif "TRUMP" in texto:
        activo = "TRUMP Coin"
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
    app.add_handler(CommandHandler("detectar", detectar_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_mensaje))

    app.run_polling(drop_pending_updates=True)

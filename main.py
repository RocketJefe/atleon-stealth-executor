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

# ================= 2. SERVIDOR KEEPALIVE (RENDER) =================
class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Atleon Stealth Executor Activo")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_web():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), KeepAliveHandler)
    server.serve_forever()

# ================= 3. CONEXIÓN PERSISTENTE IQ =================
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
            logging.info(f"✅ Conectado a IQ Option ({IQ_ACCOUNT_TYPE}) | Saldo: ${api.get_balance():.2f}")
            return True
        else:
            logging.error(f"❌ Error al conectar: {reason}")
            return False
    except Exception as e:
        logging.error(f"❌ Excepción en conexión: {e}")
        return False

def asegurar_sesion():
    global api
    if api is None or not api.check_connect():
        return conectar_iq()
    return True

# ================= 4. MOTOR DE EJECUCIÓN DIRECTO =================
def _ejecutar_en_broker(par, dir_iq, duracion_seg):
    global api
    if not asegurar_sesion():
        return False, "Broker desconectado"

    par_limpio = par.upper().replace("/", "").strip()

    # Si es Blitz / Digital (30s)
    if duracion_seg == 30 or any(k in par_limpio for k in ["GER", "AU", "BLITZ"]):
        # Mapeo de activos Digital Blitz
        blitz_asset = par_limpio
        if any(k in par_limpio for k in ["GER", "GERMANY"]):
            blitz_asset = "GERMANY30"
        elif any(k in par_limpio for k in ["AU", "AUS"]):
            blitz_asset = "AUS200"

        try:
            ok, id_op = api.buy_digital_spot_v2(blitz_asset, TRADE_AMOUNT, dir_iq, 30)
            if ok:
                return True, f"Blitz 30s #{id_op} en `{blitz_asset}`"
        except Exception:
            pass

        try:
            ok, id_op = api.buy_digital_spot(blitz_asset, TRADE_AMOUNT, dir_iq, 30)
            if ok and id_op:
                return True, f"Blitz Spot #{id_op} en `{blitz_asset}`"
        except Exception as err:
            return False, f"Digital Blitz error: {err}"

    # Si es Binarias Clásicas / OTC (60s)
    candidatos = [par_limpio]
    if "-OTC" in par_limpio:
        candidatos.append(par_limpio.replace("-OTC", ""))
    else:
        candidatos.append(f"{par_limpio}-OTC")

    ultimo_err = "No disponible"
    for p in candidatos:
        try:
            ok, id_op = api.buy(TRADE_AMOUNT, p, dir_iq, 1)
            if ok and isinstance(id_op, int):
                return True, f"Orden Binaria #{id_op} en `{p}`"
            elif ok and id_op:
                return True, f"Orden #{id_op} en `{p}`"
            else:
                ultimo_err = str(id_op)
        except Exception as e:
            ultimo_err = str(e)

    return False, f"Rechazado ({ultimo_err})"

async def disparar_orden_segura(par, direccion, duracion_seg):
    dir_iq = direccion.lower()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_ejecutar_en_broker, par, dir_iq, duracion_seg),
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
            f"👻 **Atleon Stealth Executor**\n"
            f"• Estado: 🟢 Operativo\n"
            f"• Saldo: `${saldo:.2f}`\n"
            f"• Cuenta: `{IQ_ACCOUNT_TYPE}`",
            parse_mode=constants.ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text("❌ Sin conexión con IQ Option.")

async def abiertos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not asegurar_sesion():
        await update.message.reply_text("❌ Broker desconectado.")
        return

    msg = await update.message.reply_text("🔍 Consultando pares y payout en vivo...")
    try:
        all_assets = api.get_all_open_time()
        turbo_open = [p for p, data in all_assets.get("turbo", {}).items() if data.get("open")]
        digital_open = [p for p, data in all_assets.get("digital", {}).items() if data.get("open")]

        turbo_txt = ", ".join([f"`{p}`" for p in turbo_open[:10]]) if turbo_open else "Ninguno"
        digital_txt = ", ".join([f"`{p}`" for p in digital_open[:10]]) if digital_open else "Ninguno"

        respuesta = (
            f"📊 **MERCADOS ABIERTOS EN VIVO**\n\n"
            f"⚡ **Binarias Turbo (60s):**\n{turbo_txt}\n\n"
            f"🎯 **Digital / Blitz:**\n{digital_txt}"
        )
        await msg.edit_text(respuesta, parse_mode=constants.ParseMode.MARKDOWN)
    except Exception as e:
        await msg.edit_text(f"❌ Error al obtener pares: {e}")

async def procesar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    texto = update.message.text.strip().upper()

    # 1. Dirección
    direccion = None
    if any(w in texto for w in ["CALL", "SUBE", "COMPRA"]):
        direccion = "CALL"
    elif any(w in texto for w in ["PUT", "BAJA", "VENTA"]):
        direccion = "PUT"

    if not direccion:
        return

    # 2. Duración
    duracion_seg = 30 if any(w in texto for w in ["30S", "30 S", "BLITZ", " 30"]) else 60

    # 3. Activo
    par = None
    if any(k in texto for k in ["GERMANY30", "GER30", "GER 30"]):
        par = "GERMANY30"
    elif any(k in texto for k in ["AUS200", "AU200", "AU 200"]):
        par = "AUS200"
    else:
        match = re.search(r"\b([A-Z0-9_\-]+)\b", texto)
        ignorar = ["EXEC", "CALL", "PUT", "SUBE", "BAJA", "COMPRA", "VENTA", "STATUS", "BLITZ", "30S", "60S", "30", "60"]
        if match and match.group(1) not in ignorar:
            par = match.group(1)

    if not par:
        return

    dur_txt = f"{duracion_seg}S"
    logging.info(f"⚡ Disparando: {par} {direccion} ({dur_txt})")
    exito, info = await disparar_orden_segura(par, direccion, duracion_seg)

    estado = "✅" if exito else "⚠️"
    await update.message.reply_text(
        f"{estado} `{par}` {direccion} ({dur_txt}) ➔ {info}",
        parse_mode=constants.ParseMode.MARKDOWN
    )

# ================= 6. ARRANQUE =================
if __name__ == "__main__":
    Thread(target=run_web, daemon=True).start()
    conectar_iq()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("abiertos", abiertos_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_mensaje))

    app.run_polling(drop_pending_updates=True)

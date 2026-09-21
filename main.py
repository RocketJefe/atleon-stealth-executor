import os
import time
import re
import requests
import threading
from flask import Flask
from iqoptionapi.stable_api import IQ_Option

# ================= KEEPALIVE FLASK (RENDER) =================
app = Flask(__name__)

@app.route('/')
def ping():
    return "OK - Atleon Stealth Executor Activo 24/7", 200

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ================= CREDENCIALES =================
IQ_USER = os.getenv("IQ_USER")
IQ_PASS = os.getenv("IQ_PASS")
IQ_ACCOUNT_TYPE = os.getenv("IQ_ACCOUNT_TYPE", "PRACTICE").upper()
TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", "1.0"))

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

api = None
lock = threading.Lock()

# ================= CONEXIÓN PERSISTENTE =================
def conectar():
    global api
    with lock:
        try:
            cliente = IQ_Option(IQ_USER, IQ_PASS)
            ok, motivo = cliente.connect()
            if ok:
                cliente.change_balance(IQ_ACCOUNT_TYPE)
                api = cliente
                print(f"👻 [STEALTH LISTO] Cuenta: {IQ_ACCOUNT_TYPE} | Saldo: ${api.get_balance():.2f}")
            else:
                print(f"❌ [IQ ERROR]: {motivo}")
        except Exception as e:
            print(f"[RECONECT ERROR]: {e}")

def asegurar_sesion():
    global api
    if not api or not api.check_connect():
        conectar()
    return api is not None and api.check_connect()

# ================= MOTOR DE DISPARO =================
def disparar(par, direccion, duracion):
    if not asegurar_sesion():
        return False, "Broker no disponible"

    dir_iq = direccion.lower()
    par_limpio = par.upper().replace("/", "").strip()

    # Normalización para índices Blitz
    if par_limpio in ["GER30", "GER 30", "GERMANY30"]:
        par_limpio = "GERMANY30"
    elif par_limpio in ["AU200", "AU 200", "AUS200"]:
        par_limpio = "AUS200"

    # 1. Modalidad Blitz 30s
    if "30" in str(duracion):
        # Intento vía Digital Spot (30s)
        try:
            ok, id_op = api.buy_digital_spot(par_limpio, TRADE_AMOUNT, dir_iq, 30)
            if ok and id_op:
                return True, f"Blitz 30s #{id_op}"
        except Exception as e:
            print(f"[DIGITAL ERR]: {e}")

        # Fallback a Binarias 1m si Digital Spot no está abierto
        try:
            ok, id_op = api.buy(TRADE_AMOUNT, par_limpio, dir_iq, 1)
            if ok and id_op:
                return True, f"Binaria Fallback #{id_op}"
            return False, str(id_op)
        except Exception as err:
            return False, str(err)

    # 2. Modalidad Binarias 60s (Forex / OTC)
    else:
        try:
            ok, id_op = api.buy(TRADE_AMOUNT, par_limpio, dir_iq, 1)
            if ok and id_op:
                return True, f"Binaria 60s #{id_op}"
            return False, str(id_op)
        except Exception as err:
            return False, str(err)

# ================= ESCUCHA SILENCIOSA CON REGEX =================
def listener():
    try:
        requests.get(f"{TG_API}/deleteWebhook?drop_pending_updates=True", timeout=5)
    except Exception:
        pass

    last_id = 0
    print("👂 Escuchando señales en segundo plano...")

    while True:
        try:
            res = requests.get(f"{TG_API}/getUpdates?offset={last_id + 1}&timeout=5", timeout=10).json()
            if not res.get("ok"):
                time.sleep(1)
                continue

            for item in res.get("result", []):
                last_id = item["update_id"]
                msg = item.get("message") or item.get("channel_post")
                if not msg or "text" not in msg:
                    continue

                texto = msg["text"].strip()
                chat_id = msg["chat"]["id"]

                # Estado
                if texto.upper() == "/STATUS":
                    asegurar_sesion()
                    saldo = f"${api.get_balance():.2f}" if (api and api.check_connect()) else "Desconectado"
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"👻 **Atleon Stealth Executor**\n• Estado: 🟢 Operativo\n• Saldo: {saldo}\n• Cuenta: {IQ_ACCOUNT_TYPE}"
                    }, timeout=5)
                    continue

                # Detección flexible mediante Expresión Regular
                # Captura cualquier mensaje que contenga EXEC seguido del par, CALL/PUT y duración opcional
                match = re.search(r"EXEC\s+([A-Z0-9_\-]+)\s+(CALL|PUT|SUBE|BAJA)(?:\s+(\d+\s*[SM]?))?", texto, re.IGNORECASE)
                if match:
                    par = match.group(1).strip().upper()
                    dir_raw = match.group(2).strip().upper()
                    direccion = "call" if dir_raw in ["CALL", "SUBE"] else "put"
                    duracion = match.group(3) if match.group(3) else "60S"

                    print(f"⚡ [ORDEN DETECTADA] {par} | {direccion} | {duracion}")
                    exito, info = disparar(par, direccion, duracion)

                    estado = "✅" if exito else "⚠️"
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"{estado} `{par}` {direccion.upper()} ({duracion}) -> {info}"
                    }, timeout=5)

        except Exception as e:
            print(f"[LOOP EXCEPTION]: {e}")
            time.sleep(1)

        time.sleep(0.05)

# ================= ARRANQUE =================
if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=conectar, daemon=True).start()
    listener()

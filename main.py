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

# ================= CONEXIÓN IQ OPTION =================
def conectar():
    global api
    with lock:
        try:
            print(f"[IQ] Conectando con usuario {IQ_USER}...")
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
        return False, "Broker desconectado"

    dir_iq = direccion.lower()
    par_limpio = par.upper().replace("/", "").strip()

    # Normalización de alias
    if par_limpio in ["GER30", "GER 30", "GERMANY30"]:
        par_limpio = "GERMANY30"
    elif par_limpio in ["AU200", "AU 200", "AUS200"]:
        par_limpio = "AUS200"

    # Modalidad Blitz 30s
    if "30" in str(duracion):
        try:
            ok, id_op = api.buy_digital_spot(par_limpio, TRADE_AMOUNT, dir_iq, 30)
            if ok and id_op:
                return True, f"Blitz 30s #{id_op}"
        except Exception as e:
            print(f"[DIGITAL ERR]: {e}")

        # Fallback a binaria 1m si Digital Spot no responde
        try:
            ok, id_op = api.buy(TRADE_AMOUNT, par_limpio, dir_iq, 1)
            if ok and id_op:
                return True, f"Binaria Fallback #{id_op}"
            return False, str(id_op)
        except Exception as err:
            return False, str(err)

    # Modalidad Binarias 60s (Forex / OTC)
    else:
        try:
            ok, id_op = api.buy(TRADE_AMOUNT, par_limpio, dir_iq, 1)
            if ok and id_op:
                return True, f"Binaria 60s #{id_op}"
            return False, str(id_op)
        except Exception as err:
            return False, str(err)

# ================= PARSER UNIVERSAL =================
def parsear_texto(texto):
    texto_upper = texto.upper()

    # 1. Dirección
    direccion = None
    if any(k in texto_upper for k in ["CALL", "SUBE", "COMPRA", "HIGHER"]):
        direccion = "call"
    elif any(k in texto_upper for k in ["PUT", "BAJA", "VENTA", "LOWER"]):
        direccion = "put"

    if not direccion:
        return None, None, None

    # 2. Duración
    duracion = "60S"
    if any(k in texto_upper for k in ["30S", "30 S", "BLITZ", "30SEG", "30 SEG"]):
        duracion = "30S"
    elif any(k in texto_upper for k in ["60S", "60 S", "1M", "1 MIN", "60SEG"]):
        duracion = "60S"

    # 3. Activo
    activo = None
    if "GER30" in texto_upper or "GERMANY30" in texto_upper or "GER 30" in texto_upper:
        activo = "GERMANY30"
    elif "AU200" in texto_upper or "AUS200" in texto_upper or "AU 200" in texto_upper:
        activo = "AUS200"
    elif "TRUMP" in texto_upper:
        activo = "TRUMP"
    else:
        # Busca cualquier par tipo EURUSD-OTC o GBPUSD
        match_par = re.search(r"\b([A-Z0-9]{3,6}(?:-OTC)?)\b", texto_upper)
        ignorar = ["EXEC", "CALL", "PUT", "SUBE", "BAJA", "COMPRA", "VENTA", "STATUS", "BLITZ", "30S", "60S"]
        if match_par and match_par.group(1) not in ignorar:
            activo = match_par.group(1)

    return activo, direccion, duracion

# ================= RECEPTOR TELEGRAM =================
def listener():
    # Limpieza rigurosa de webhook residual
    for _ in range(3):
        try:
            requests.get(f"{TG_API}/deleteWebhook?drop_pending_updates=True", timeout=5)
            break
        except Exception:
            time.sleep(1)

    last_id = 0
    print("👂 [RECEPTOR] Escuchando mensajes en tiempo real...")

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
                print(f"[RECEPTOR TG MSG]: {texto}")

                # Diagnóstico
                if texto.upper().startswith("/STATUS"):
                    asegurar_sesion()
                    saldo = f"${api.get_balance():.2f}" if (api and api.check_connect()) else "Desconectado"
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"👻 **Atleon Stealth Executor**\n• Estado: 🟢 Operativo\n• Saldo: {saldo}\n• Cuenta: {IQ_ACCOUNT_TYPE}"
                    }, timeout=5)
                    continue

                # Procesar cualquier orden
                par, direccion, duracion = parsear_texto(texto)
                if not par or not direccion:
                    continue

                print(f"⚡ [DISPARANDO]: {par} | {direccion.upper()} | {duracion}")
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

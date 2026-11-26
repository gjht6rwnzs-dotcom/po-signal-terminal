import os
import sqlite3
import yfinance as yf
import pandas as pd
import numpy as np
from flask import Flask, request, jsonify, render_template_string, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# Валютні пари (тікери для реального ринку Forex)
PAIRS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "USDJPY=X",
    "USD/CHF": "USDCHF=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CAD": "USDCAD=X",
    "EUR/GBP": "EURGBP=X",
    "EUR/JPY": "EURJPY=X"
}


# --- БАЗА ДАНИХ ДЛЯ РЕЄСТРАЦІЇ ТА ВХОДУ ---
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (
                     id
                     INTEGER
                     PRIMARY
                     KEY
                     AUTOINCREMENT,
                     email
                     TEXT
                     UNIQUE,
                     password
                     TEXT
                 )''')
    conn.commit()
    conn.close()


init_db()


# --- ЛОГІКА АНАЛІЗУ ХВИЛИННИХ СВІЧОК (M1) ---
def analyze_market(ticker):
    try:
        # Завантажуємо свічки за 1 день з інтервалом 1 хвилина
        df = yf.download(tickers=ticker, period="1d", interval="1m", progress=False)

        if df.empty or len(df) < 25:
            return {"status": "wait", "text": "НАДО ЖДАТЬ (мало данных)", "prob": 0}

        # Вирівнюємо колонки (актуально для нових версій yfinance)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close = df['Close']

        # 1. Розрахунок RSI (14 періодів)
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = float(rsi.iloc[-1])

        # 2. Розрахунок ковзних середніх EMA (швидка 5 хв та повільна 15 хв)
        ema_fast = float(close.ewm(span=5, adjust=False).mean().iloc[-1])
        ema_slow = float(close.ewm(span=15, adjust=False).mean().iloc[-1])

        # 3. Смуги Боллінджера (20 періодів)
        sma20 = close.rolling(window=20).mean()
        std20 = close.rolling(window=20).std()
        upper_band = float((sma20 + (std20 * 2)).iloc[-1])
        lower_band = float((sma20 - (std20 * 2)).iloc[-1])
        last_price = float(close.iloc[-1])

        # Підрахунок балів ймовірності (базова 50%)
        up_score = 50
        down_score = 50

        # Сигнали RSI на розворот або імпульс
        if current_rsi < 30:
            up_score += 22  # Перепроданість -> очікуємо відскок ВВЕРХ
        elif current_rsi > 70:
            down_score += 22  # Перекупленість -> очікуємо відскок ВНИЗ
        elif 52 < current_rsi < 68:
            up_score += 10
        elif 32 < current_rsi < 48:
            down_score += 10

        # Сигнали тренду EMA
        if ema_fast > ema_slow:
            up_score += 12
        else:
            down_score += 12

        # Сигнали відбою від меж Боллінджера
        if last_price <= lower_band:
            up_score += 15
        elif last_price >= upper_band:
            down_score += 15

        # Перевірка порогу у 70%
        if up_score > 70 and up_score > down_score:
            prob = min(up_score, 94)
            return {"status": "up", "text": f"ВВЕРХ ({prob}%)", "prob": prob}
        elif down_score > 70 and down_score > up_score:
            prob = min(down_score, 94)
            return {"status": "down", "text": f"ВНИЗ ({prob}%)", "prob": prob}
        else:
            best_prob = max(up_score, down_score)
            return {"status": "wait", "text": "НАДО ЖДАТЬ", "prob": best_prob}

    except Exception:
        return {"status": "wait", "text": "НАДО ЖДАТЬ (рынок закрыт)", "prob": 0}


# --- ІНТЕРФЕЙС (HTML + CSS + JS) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PO Signal Terminal</title>
    <style>
        :root {
            --bg-color: #070b08;
            --card-bg: #0d1510;
            --green-main: #00ff66;
            --green-dim: #00b347;
            --border-color: #162b1e;
            --text-color: #e6f5ec;
            --yellow: #ffd600;
            --red: #ff3b3b;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
        }

        body {
            background-color: var(--bg-color);
            color: var(--text-color);
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        /* Кнопка повного екрану */
        .fullscreen-btn {
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: var(--card-bg);
            color: var(--green-main);
            border: 1px solid var(--green-main);
            padding: 10px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            letter-spacing: 1px;
            transition: 0.2s;
            z-index: 100;
        }

        .fullscreen-btn:hover {
            background: var(--green-main);
            color: #000;
        }

        /* Екран входу / реєстрації */
        #auth-screen {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100vh;
            padding: 20px;
        }

        .logo-container {
            margin-bottom: 25px;
            text-align: center;
        }

        .logo-container img {
            max-width: 140px;
            max-height: 140px;
            border-radius: 12px;
        }

        .logo-fallback {
            width: 80px;
            height: 80px;
            border: 2px solid var(--green-main);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            color: var(--green-main);
            font-size: 22px;
            margin: 0 auto;
            box-shadow: 0 0 20px rgba(0, 255, 102, 0.2);
        }

        .auth-box {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            padding: 35px;
            border-radius: 12px;
            width: 100%;
            max-width: 360px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.8);
        }

        .auth-box h2 {
            text-align: center;
            margin-bottom: 20px;
            font-weight: 500;
            color: var(--green-main);
            font-size: 20px;
        }

        .input-group {
            margin-bottom: 15px;
        }

        .input-group input {
            width: 100%;
            padding: 12px 15px;
            background: #050806;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            color: var(--text-color);
            font-size: 14px;
            outline: none;
        }

        .input-group input:focus {
            border-color: var(--green-main);
        }

        .btn-primary {
            width: 100%;
            padding: 12px;
            background: var(--green-main);
            color: #000;
            border: none;
            border-radius: 6px;
            font-weight: 700;
            font-size: 15px;
            cursor: pointer;
            transition: 0.2s;
            margin-top: 5px;
        }

        .btn-primary:hover {
            background: var(--green-dim);
        }

        .switch-auth {
            text-align: center;
            margin-top: 15px;
            font-size: 13px;
            color: #7c9685;
            cursor: pointer;
        }

        .switch-auth span {
            color: var(--green-main);
            text-decoration: underline;
        }

        #auth-msg {
            text-align: center;
            margin-top: 12px;
            font-size: 13px;
            color: var(--red);
        }

        /* Головний екран терміналу */
        #app-screen {
            display: none;
            flex-direction: column;
            height: 100vh;
        }

        /* Верхня панель з валютними парами */
        .top-bar {
            display: flex;
            justify-content: center;
            flex-wrap: wrap;
            gap: 10px;
            padding: 20px;
            background: var(--card-bg);
            border-bottom: 1px solid var(--border-color);
        }

        .pair-btn {
            background: #070b08;
            color: var(--text-color);
            border: 1px solid var(--border-color);
            padding: 10px 18px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            transition: 0.2s;
        }

        .pair-btn:hover {
            border-color: var(--green-main);
        }

        .pair-btn.active {
            background: rgba(0, 255, 102, 0.12);
            border-color: var(--green-main);
            color: var(--green-main);
            box-shadow: 0 0 12px rgba(0, 255, 102, 0.2);
        }

        /* Центральна робоча зона */
        .workspace {
            flex: 1;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }

        .selected-pair-title {
            font-size: 16px;
            color: #7c9685;
            margin-bottom: 25px;
            letter-spacing: 2px;
        }

        #analyze-btn {
            display: none;
            padding: 18px 55px;
            font-size: 20px;
            font-weight: 700;
            letter-spacing: 2px;
            text-transform: uppercase;
            background: transparent;
            color: var(--green-main);
            border: 2px solid var(--green-main);
            border-radius: 8px;
            cursor: pointer;
            transition: 0.25s;
            box-shadow: 0 0 20px rgba(0, 255, 102, 0.15);
        }

        #analyze-btn:hover {
            background: var(--green-main);
            color: #000;
            box-shadow: 0 0 30px rgba(0, 255, 102, 0.5);
        }

        #analyze-btn:disabled {
            opacity: 0.5;
            cursor: wait;
        }

        /* Результат аналізу */
        #result-box {
            margin-top: 40px;
            font-size: 38px;
            font-weight: 800;
            letter-spacing: 2px;
            text-align: center;
            min-height: 55px;
            text-transform: uppercase;
        }

        .res-up {
            color: var(--green-main);
            text-shadow: 0 0 25px rgba(0, 255, 102, 0.4);
        }

        .res-down {
            color: var(--red);
            text-shadow: 0 0 25px rgba(255, 59, 59, 0.4);
        }

        .res-wait {
            color: var(--yellow);
            text-shadow: 0 0 20px rgba(255, 214, 0, 0.3);
        }

        .timeframe-badge {
            margin-top: 12px;
            font-size: 12px;
            color: #556e5d;
        }
    </style>
</head>
<body>

    <button class="fullscreen-btn" onclick="toggleFullScreen()">[ Полный экран ]</button>

    <!-- ЕКРАН АВТОРИЗАЦІЇ -->
    <div id="auth-screen">
        <div class="logo-container">
            <img src="/logo.png" alt="Logo" onerror="this.style.display='none'; document.getElementById('fallback-logo').style.display='flex';">
            <div id="fallback-logo" class="logo-fallback" style="display:none;">PO</div>
        </div>

        <div class="auth-box">
            <h2 id="auth-title">Вход в терминал</h2>
            <div class="input-group">
                <input type="email" id="email" placeholder="Почта" required>
            </div>
            <div class="input-group">
                <input type="password" id="password" placeholder="Пароль" required>
            </div>
            <button class="btn-primary" id="auth-submit" onclick="handleAuth()">Войти</button>
            <div class="switch-auth" onclick="toggleAuthMode()">
                <span id="switch-text">Нет аккаунта? Регистрация</span>
            </div>
            <div id="auth-msg"></div>
        </div>
    </div>

    <!-- ГОЛОВНИЙ ЕКРАН -->
    <div id="app-screen">
        <div class="top-bar" id="pairs-container">
            {% for pair in pairs %}
                <button class="pair-btn" onclick="selectPair('{{ pair }}', this)">{{ pair }}</button>
            {% endfor %}
        </div>

        <div class="workspace">
            <div class="selected-pair-title" id="pair-label">ВЫБЕРИТЕ ВАЛЮТНУЮ ПАРУ СВЕРХУ</div>
            <button id="analyze-btn" onclick="runAnalysis()">Анализ</button>
            <div id="result-box"></div>
            <div class="timeframe-badge" id="tf-info" style="display:none;">Таймфрейм экспирации: 1 минута (M1)</div>
        </div>
    </div>

    <script>
        let isRegister = false;
        let selectedPair = null;

        function toggleFullScreen() {
            if (!document.fullscreenElement) {
                document.documentElement.requestFullscreen().catch(err => {});
            } else {
                if (document.exitFullscreen) {
                    document.exitFullscreen();
                }
            }
        }

        function toggleAuthMode() {
            isRegister = !isRegister;
            document.getElementById('auth-title').innerText = isRegister ? 'Регистрация' : 'Вход в терминал';
            document.getElementById('auth-submit').innerText = isRegister ? 'Создать аккаунт' : 'Войти';
            document.getElementById('switch-

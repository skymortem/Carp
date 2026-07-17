# Carp 🚗

**Carp** — это self-hosted веб-приложение для учёта расходов на автомобиль, отслеживания пробега, расхода топлива и моточасов — с **автоматическим сбором данных** через GSM-сигнализации StarLine.

Carp сам забирает телеметрию с CAN-шины автомобиля через StarLine API, сохраняет и визуализирует на дашборде.

---

## ✨ Возможности

### ✅ Уже работает

- **🔐 Аутентификация** — регистрация/вход с JWT в куке
- **📡 Интеграция со StarLine** — полный OAuth2-подобный цикл авторизации (SLID + WebAPI)
- **🚘 Автопоиск устройства** — находит твою сигнализацию по WebAPI user_id
- **⛽ Уровень топлива** — литры в баке с CAN-шины (`obd.fuel_litres`)
- **📏 Пробег** — одометр с CAN-шины (`obd.mileage`) *когда двигатель заведён*
- **🧭 GPS-позиция** — координаты, скорость, статус движения
- **🔋 Состояние авто** — напряжение АКБ, температура салона/двигателя, уровень GSM, зажигание
- **⏱ Счётчик моточасов** — отслеживание `state.motohrs`
- **🔧 План ТО** — операции по обслуживанию с интервалами (км/месяцы/моточасы), автообновление пробега при отметке «Выполнено»
- **📊 Дашборд** — тёмная тема, карточки в реальном времени + графики Chart.js
- **🌐 HTMX + Alpine.js** — SPA-подобный интерфейс без тяжёлого фронтенда
- **⏰ Автосбор раз в час** — APScheduler в фоне собирает данные

### 🗺 В планах

- **⛽ Автодетект заправок** — определять заправки по скачкам уровня топлива между сниппетами
- **💰 Ручной ввод расходов** — ремонты, страховка, налоги, парковки, мойки
- **🚘 Несколько машин** — поддержка гаража
- **📈 Продвинутые отчёты** — стоимость км/месяц, эффективность топлива, стиль вождения
- **🔔 Уведомления** — Telegram / email о приближающемся ТО
- **🐳 Docker** — деплой одной командой

---

## 🏗 Стек технологий

| Слой | Технология |
|------|-----------|
| **Бэкенд** | Python 3.11+, FastAPI, SQLAlchemy (async), SQLite |
| **Фронтенд** | Jinja2, Tailwind CSS, HTMX 2.x, Alpine.js 3.x |
| **Графики** | Chart.js 4.x |
| **Аутентификация** | JWT (python-jose) + bcrypt |
| **Внешнее API** | StarLine (SLID + WebAPI через httpx) |
| **Планировщик** | APScheduler (сбор данных каждый час) |
| **Миграции** | Alembic (планируется) |

---

## 🚀 Быстрый старт

```bash
# Клонирование
git clone https://github.com/skymortem/carp.git
cd carp/backend

# Виртуальное окружение
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Настройка
cp .env.example .env
# Заполни: STARLINE_APP_ID, STARLINE_APP_SECRET, STARLINE_LOGIN, STARLINE_PASSWORD
# Сгенерируй SECRET_KEY: python3 -c "import secrets; print(secrets.token_hex(32))"

# Запуск
PYTHONPATH="" uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Открой **http://localhost:8000**, зарегистрируйся и перейди в настройки, чтобы подключить свой StarLine аккаунт.

---

## 📡 Интеграция со StarLine

Carp выполняет многошаговую авторизацию:

1. **Код приложения** — `GET id.starline.ru/apiV3/application/getCode`
2. **Токен приложения** — `GET id.starline.ru/apiV3/application/getToken` (живёт 4ч)
3. **Токен пользователя** — `POST id.starline.ru/apiV3/user/login` (живёт 1 год)
4. **SLNet токен** — `POST developer.starline.ru/json/v2/auth.slid` → cookie (живёт 24ч)

После авторизации Carp запрашивает данные через `GET /json/v3/device/{id}/data`.

> ⚠️ **Важно:** WebAPI user_id (из шага 4) **отличается** от SLID user_id. Carp обрабатывает это автоматически.

---

## 🗂 Структура проекта

```
backend/
├── app/
│   ├── main.py              # Точка входа FastAPI
│   ├── config.py            # Настройки из .env (pydantic-settings)
│   ├── database.py          # Async SQLAlchemy engine + session
│   ├── models/
│   │   ├── user.py          # Пользователь
│   │   └── car.py           # Car, StarSnap, ServicePlan
│   ├── services/
│   │   ├── auth.py          # JWT + bcrypt
│   │   ├── starline.py      # StarLine API клиент
│   │   └── scheduler.py     # Планировщик автосбора
│   ├── routers/
│   │   ├── auth.py          # /auth/register, /auth/login
│   │   ├── starline.py      # /starline/connect, /starline/fetch, /starline/reset-motohours
│   │   ├── dashboard.py     # Главная, дашборд, страницы
│   │   └── service_plan.py  # /service-plan/add, /done, /delete
│   └── templates/           # Jinja2 + Tailwind + Alpine.js
└── .env                     # Секреты (не коммитить!)
```

---

## 📄 Лицензия

Apache License Version 2.0

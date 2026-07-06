
# !under construction!

# Carp 🚗

**Carp** is a self-hosted web application for tracking car expenses, mileage, fuel consumption, and engine hours — with **automatic data collection** via StarLine GSM alarm systems.

Instead of manually punching numbers into a spreadsheet, Carp fetches real telemetry from your car's CAN bus through the StarLine API, stores it, and visualizes it on a dashboard.

---

## ✨ Features

### ✅ Currently Working

- **🔐 User authentication** — register/login with JWT stored in cookies
- **📡 StarLine integration** — full OAuth2-like auth flow with SLID + WebAPI
- **🚘 Auto device discovery** — finds your StarLine device by WebAPI user ID
- **⛽ Fuel level tracking** — live fuel litres from CAN bus (`obd.fuel_litres`)
- **📏 Mileage tracking** — odometer from CAN bus (`obd.mileage`) *when engine is running*
- **🧭 GPS position** — coordinates, speed, movement status
- **🔋 Vehicle state** — battery voltage, cabin/engine temperature, GSM signal, ignition status
- **⏱ Engine hours counter** — tracks `state.motohrs`, resettable for oil change intervals
- **📊 Dashboard** — dark-themed UI with real-time cards + Chart.js graphs
- **🌐 HTMX + Alpine.js** — SPA-like experience without a heavy frontend framework

### 🗺 Planned

- **⏰ Automatic hourly collection** — cron/APScheduler to fetch StarLine data periodically
- **⛽ Fuel fill detection** — auto-detect refuels from fuel level jumps between snapshots
- **🔧 Service scheduler** — track oil changes, filters, spark plugs, timing belt intervals based on mileage or engine hours
- **💰 Manual expense log** — add repairs, insurance, taxes, parking, washes
- **🚘 Multiple cars** — support for a garage of vehicles
- **📈 Advanced reports** — cost per km/month, fuel efficiency over time, driving score
- **🔔 Notifications** — Telegram / email alerts for upcoming maintenance
- **🐳 Docker** — one-command deploy with prebuilt image

---

## 🏗 Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.11+, FastAPI, SQLAlchemy (async), SQLite |
| **Frontend** | Jinja2 templates, Tailwind CSS, HTMX 2.x, Alpine.js 3.x |
| **Charts** | Chart.js 4.x |
| **Auth** | JWT (python-jose) + bcrypt |
| **External API** | StarLine (SLID + WebAPI via httpx) |
| **Migrations** | Alembic (planned) |

---

## 🚀 Quick Start

```bash
# Clone
git clone https://github.com/yourname/carp.git
cd carp/backend

# Virtual environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt  # or: pip install fastapi uvicorn sqlalchemy aiosqlite ...

# Config
cp .env.example .env
# Fill in: STARLINE_APP_ID, STARLINE_APP_SECRET, STARLINE_LOGIN, STARLINE_PASSWORD

# Run
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000**, register, and head to the setup page to connect your StarLine account.

---

## 📡 StarLine Integration

Carp performs a multi-step authentication with StarLine:

1. **App code** — `GET id.starline.ru/apiV3/application/getCode`
2. **App token** — `GET id.starline.ru/apiV3/application/getToken` (4h lifetime)
3. **User token** — `POST id.starline.ru/apiV3/user/login` (1yr lifetime)
4. **SLNet token** — `POST developer.starline.ru/json/v2/auth.slid` → cookie (24h)

Once authenticated, Carp queries `GET /json/v3/device/{id}/data` for telemetry.

> ⚠️ **Note:** The WebAPI user ID (from step 4) **differs** from the SLID user ID. Carp handles this transparently.

---

## 🗂 Project Structure

```
backend/
├── app/
│   ├── main.py              # FastAPI app entrypoint
│   ├── config.py            # Pydantic settings (reads .env)
│   ├── database.py          # Async SQLAlchemy engine + session
│   ├── models/
│   │   ├── user.py          # User model
│   │   └── car.py           # Car + StarSnap models
│   ├── services/
│   │   ├── auth.py          # JWT + bcrypt helpers
│   │   └── starline.py      # StarLine API client
│   ├── routers/
│   │   ├── auth.py          # /auth/register, /auth/login
│   │   ├── starline.py      # /starline/connect, /starline/fetch, /starline/reset-motohours
│   │   └── dashboard.py     # HTML page routes
│   └── templates/           # Jinja2 + Tailwind + Alpine.js
└── .env                     # Your secrets (not committed)
```

---

## 📄 License

 Apache License Version 2.0

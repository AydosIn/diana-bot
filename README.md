# Diana Bot

Diana is a Telegram chatbot that is designed to feel like a real person texting back: short, lowercase, direct, honest, and casual.

The personality lives in `diana_persona.md`. The bot reloads that file every time it replies, so you can change Diana's tone without changing Python code.

## Stack

- Python 3.11+
- `python-telegram-bot`
- OpenAI API
- SQLite via `aiosqlite`
- VPS deployment with `systemd`

## Project Structure

```text
diana-bot/
├── bot/
│   ├── __init__.py
│   ├── config.py
│   ├── handlers.py
│   ├── memory.py
│   └── openai_client.py
├── deploy/
│   └── diana-bot.service.example
├── diana_persona.md
├── main.py
├── requirements.txt
├── .env.example
└── README.md
```

## Local Setup

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Activate it on Linux/macOS:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create `.env` from the example:

```bash
cp .env.example .env
```

Fill in:

```text
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4o-mini
DATABASE_PATH=database.db
MAX_HISTORY_MESSAGES=20
```

Run the bot:

```bash
python main.py
```

## Telegram Setup

1. Open [@BotFather](https://t.me/BotFather).
2. Create a new bot.
3. Copy the token into `.env` as `TELEGRAM_BOT_TOKEN`.
4. Do not set a command menu if you want Diana to feel less like a bot.

## How Memory Works

SQLite stores:

- Telegram user profile basics
- User messages
- Diana's replies

For each new message, Diana receives:

1. `diana_persona.md`
2. The last `MAX_HISTORY_MESSAGES` messages
3. The user's newest message

That gives her lightweight memory without overcomplicating the MVP.

## VPS Deployment

Recommended simple path:

```bash
sudo mkdir -p /opt/diana-bot
sudo chown $USER:$USER /opt/diana-bot
```

Copy the project to `/opt/diana-bot`, then:

```bash
cd /opt/diana-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
```

Install the service:

```bash
sudo cp deploy/diana-bot.service.example /etc/systemd/system/diana-bot.service
sudo systemctl daemon-reload
sudo systemctl enable diana-bot
sudo systemctl start diana-bot
```

Check logs:

```bash
sudo journalctl -u diana-bot -f
```

Restart after code changes:

```bash
sudo systemctl restart diana-bot
```

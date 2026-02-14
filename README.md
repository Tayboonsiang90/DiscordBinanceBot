# Binance Price Alert Discord Bot

A Python Discord bot that monitors Binance 1-minute candles and sends Discord alerts when price targets (strike prices) are hit. Supports both **up** alerts (candle High >= strike) and **down** alerts (candle Low <= strike).

## Features

- **Touch alerts:** Triggers when price touches the strike during a 1-minute candle (Low <= strike <= High)
- **Notes:** Add a note to each alert; it appears in the embed when the alert fires
- **Message commands:** `!setchannel`, `!addalert`, `!listalerts`, `!removealert`, `!help`
- **Binance-only:** Uses Binance spot data (e.g. https://www.binance.com/en/trade/BTC_USDT, 1m chart)
- **No historical scan:** Only monitors candles from bot startup onward

## Local Setup

1. **Create virtual environment and install dependencies**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   pip install -r requirements.txt
   ```

2. **Configure environment**
   - Copy `.env.example` to `.env`
   - Set `DISCORD_TOKEN` (from [Discord Developer Portal](https://discord.com/developers/applications) → your app → Bot → Reset Token)
   - Optional: Set `DISCORD_GUILD_ID` for faster slash command registration (right-click server → Copy Server ID; enable Developer Mode in Discord settings)

3. **Run the bot**
   ```bash
   python -m src.bot
   ```

4. **Configure the announcement channel**
   - In your Discord server, go to the channel where you want alerts
   - Type `!setchannel`

5. **Add alerts**
   - `!addalert BTC 100000 Key resistance`
   - `!addalert ETH 2000 Support level`

---

## Deploy to Render

### Prerequisites

- [Render](https://render.com) account
- [GitHub](https://github.com) account
- Discord bot token and guild ID

### Step 1: Push to GitHub

1. Create a new repository on GitHub (e.g. `binance-alert-bot`)
2. In your project folder, run:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/binance-alert-bot.git
   git push -u origin main
   ```

### Step 2: Create a Background Worker on Render

1. Log in to [Render Dashboard](https://dashboard.render.com)
2. Click **New +** → **Background Worker**
3. Connect your GitHub account if needed
4. Select the repository (e.g. `binance-alert-bot`)

### Step 3: Configure the Worker

| Field | Value |
|-------|-------|
| **Name** | `binance-alert-bot` (or any name) |
| **Environment** | Python |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python -m src.bot` |

### Step 4: Add Environment Variables

In the **Environment** section:

| Key | Value | Secret |
|-----|-------|--------|
| `DISCORD_TOKEN` | Your bot token | Yes (toggle "Secret") |
| `DISCORD_GUILD_ID` | Your server/guild ID | No |

- **DISCORD_TOKEN:** From [Discord Developer Portal](https://discord.com/developers/applications) → Your Application → Bot → Reset Token → Copy
- **DISCORD_GUILD_ID:** Optional; bot syncs commands to all servers it's in

### Step 5: Deploy

Click **Create Background Worker**. Render will build and start the bot. Check the **Logs** tab for output.

### Step 6: Configure in Discord

1. Wait for the bot to come online (status shows green in Discord)
2. In your announcement channel, type: `!setchannel`
3. Add alerts with `!addalert BTC 100000 up` (etc.)

---

## Render Notes

- **Worker vs Web Service:** This is a **Background Worker** (no HTTP server). Use "Background Worker" when creating the service.
- **Ephemeral disk:** On Render, the filesystem is ephemeral. Alerts stored in SQLite reset on redeploy. Re-add alerts via `/addalert` after a deploy.
- **Free tier:** Render's free tier may spin down workers after inactivity; paid plans keep them running 24/7.
- **Blueprint (render.yaml):** The repo includes `render.yaml`. You can use **Blueprint** to deploy from the YAML, but you still must add `DISCORD_TOKEN` in the dashboard.

---

## Commands

Use the `!` prefix (message commands—work without slash commands):

| Command | Description |
|---------|-------------|
| `!setchannel` | Set this channel for price alerts |
| `!addalert <ticker> <price> [note]` | Add alert (fires when price touches strike), e.g. `!addalert BTC 100000 Key level` |
| `!removealert <id>` | Remove alert by ID |
| `!listalerts` | List all active alerts |
| `!help` | Show command help |

---

## Invite the Bot to Your Server

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Select your application → OAuth2 → URL Generator
3. **Scopes:** `bot`, `applications.commands`
4. **Bot Permissions:** Check **Administrator** (simplest—avoids permission issues; this bot does not handle sensitive data)
5. Copy the generated URL and open it in a browser
6. Select any server and authorize—the bot works in any server you add it to

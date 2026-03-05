# 🤖 RAI Validator Telegram Bot

A feature-rich Telegram bot for real-time monitoring of your **Republic AI Network** validator node — with automatic governance alerts, jail detection, and active set analysis.

🔗 **Chain:** `raitestnet_77701-1` · **Language:** Python 3.10+

---

## ✨ Features

### 📡 Commands

| Command | Description |
|---|---|
| `/status` | Validator status (bonded/jailed), stake, commission, block height |
| `/balance` | Wallet balance and accumulated staking rewards |
| `/uptime` | Signing uptime percentage and missed block count |
| `/rank` | Your position within the active validator set |
| `/activeset` | Active set entry analysis — how many tokens you still need |
| `/rewards` | Current pending staking rewards |
| `/proposals` | All governance proposals currently in voting period |
| `/network` | Chain ID, sync status, total bonded power, active validator count |
| `/help` | Command list |

### 🚨 Automatic Alerts

| Alert | Trigger |
|---|---|
| 🗳️ **New Governance Proposal** | Fires instantly when a new proposal enters the voting period |
| 🔴 **Validator Jailed** | Fires the moment your validator gets jailed |
| ✅ **Validator Unjailed** | Fires when your validator recovers |
| ⚠️ **Active Set Warning** | Fires when your stake margin drops below 5% of the lowest active validator |

---

## 🚀 Stack

- **Language:** Python 3.10+
- **Bot Framework:** `python-telegram-bot` v21
- **HTTP Client:** `aiohttp` (async)
- **Data Source:** Tendermint RPC + Cosmos REST API
- **State:** Local `state.json` (proposal tracking, jail history)

---

## ⚙️ Setup Guide

### 1. Create a Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` → follow prompts → name it (e.g. `RAI Validator Bot`)
3. Copy the **API Token** you receive

### 2. Get Your Chat ID

1. Add the bot to your personal chat or group
2. Send any message to the bot
3. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
4. Find `"chat":{"id":XXXXXXX}` — that number is your Chat ID

### 3. Clone & Install

```bash
git clone https://github.com/YOUR_USERNAME/rai-telegram-bot
cd rai-telegram-bot

apt install python3 python3-pip -y
pip3 install -r requirements.txt --break-system-packages
```

### 4. Configure the Bot

Open `rai_bot.py` and fill in your details at the top:

```python
TELEGRAM_TOKEN = "YOUR_BOT_TOKEN"          # from BotFather
CHAT_ID        = "YOUR_CHAT_ID"            # your personal or group chat id
VALIDATOR_ADDR = "raivaloper1..."           # your validator address
WALLET_ADDR    = "rai1..."                  # your wallet address
MONIKER        = "MyValidator"             # your validator name
```

> **Tip:** You can also set these as environment variables instead of editing the file.

### 5. Run as a System Service

```bash
sudo tee /etc/systemd/system/rai_telegram_bot.service > /dev/null <<EOF
[Unit]
Description=RAI Validator Telegram Bot
After=network-online.target

[Service]
User=root
WorkingDirectory=/root/rai-telegram-bot
ExecStart=/usr/bin/python3 /root/rai-telegram-bot/rai_bot.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable rai_telegram_bot
sudo systemctl start rai_telegram_bot
```

### 6. Verify

```bash
# Check if bot is running
systemctl status rai_telegram_bot

# Watch live logs
journalctl -u rai_telegram_bot -f
```

---

## 📡 API Endpoints Used

| Endpoint | Purpose |
|---|---|
| `/cosmos/staking/v1beta1/validators/{addr}` | Validator info (stake, status, commission) |
| `/cosmos/staking/v1beta1/validators` | Full validator list for ranking |
| `/cosmos/bank/v1beta1/balances/{addr}` | Wallet balance |
| `/cosmos/distribution/v1beta1/delegators/{addr}/rewards` | Staking rewards |
| `/cosmos/gov/v1beta1/proposals` | Governance proposals |
| `/cosmos/slashing/v1beta1/signing_infos/{addr}` | Missed blocks & uptime |
| `/rpc/status` | Block height, sync status, chain ID |

---

## 🔔 Alert Configuration

You can adjust alert check intervals in `rai_bot.py`:

```python
GOVERNANCE_CHECK_INTERVAL = 60    # seconds — how often to check for new proposals
VALIDATOR_CHECK_INTERVAL  = 120   # seconds — how often to check jail status
ACTIVE_SET_CHECK_INTERVAL = 300   # seconds — how often to check active set margin
```

---

## 🛠️ Common Errors

| Error | Solution |
|---|---|
| `Unauthorized` | Bot token is wrong — re-copy from BotFather |
| `Chat not found` | Make sure you sent at least one message to the bot first |
| `aiohttp.ClientConnectorError` | RPC/REST endpoint unreachable — check node sync |
| `KeyError: 'result'` | Node is still syncing — wait for full sync |

---

## 🗂️ File Structure

```
rai-telegram-bot/
├── rai_bot.py        # Main bot — commands + background alert tasks
├── requirements.txt  # Python dependencies
├── state.json        # Auto-generated — tracks seen proposals & jail state
└── README.md
```

---

> Built with ❤️ for the Republic AI Network validator community.
> Chain ID: `raitestnet_77701-1`

import asyncio
import logging
import os
import json
import time
from datetime import datetime, timezone

import aiohttp
from telegram import Bot, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
from telegram.constants import ParseMode

# ─────────────────────────────────────────────
#  CONFIG  (edit these values or use .env)
# ─────────────────────────────────────────────
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN")
CHAT_ID           = os.getenv("CHAT_ID", "YOUR_CHAT_ID")          # personal / group chat id
VALIDATOR_ADDR    = os.getenv("VALIDATOR_ADDR", "raivaloper1...")  # raivaloper1xxx
WALLET_ADDR       = os.getenv("WALLET_ADDR", "rai1...")            # rai1xxx
MONIKER           = os.getenv("MONIKER", "MyValidator")

RPC_URL           = os.getenv("RPC_URL", "https://rpc.republicai.io")
REST_URL          = os.getenv("REST_URL", "https://rest.republicai.io")

# Alert intervals (seconds)
GOVERNANCE_CHECK_INTERVAL = 60    # check for new proposals every 60s
VALIDATOR_CHECK_INTERVAL  = 120   # check validator health every 2 min
ACTIVE_SET_CHECK_INTERVAL = 300   # check active set ranking every 5 min

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  STATE  (in-memory, persisted to state.json)
# ─────────────────────────────────────────────
STATE_FILE = "state.json"

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"seen_proposals": [], "last_jailed": False}

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

state = load_state()

# ─────────────────────────────────────────────
#  HTTP HELPERS
# ─────────────────────────────────────────────
async def get(session: aiohttp.ClientSession, url: str) -> dict | None:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                return await r.json()
    except Exception as e:
        logger.warning(f"GET {url} failed: {e}")
    return None

# ─────────────────────────────────────────────
#  CHAIN DATA FETCHERS
# ─────────────────────────────────────────────
async def fetch_validator_info(session: aiohttp.ClientSession) -> dict | None:
    url = f"{REST_URL}/cosmos/staking/v1beta1/validators/{VALIDATOR_ADDR}"
    data = await get(session, url)
    if data and "validator" in data:
        return data["validator"]
    return None

async def fetch_balance(session: aiohttp.ClientSession) -> str:
    url = f"{REST_URL}/cosmos/bank/v1beta1/balances/{WALLET_ADDR}"
    data = await get(session, url)
    if data and "balances" in data:
        for coin in data["balances"]:
            if coin["denom"] == "arai":
                raw = int(coin["amount"])
                return f"{raw / 1e18:.4f} RAI"
    return "0 RAI"

async def fetch_all_validators(session: aiohttp.ClientSession) -> list:
    url = f"{REST_URL}/cosmos/staking/v1beta1/validators?status=BOND_STATUS_BONDED&pagination.limit=200"
    data = await get(session, url)
    if data and "validators" in data:
        return data["validators"]
    return []

async def fetch_block_height(session: aiohttp.ClientSession) -> int | None:
    data = await get(session, f"{RPC_URL}/status")
    if data:
        try:
            return int(data["result"]["sync_info"]["latest_block_height"])
        except (KeyError, TypeError):
            pass
    return None

async def fetch_proposals(session: aiohttp.ClientSession) -> list:
    url = f"{REST_URL}/cosmos/gov/v1beta1/proposals?proposal_status=2"  # status=2 = VOTING_PERIOD
    data = await get(session, url)
    if data and "proposals" in data:
        return data["proposals"]
    return []

async def fetch_rewards(session: aiohttp.ClientSession) -> str:
    url = f"{REST_URL}/cosmos/distribution/v1beta1/delegators/{WALLET_ADDR}/rewards"
    data = await get(session, url)
    if data and "total" in data:
        for coin in data["total"]:
            if coin["denom"] == "arai":
                raw = float(coin["amount"])
                return f"{raw / 1e18:.4f} RAI"
    return "0 RAI"

async def fetch_signed_blocks(session: aiohttp.ClientSession) -> tuple[int, int]:
    """Returns (signed, total) for last 100 blocks."""
    height_data = await get(session, f"{RPC_URL}/status")
    if not height_data:
        return 0, 0
    try:
        latest = int(height_data["result"]["sync_info"]["latest_block_height"])
    except (KeyError, TypeError):
        return 0, 0

    signed = 0
    total = 0
    for h in range(latest - 99, latest + 1):
        commit = await get(session, f"{RPC_URL}/commit?height={h}")
        if not commit:
            continue
        sigs = commit.get("result", {}).get("signed_header", {}).get("commit", {}).get("signatures", [])
        total += 1
        for sig in sigs:
            if sig.get("validator_address") and sig.get("block_id_flag") == 2:
                signed += 1
                break
    return signed, total

# ─────────────────────────────────────────────
#  FORMATTING HELPERS
# ─────────────────────────────────────────────
def short_addr(addr: str, chars: int = 8) -> str:
    return f"{addr[:chars]}...{addr[-6:]}" if len(addr) > chars + 6 else addr

def status_emoji(jailed: bool, bonded: bool) -> str:
    if jailed:
        return "🔴 Jailed"
    if bonded:
        return "🟢 Active"
    return "🟡 Inactive"

def tokens_to_rai(tokens: str) -> str:
    try:
        return f"{int(tokens) / 1e18:.2f} RAI"
    except (ValueError, TypeError):
        return tokens

# ─────────────────────────────────────────────
#  TELEGRAM COMMANDS
# ─────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *RAI Validator Telegram Bot*\n\n"
        "Kullanılabilir komutlar:\n\n"
        "`/status`     — Validator durumu\n"
        "`/balance`    — Cüzdan bakiyesi\n"
        "`/uptime`     — Uptime & miss sayısı\n"
        "`/rank`       — Aktif setteki sıralama\n"
        "`/activeset`  — Aktif sete girme analizi\n"
        "`/rewards`    — Birikmiş ödüller\n"
        "`/proposals`  — Açık governance oylamaları\n"
        "`/network`    — Ağ istatistikleri\n"
        "`/help`       — Bu menü\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    async with aiohttp.ClientSession() as session:
        val = await fetch_validator_info(session)
        height = await fetch_block_height(session)

    if not val:
        await update.message.reply_text("❌ Validator bilgisi alınamadı.")
        return

    jailed = val.get("jailed", False)
    bonded = val.get("status") == "BOND_STATUS_BONDED"
    tokens = tokens_to_rai(val.get("tokens", "0"))
    commission = float(val.get("commission", {}).get("commission_rates", {}).get("rate", "0")) * 100
    moniker = val.get("description", {}).get("moniker", MONIKER)

    text = (
        f"🛡️ *Validator Status*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📛 Moniker : `{moniker}`\n"
        f"🔗 Adres   : `{short_addr(VALIDATOR_ADDR)}`\n"
        f"📊 Durum   : {status_emoji(jailed, bonded)}\n"
        f"💰 Stake   : `{tokens}`\n"
        f"💸 Komisyon: `{commission:.1f}%`\n"
        f"📦 Blok    : `{height:,}`\n"
        f"🕐 Zaman   : `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Sorgulanıyor...")
    async with aiohttp.ClientSession() as session:
        balance = await fetch_balance(session)
        rewards = await fetch_rewards(session)

    text = (
        f"💳 *Cüzdan Bakiyesi*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👛 Adres   : `{short_addr(WALLET_ADDR)}`\n"
        f"💰 Bakiye  : `{balance}`\n"
        f"🎁 Ödüller : `{rewards}`\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_rank(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Validator listesi çekiliyor...")
    async with aiohttp.ClientSession() as session:
        validators = await fetch_all_validators(session)

    if not validators:
        await update.message.reply_text("❌ Validator listesi alınamadı.")
        return

    sorted_vals = sorted(validators, key=lambda v: int(v.get("tokens", 0)), reverse=True)
    rank = next((i + 1 for i, v in enumerate(sorted_vals) if v.get("operator_address") == VALIDATOR_ADDR), None)
    total = len(sorted_vals)

    text = (
        f"🏆 *Validator Sıralaması*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📋 Toplam Aktif : `{total}`\n"
        f"🥇 Sıralaman    : `#{rank}`\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_activeset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Aktif set analizi yapılıyor...")
    async with aiohttp.ClientSession() as session:
        all_vals_data = await get(session, f"{REST_URL}/cosmos/staking/v1beta1/validators?pagination.limit=500")
        my_val = await fetch_validator_info(session)

    if not all_vals_data or not my_val:
        await update.message.reply_text("❌ Veri alınamadı.")
        return

    all_vals = all_vals_data.get("validators", [])
    bonded = [v for v in all_vals if v.get("status") == "BOND_STATUS_BONDED"]
    unbonded = [v for v in all_vals if v.get("status") != "BOND_STATUS_BONDED"]

    bonded_sorted = sorted(bonded, key=lambda v: int(v.get("tokens", 0)))
    lowest_active_tokens = int(bonded_sorted[0]["tokens"]) if bonded_sorted else 0
    my_tokens = int(my_val.get("tokens", 0))

    diff = lowest_active_tokens - my_tokens
    diff_rai = diff / 1e18

    in_active = my_val.get("status") == "BOND_STATUS_BONDED"

    if in_active:
        margin_rai = (my_tokens - lowest_active_tokens) / 1e18
        text = (
            f"✅ *Aktif Set Analizi*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 Durum        : Aktif Settesin!\n"
            f"🔢 Aktif Val.   : `{len(bonded)}`\n"
            f"💎 Stake'in     : `{my_tokens / 1e18:.2f} RAI`\n"
            f"📉 En Düşük Aktif: `{lowest_active_tokens / 1e18:.2f} RAI`\n"
            f"🛡️ Güvenlik Marjı: `+{margin_rai:.2f} RAI`\n"
        )
    else:
        text = (
            f"⚠️ *Aktif Set Analizi*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 Durum          : Aktif Dışındasın\n"
            f"🔢 Aktif Val.     : `{len(bonded)}`\n"
            f"💎 Stake'in       : `{my_tokens / 1e18:.2f} RAI`\n"
            f"🎯 Gereken Min.   : `{lowest_active_tokens / 1e18:.2f} RAI`\n"
            f"➕ Eksik Miktar   : `{diff_rai:.2f} RAI`\n\n"
            f"💡 _Aktif sete girebilmek için yaklaşık *{diff_rai:.2f} RAI* daha stake etmen gerekiyor._"
        )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_uptime(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Son bloklar kontrol ediliyor (bu biraz sürebilir)...")
    async with aiohttp.ClientSession() as session:
        val = await fetch_validator_info(session)

    if not val:
        await update.message.reply_text("❌ Validator bilgisi alınamadı.")
        return

    # Use missed_blocks_counter from slashing info if available
    async with aiohttp.ClientSession() as session:
        slash_url = f"{REST_URL}/cosmos/slashing/v1beta1/signing_infos/{VALIDATOR_ADDR}"
        slash_data = await get(session, slash_url)

    missed = 0
    if slash_data and "val_signing_info" in slash_data:
        missed = int(slash_data["val_signing_info"].get("missed_blocks_counter", 0))
        window = 10000  # typical slashing window
        signed = window - missed
        uptime = (signed / window) * 100
    else:
        uptime = 99.0
        missed = 0

    bar_filled = int(uptime / 5)
    bar = "█" * bar_filled + "░" * (20 - bar_filled)

    text = (
        f"📡 *Validator Uptime*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⬛ `[{bar}]`\n"
        f"✅ Uptime   : `{uptime:.2f}%`\n"
        f"❌ Miss     : `{missed}` blok\n"
        f"📊 Pencere  : Son `10,000` blok\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_proposals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Governance sorgulanıyor...")
    async with aiohttp.ClientSession() as session:
        proposals = await fetch_proposals(session)

    if not proposals:
        await update.message.reply_text("✅ Şu an oylamada bekleyen proposal yok.")
        return

    text = f"🗳️ *Açık Governance Oylamaları* ({len(proposals)} adet)\n━━━━━━━━━━━━━━━━━━\n\n"
    for p in proposals[:5]:
        pid    = p.get("proposal_id", "?")
        title  = p.get("content", {}).get("title", "Başlık yok")
        end    = p.get("voting_end_time", "")[:10]
        text += f"🔹 *#{pid}* — {title}\n   ⏰ Bitiş: `{end}`\n\n"

    text += "_/status komutuyla validator durumunu da kontrol edebilirsin._"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_rewards(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    async with aiohttp.ClientSession() as session:
        rewards = await fetch_rewards(session)
        balance = await fetch_balance(session)

    text = (
        f"🎁 *Staking Ödülleri*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Bakiye  : `{balance}`\n"
        f"🎁 Ödüller : `{rewards}`\n"
        f"🕐 Zaman   : `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_network(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Ağ bilgisi alınıyor...")
    async with aiohttp.ClientSession() as session:
        status = await get(session, f"{RPC_URL}/status")
        validators = await fetch_all_validators(session)

    if not status:
        await update.message.reply_text("❌ RPC bağlantısı kurulamadı.")
        return

    sync = status.get("result", {}).get("sync_info", {})
    height   = sync.get("latest_block_height", "?")
    catching = sync.get("catching_up", False)
    chain_id = status.get("result", {}).get("node_info", {}).get("network", "?")
    total_power = sum(int(v.get("tokens", 0)) for v in validators)

    text = (
        f"🌐 *Network Durumu*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔗 Chain ID    : `{chain_id}`\n"
        f"📦 Blok        : `{int(height):,}`\n"
        f"🔄 Sync        : {'⚠️ Catching Up' if catching else '✅ Synced'}\n"
        f"👥 Aktif Val.  : `{len(validators)}`\n"
        f"💎 Total Power : `{total_power / 1e18:.0f} RAI`\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────
#  BACKGROUND ALERT TASKS
# ─────────────────────────────────────────────
async def alert_governance(bot: Bot):
    """Sends Telegram alert when a new governance proposal enters voting period."""
    global state
    logger.info("Governance alert task started.")
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                proposals = await fetch_proposals(session)
            for p in proposals:
                pid = p.get("proposal_id")
                if pid and pid not in state["seen_proposals"]:
                    state["seen_proposals"].append(pid)
                    save_state(state)
                    title = p.get("content", {}).get("title", "Başlık yok")
                    desc  = p.get("content", {}).get("description", "")[:200]
                    end   = p.get("voting_end_time", "")[:10]
                    text = (
                        f"🚨 *YENİ GOVERNANCE PROPOSAL!*\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🔹 ID     : `#{pid}`\n"
                        f"📋 Başlık : *{title}*\n"
                        f"📝 Özet   : _{desc}..._\n"
                        f"⏰ Bitiş  : `{end}`\n\n"
                        f"👉 Oy vermek için: `/proposals`"
                    )
                    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Governance check error: {e}")
        await asyncio.sleep(GOVERNANCE_CHECK_INTERVAL)

async def alert_jail(bot: Bot):
    """Sends Telegram alert if validator gets jailed."""
    global state
    logger.info("Jail alert task started.")
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                val = await fetch_validator_info(session)
            if val:
                jailed = val.get("jailed", False)
                if jailed and not state.get("last_jailed"):
                    state["last_jailed"] = True
                    save_state(state)
                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text=(
                            "🚨 *VALİDATOR JAILED!*\n"
                            "━━━━━━━━━━━━━━━━━━\n"
                            f"📛 Moniker : `{MONIKER}`\n"
                            f"🔗 Adres   : `{short_addr(VALIDATOR_ADDR)}`\n\n"
                            "⚠️ Hemen unjail işlemi yapılması gerekiyor!"
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )
                elif not jailed and state.get("last_jailed"):
                    state["last_jailed"] = False
                    save_state(state)
                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text="✅ *Validator başarıyla unjailed edildi!*",
                        parse_mode=ParseMode.MARKDOWN,
                    )
        except Exception as e:
            logger.error(f"Jail check error: {e}")
        await asyncio.sleep(VALIDATOR_CHECK_INTERVAL)

async def alert_active_set(bot: Bot):
    """Warns if validator is close to falling out of active set."""
    logger.info("Active set alert task started.")
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                all_data = await get(session, f"{REST_URL}/cosmos/staking/v1beta1/validators?pagination.limit=500")
                my_val   = await fetch_validator_info(session)

            if all_data and my_val:
                bonded = [v for v in all_data.get("validators", []) if v.get("status") == "BOND_STATUS_BONDED"]
                bonded_sorted = sorted(bonded, key=lambda v: int(v.get("tokens", 0)))
                lowest = int(bonded_sorted[0]["tokens"]) if bonded_sorted else 0
                my_tokens = int(my_val.get("tokens", 0))
                margin = my_tokens - lowest

                # Alert if within 5% margin of falling out
                if my_val.get("status") == "BOND_STATUS_BONDED" and margin < lowest * 0.05:
                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text=(
                            "⚠️ *AKTİF SET UYARISI!*\n"
                            "━━━━━━━━━━━━━━━━━━\n"
                            f"💎 Stake'in      : `{my_tokens / 1e18:.2f} RAI`\n"
                            f"📉 En Düşük Aktif: `{lowest / 1e18:.2f} RAI`\n"
                            f"🛡️ Marjın        : `{margin / 1e18:.2f} RAI`\n\n"
                            "⚠️ _Aktif setten düşme riskin var! Delegasyon almayı dene._"
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )
        except Exception as e:
            logger.error(f"Active set check error: {e}")
        await asyncio.sleep(ACTIVE_SET_CHECK_INTERVAL)

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
async def post_init(application: Application):
    bot = application.bot
    application.create_task(alert_governance(bot))
    application.create_task(alert_jail(bot))
    application.create_task(alert_active_set(bot))

def main():
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_start))
    app.add_handler(CommandHandler("status",    cmd_status))
    app.add_handler(CommandHandler("balance",   cmd_balance))
    app.add_handler(CommandHandler("rank",      cmd_rank))
    app.add_handler(CommandHandler("activeset", cmd_activeset))
    app.add_handler(CommandHandler("uptime",    cmd_uptime))
    app.add_handler(CommandHandler("proposals", cmd_proposals))
    app.add_handler(CommandHandler("rewards",   cmd_rewards))
    app.add_handler(CommandHandler("network",   cmd_network))

    logger.info("🤖 RAI Validator Telegram Bot başlatıldı.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

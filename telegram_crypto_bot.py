import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict, List

import aiohttp
from fastapi import FastAPI
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# --- Standard Configuration ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Load Secrets from Environment Variables ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")

# --- Chain Configuration ---
CHAINS = {
    'ethereum': {'name': 'Ethereum Mainnet', 'symbol': 'ETH', 'rpc': 'https://eth.llamarpc.com'},
    'base': {'name': 'Base', 'symbol': 'ETH', 'rpc': 'https://mainnet.base.org'},
    'arbitrum': {'name': 'Arbitrum', 'symbol': 'ETH', 'rpc': 'https://arb1.arbitrum.io/rpc'},
    'optimism': {'name': 'Optimism', 'symbol': 'ETH', 'rpc': 'https://mainnet.optimism.io'},
    'polygon': {'name': 'Polygon', 'symbol': 'MATIC', 'rpc': 'https://polygon-rpc.com'},
    'bsc': {'name': 'BSC', 'symbol': 'BNB', 'rpc': 'https://bsc-dataseed.binance.org'},
    'ink': {'name': 'Ink', 'symbol': 'ETH', 'rpc': 'https://rpc-gel.inkonchain.com'},
    'hyperliquid': {'name': 'Hyperliquid', 'symbol': 'ETH', 'rpc': 'https://rpc.hyperliquid.xyz/evm'},
    'unichain': {'name': 'Unichain', 'symbol': 'ETH', 'rpc': 'https://mainnet.unichain.org'},
}

# --- Bot Logic (All Functions) ---
async def get_eth_price(session: aiohttp.ClientSession) -> float:
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": "ethereum", "vs_currencies": "usd"}
    if COINGECKO_API_KEY: params['x_cg_demo_api_key'] = COINGECKO_API_KEY
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        async with session.get(url, params=params, headers=headers, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                if 'ethereum' in data and 'usd' in data['ethereum']: return data['ethereum']['usd']
    except Exception as e: logger.error(f"Could not fetch ETH price: {e}")
    return 0.0

async def get_native_balance(session: aiohttp.ClientSession, rpc_url: str, address: str) -> float:
    try:
        payload = {"jsonrpc": "2.0", "method": "eth_getBalance", "params": [address, "latest"], "id": 1}
        async with session.post(rpc_url, json=payload, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                if 'result' in data: return int(data['result'], 16) / 10**18
    except Exception: pass
    return 0.0

async def get_balances_for_chain(session: aiohttp.ClientSession, chain_id: str, addresses: List[str]) -> Dict[str, float]:
    rpc_url = CHAINS[chain_id]['rpc']
    tasks = {addr: get_native_balance(session, rpc_url, addr) for addr in addresses}
    balances = await asyncio.gather(*tasks.values())
    return {addr: balance for addr, balance in zip(tasks.keys(), balances)}

async def get_all_balances(session: aiohttp.ClientSession, addresses: List[str]) -> Dict[str, Dict[str, float]]:
    tasks = {chain_id: get_balances_for_chain(session, chain_id, addresses) for chain_id in CHAINS.keys()}
    results = await asyncio.gather(*tasks.values())
    return {chain_id: result for chain_id, result in zip(tasks.keys(), results)}

def parse_addresses(text: str) -> List[str]:
    address_pattern = r'0x[a-fA-F0-9]{40}'
    return list(dict.fromkeys(addr.lower() for addr in re.findall(address_pattern, text)))

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command with the new, detailed welcome message."""
    welcome_message = """
🤖 **Crypto Balance Bot**

I can help you check ETH balances across multiple EVM chains!

**Commands:**
/start - Show this help message

**Supported Chains:**
• Ethereum Mainnet
• Base
• Ink
• Arbitrum
• Hyperliquid
• Unichain
• Polygon
• Optimism
• BSC

**Usage:**
1. Paste your wallet addresses, one per line.
2. You can paste up to 200 addresses.
3. I'll sum up all ETH across all supported chains.

**Example:**
0x742d35Cc6634C0532925a3b8D5C9E49C7F59c2c4
0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045
"""
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """This function is triggered by any non-command text message."""
    full_text = update.message.text; addresses = parse_addresses(full_text)
    if not addresses:
        await update.message.reply_text("I didn't find any valid wallet addresses in your message. Use /start to see instructions and an example.")
        return
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    async with aiohttp.ClientSession() as session:
        balance_task = get_all_balances(session, addresses); price_task = get_eth_price(session)
        all_balances, eth_price = await asyncio.gather(balance_task, price_task)
    
    asset_totals = {}; chain_breakdown = []
    for chain_id, balances in all_balances.items():
        chain_info = CHAINS[chain_id]; symbol = chain_info['symbol']; chain_total = sum(balances.values())
        if chain_total > 0.000001: chain_breakdown.append(f"• **{chain_info['name']}:** {chain_total:.6f} {symbol}")
        if symbol not in asset_totals: asset_totals[symbol] = 0
        asset_totals[symbol] += chain_total
    
    result_message = f"📊 **Balance Summary for {len(addresses)} addresses:**\n\n"
    if chain_breakdown: result_message += "\n".join(sorted(chain_breakdown))
    else: result_message += "No balances found on any supported chain."
    result_message += "\n\n"
    for symbol, total in sorted(asset_totals.items()):
        if total > 0.000001: result_message += f"🎯 **TOTAL {symbol}:** {total:.6f} {symbol}\n"
    if 'ETH' in asset_totals and asset_totals['ETH'] > 0 and eth_price > 0:
        usd_value = asset_totals['ETH'] * eth_price
        result_message += f"💰 **ETH USD Value:** `${usd_value:,.2f}` (at `${eth_price:,.2f}/ETH`)"
    
    await update.message.reply_text(result_message, parse_mode='Markdown')

# --- LIFESPAN MANAGER TO START/STOP THE BOT ---
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    if not TELEGRAM_TOKEN:
        logger.critical("CRITICAL: TELEGRAM_TOKEN environment variable not set. Bot will not start.")
        yield
        return
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, balance_command))
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot has started successfully.")
    
    yield
    
    await application.updater.stop()
    await application.stop()
    await application.shutdown()
    logger.info("Telegram bot has been shut down.")

# --- WEB SERVER SETUP ---
web_app = FastAPI(lifespan=lifespan)

@web_app.api_route("/", methods=["GET", "HEAD"])
def health_check():
    """This endpoint responds to both GET and HEAD requests to keep the service alive."""
    return {"status": "ok, bot is running"}

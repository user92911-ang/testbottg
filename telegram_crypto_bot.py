import asyncio
import logging
import os
import re
from typing import Dict, List

import aiohttp
from telegram import Update
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          MessageHandler, filters)

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

class CryptoBalanceBot:
    def __init__(self, telegram_token: str, coingecko_api_key: str = None):
        self.telegram_token = telegram_token
        self.coingecko_api_key = coingecko_api_key
        
        if self.coingecko_api_key:
            logger.info("CoinGecko API key found. Using Pro API.")
        else:
            logger.warning("CoinGecko API key not found. Using public API (may be unreliable).")

        self.rpc_endpoints = {
            'ethereum': 'https://eth.llamarpc.com',
            'base': 'https://mainnet.base.org',
            'ink': 'https://rpc-gel.inkonchain.com',
            'arbitrum': 'https://arb1.arbitrum.io/rpc',
            'hyperliquid': 'https://rpc.hyperliquid.xyz/evm',
            'unichain': 'https://mainnet.unichain.org',
            'polygon': 'https://polygon-rpc.com',
            'optimism': 'https://mainnet.optimism.io',
            'bsc': 'https://bsc-dataseed.binance.org',
        }
        
        self.chain_names = {
            'ethereum': 'Ethereum', 'base': 'Base', 'ink': 'Ink',
            'arbitrum': 'Arbitrum', 'hyperliquid': 'Hyperliquid', 'unichain': 'Unichain',
            'polygon': 'Polygon', 'optimism': 'Optimism', 'bsc': 'BSC',
        }

    async def get_eth_price(self, session: aiohttp.ClientSession) -> float:
        """
        Get the current price of ETH in USD from CoinGecko.
        Uses the Pro API with a key if available, otherwise falls back to the public API.
        """
        params = {"ids": "ethereum", "vs_currencies": "usd"}
        
        if self.coingecko_api_key:
            url = "https://pro-api.coingecko.com/api/v3/simple/price"
            # CORRECTED PARAMETER NAME FOR THE API KEY
            params['x_cg_pro_api_key'] = self.coingecko_api_key
        else:
            url = "https://api.coingecko.com/api/v3/simple/price"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        try:
            async with session.get(url, params=params, headers=headers, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'ethereum' in data and 'usd' in data['ethereum']:
                        price = data['ethereum']['usd']
                        logger.info(f"Successfully fetched ETH price: ${price}")
                        return price
                    else:
                        logger.warning(f"CoinGecko API response format unexpected: {data}")
                else:
                    response_text = await response.text()
                    logger.error(f"CoinGecko API returned non-200 status: {response.status} - Body: {response_text}")
        except Exception as e:
            logger.error(f"An exception occurred while fetching ETH price: {e}", exc_info=True)

        logger.warning("Returning 0.0 for ETH price due to a fetch issue.")
        return 0.0

    async def get_eth_balance(self, session: aiohttp.ClientSession, rpc_url: str, address: str) -> float:
        try:
            payload = {"jsonrpc": "2.0", "method": "eth_getBalance", "params": [address, "latest"], "id": 1}
            async with session.post(rpc_url, json=payload, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'result' in data:
                        return int(data['result'], 16) / 10**18
                return 0.0
        except Exception:
            return 0.0

    async def get_balances_for_chain(self, session: aiohttp.ClientSession, chain: str, addresses: List[str]) -> Dict[str, float]:
        rpc_url = self.rpc_endpoints[chain]
        balances = {}
        batch_size = 10
        for i in range(0, len(addresses), batch_size):
            batch = addresses[i:i+batch_size]
            tasks = [self.get_eth_balance(session, rpc_url, addr) for addr in batch]
            batch_balances = await asyncio.gather(*tasks)
            for addr, balance in zip(batch, batch_balances):
                balances[addr] = balance
            await asyncio.sleep(0.1)
        return balances

    async def get_all_balances(self, session: aiohttp.ClientSession, addresses: List[str]) -> Dict[str, Dict[str, float]]:
        all_balances = {}
        tasks = [self.get_balances_for_chain(session, chain, addresses) for chain in self.rpc_endpoints.keys()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, chain in enumerate(self.rpc_endpoints.keys()):
            if isinstance(results[i], Exception):
                all_balances[chain] = {addr: 0.0 for addr in addresses}
            else:
                all_balances[chain] = results[i]
        return all_balances

    def parse_addresses(self, text: str) -> List[str]:
        address_pattern = r'0x[a-fA-F0-9]{40}'
        addresses = re.findall(address_pattern, text)
        return list(dict.fromkeys(addr.lower() for addr in addresses))

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        welcome_message = "🤖 **Crypto Balance Bot**\n\nI check ETH balances on multiple chains.\n\n**Usage:**\nJust paste wallet addresses and I'll do the rest."
        await update.message.reply_text(welcome_message, parse_mode='Markdown')

    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        full_text = update.message.text
        addresses = self.parse_addresses(full_text)

        if not addresses:
            await update.message.reply_text("❌ No valid Ethereum addresses found.")
            return

        if len(addresses) > 200:
            await update.message.reply_text("❌ Please limit requests to 200 addresses.")
            return

        status_message = await update.message.reply_text(f"🔍 Checking balances for {len(addresses)} addresses...")

        try:
            async with aiohttp.ClientSession() as session:
                balance_task = self.get_all_balances(session, addresses)
                price_task = self.get_eth_price(session)
                all_balances, eth_price = await asyncio.gather(balance_task, price_task)

            grand_total = sum(sum(balances.values()) for balances in all_balances.values())
            
            result_message = f"📊 **Balance Summary for {len(addresses)} addresses:**\n\n"
            sorted_chains = sorted(all_balances.items(), key=lambda item: sum(item[1].values()), reverse=True)

            for chain, balances in sorted_chains:
                chain_total = sum(balances.values())
                if chain_total > 0.000001:
                    chain_name = self.chain_names.get(chain, chain.title())
                    result_message += f"• **{chain_name}:** {chain_total:.6f} ETH\n"
            
            result_message += f"\n🎯 **TOTAL:** {grand_total:.6f} ETH"
            
            if grand_total > 0:
                if eth_price > 0:
                    usd_value = grand_total * eth_price
                    result_message += f"\n💰 **USD Value:** `${usd_value:,.2f}` (at `${eth_price:,.2f}/ETH`)"
                else:
                    result_message += "\n\n⚠️ _Could not fetch live ETH price for USD conversion._"
            
            await status_message.edit_text(result_message, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error in balance_command: {e}", exc_info=True)
            await status_message.edit_text("❌ An unexpected error occurred.")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.parse_addresses(update.message.text):
            await self.balance_command(update, context)
        else:
            await update.message.reply_text("I didn't find any valid Ethereum addresses.")

    def run(self):
        application = Application.builder().token(self.telegram_token).build()
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("balance", self.balance_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        logger.info("Starting Telegram bot...")
        application.run_polling()

def main():
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    if not TELEGRAM_TOKEN:
        logger.critical("CRITICAL: TELEGRAM_TOKEN environment variable not set.")
        return
    
    COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")
    
    bot = CryptoBalanceBot(TELEGRAM_TOKEN, COINGECKO_API_KEY)
    bot.run()

if __name__ == "__main__":
    main()
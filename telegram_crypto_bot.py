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
            logger.info("CoinGecko API key found. Using authenticated public API endpoint.")
        else:
            logger.warning("CoinGecko API key not found. Using public API (may be unreliable).")

        # --- NEW, UNIFIED CHAIN CONFIGURATION ---
        # This structure now includes the native token symbol for each chain.
        self.chains = {
            'ethereum': {'name': 'Ethereum', 'symbol': 'ETH', 'rpc': 'https://eth.llamarpc.com'},
            'base': {'name': 'Base', 'symbol': 'ETH', 'rpc': 'https://mainnet.base.org'},
            'arbitrum': {'name': 'Arbitrum', 'symbol': 'ETH', 'rpc': 'https://arb1.arbitrum.io/rpc'},
            'optimism': {'name': 'Optimism', 'symbol': 'ETH', 'rpc': 'https://mainnet.optimism.io'},
            'polygon': {'name': 'Polygon', 'symbol': 'MATIC', 'rpc': 'https://polygon-rpc.com'},
            'bsc': {'name': 'BSC', 'symbol': 'BNB', 'rpc': 'https://bsc-dataseed.binance.org'},
            'ink': {'name': 'Ink', 'symbol': 'ETH', 'rpc': 'https://rpc-gel.inkonchain.com'},
            'hyperliquid': {'name': 'Hyperliquid', 'symbol': 'ETH', 'rpc': 'https://rpc.hyperliquid.xyz/evm'},
            'unichain': {'name': 'Unichain', 'symbol': 'ETH', 'rpc': 'https://mainnet.unichain.org'},
        }

    async def get_eth_price(self, session: aiohttp.ClientSession) -> float:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": "ethereum", "vs_currencies": "usd"}
        
        if self.coingecko_api_key:
            params['x_cg_demo_api_key'] = self.coingecko_api_key

        headers = {'User-Agent': 'Mozilla/5.0'}
        
        try:
            async with session.get(url, params=params, headers=headers, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'ethereum' in data and 'usd' in data['ethereum']:
                        price = data['ethereum']['usd']
                        logger.info(f"Successfully fetched ETH price: ${price}")
                        return price
                else:
                    logger.error(f"CoinGecko API returned non-200 status: {response.status}")
        except Exception as e:
            logger.error(f"An exception occurred while fetching ETH price: {e}", exc_info=True)

        return 0.0

    async def get_native_balance(self, session: aiohttp.ClientSession, rpc_url: str, address: str) -> float:
        """This function correctly fetches the NATIVE token balance, whatever it may be."""
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

    async def get_balances_for_chain(self, session: aiohttp.ClientSession, chain_id: str, addresses: List[str]) -> Dict[str, float]:
        rpc_url = self.chains[chain_id]['rpc']
        balances = {}
        batch_size = 10
        for i in range(0, len(addresses), batch_size):
            batch = addresses[i:i+batch_size]
            tasks = [self.get_native_balance(session, rpc_url, addr) for addr in batch]
            batch_balances = await asyncio.gather(*tasks)
            for addr, balance in zip(batch, batch_balances):
                balances[addr] = balance
            await asyncio.sleep(0.1)
        return balances

    async def get_all_balances(self, session: aiohttp.ClientSession, addresses: List[str]) -> Dict[str, Dict[str, float]]:
        all_balances = {}
        tasks = [self.get_balances_for_chain(session, chain_id, addresses) for chain_id in self.chains.keys()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, chain_id in enumerate(self.chains.keys()):
            if isinstance(results[i], Exception):
                all_balances[chain_id] = {addr: 0.0 for addr in addresses}
            else:
                all_balances[chain_id] = results[i]
        return all_balances

    def parse_addresses(self, text: str) -> List[str]:
        address_pattern = r'0x[a-fA-F0-9]{40}'
        addresses = re.findall(address_pattern, text)
        return list(dict.fromkeys(addr.lower() for addr in addresses))

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        welcome_message = """
🤖 **Crypto Balance Bot**
I can help you check native token balances across multiple EVM chains!
**Commands:**
/start - Show this help message
/balance - Check balances
**Supported Chains:**
• Ethereum (ETH)
• Base (ETH)
• Arbitrum (ETH)
• Optimism (ETH)
• Polygon (MATIC)
• BSC (BNB)
• and more!
**Usage:**
Just paste wallet addresses and I'll do the rest.
        """
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

            # --- MODIFIED LOGIC TO HANDLE MULTIPLE ASSETS ---
            asset_totals = {}
            chain_breakdown = []

            for chain_id, balances in all_balances.items():
                chain_info = self.chains[chain_id]
                symbol = chain_info['symbol']
                chain_total = sum(balances.values())

                if chain_total > 0.000001:
                    # Prepare the per-chain breakdown line
                    chain_breakdown.append(f"• **{chain_info['name']}:** {chain_total:.6f} {symbol}")
                
                # Add to the correct asset total
                if symbol not in asset_totals:
                    asset_totals[symbol] = 0
                asset_totals[symbol] += chain_total
            
            # --- UPDATED MESSAGE FORMATTING ---
            result_message = f"📊 **Balance Summary for {len(addresses)} addresses:**\n\n"
            
            if chain_breakdown:
                result_message += "\n".join(sorted(chain_breakdown))
            else:
                result_message += "No balances found on any chain."

            result_message += "\n\n"
            
            # Display totals for each asset found
            for symbol, total in sorted(asset_totals.items()):
                if total > 0.000001:
                    result_message += f"🎯 **TOTAL {symbol}:** {total:.6f} {symbol}\n"

            # Only show USD value for the ETH total
            if 'ETH' in asset_totals and asset_totals['ETH'] > 0 and eth_price > 0:
                usd_value = asset_totals['ETH'] * eth_price
                result_message += f"💰 **ETH USD Value:** `${usd_value:,.2f}` (at `${eth_price:,.2f}/ETH`)"
            
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

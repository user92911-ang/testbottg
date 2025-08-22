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
    def __init__(self, telegram_token: str):
        self.telegram_token = telegram_token
        
        # RPC endpoints for different chains
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
        
        # Chain names for display
        self.chain_names = {
            'ethereum': 'Ethereum',
            'base': 'Base',
            'ink': 'Ink',
            'arbitrum': 'Arbitrum',
            'hyperliquid': 'Hyperliquid',
            'unichain': 'Unichain',
            'polygon': 'Polygon',
            'optimism': 'Optimism',
            'bsc': 'BSC',
        }

    async def get_eth_price(self, session: aiohttp.ClientSession) -> float:
        """Get the current price of ETH in USD from CoinGecko with a User-Agent."""
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": "ethereum", "vs_currencies": "usd"}
        
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

        except asyncio.TimeoutError:
            logger.error("Error fetching ETH price from CoinGecko: Request timed out.")
        except Exception as e:
            logger.error(f"An exception occurred while fetching ETH price: {e}")

        logger.warning("Returning 0.0 for ETH price due to a fetch issue.")
        return 0.0


    async def get_eth_balance(self, session: aiohttp.ClientSession, rpc_url: str, address: str) -> float:
        """Get ETH balance for a single address on a specific chain"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_getBalance",
                "params": [address, "latest"],
                "id": 1
            }
            
            async with session.post(rpc_url, json=payload, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'result' in data:
                        balance_wei = int(data['result'], 16)
                        balance_eth = balance_wei / 10**18
                        return balance_eth
                return 0.0
        except Exception as e:
            logger.error(f"Error fetching balance for {address} on {rpc_url}: {e}")
            return 0.0

    async def get_balances_for_chain(self, session: aiohttp.ClientSession, chain: str, addresses: List[str]) -> Dict[str, float]:
        """Get balances for all addresses on a specific chain"""
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
        """Get balances for all addresses across all chains"""
        all_balances = {}
        
        tasks = []
        for chain in self.rpc_endpoints.keys():
            task = self.get_balances_for_chain(session, chain, addresses)
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, chain in enumerate(self.rpc_endpoints.keys()):
            if isinstance(results[i], Exception):
                logger.error(f"Error getting balances for {chain}: {results[i]}")
                all_balances[chain] = {addr: 0.0 for addr in addresses}
            else:
                all_balances[chain] = results[i]
                logger.info(f"Completed {chain} balances")

        return all_balances


    def parse_addresses(self, text: str) -> List[str]:
        """Parse Ethereum addresses from text"""
        address_pattern = r'0x[a-fA-F0-9]{40}'
        addresses = re.findall(address_pattern, text)
        
        unique_addresses = []
        seen = set()
        for addr in addresses:
            addr_lower = addr.lower()
            if addr_lower not in seen:
                unique_addresses.append(addr)
                seen.add(addr_lower)
        
        return unique_addresses

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        welcome_message = """
🤖 **Crypto Balance Bot**

I can help you check ETH balances across multiple EVM chains!

**Commands:**
/start - Show this help message
/balance - Check balances (paste your addresses after this command)

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
1. Type `/balance` followed by your wallet addresses or just paste them.
2. You can paste up to 200 addresses.
3. I'll sum up all ETH across all supported chains.

**Example:**
```
/balance
0x742d35Cc6634C0532925a3b8D5C9E49C7F59c2c4
0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045
0x8ba1f109551bD432803012645Hac136c9c36E7d
```
        """
        await update.message.reply_text(welcome_message, parse_mode='Markdown')

    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /balance command with improved feedback for price fetching."""
        full_text = update.message.text
        addresses = self.parse_addresses(full_text)

        if not addresses:
            await update.message.reply_text("❌ No valid Ethereum addresses found. Please check your input.")
            return

        if len(addresses) > 200:
            await update.message.reply_text(f"❌ Too many addresses ({len(addresses)}). Please limit to 200 addresses per request.")
            return

        status_message = await update.message.reply_text(
            f"🔍 Checking balances for {len(addresses)} addresses across {len(self.rpc_endpoints)} chains...\n"
            "This may take a moment..."
        )

        try:
            async with aiohttp.ClientSession() as session:
                balance_task = self.get_all_balances(session, addresses)
                price_task = self.get_eth_price(session)
                all_balances, eth_price = await asyncio.gather(balance_task, price_task)

            chain_totals = {}
            grand_total = 0.0
            for chain, balances in all_balances.items():
                chain_total = sum(balances.values())
                chain_totals[chain] = chain_total
                grand_total += chain_total

            result_message = f"📊 **Balance Summary for {len(addresses)} addresses:**\n\n"
            sorted_chains = sorted(chain_totals.items(), key=lambda item: item[1], reverse=True)

            for chain, total in sorted_chains:
                chain_name = self.chain_names.get(chain, chain.title())
                if total > 0:
                    result_message += f"• **{chain_name}:** {total:.6f} ETH\n"
            
            result_message += f"\n🎯 **TOTAL:** {grand_total:.6f} ETH"
            
            if grand_total > 0:
                if eth_price > 0:
                    usd_value = grand_total * eth_price
                    result_message += f"\n💰 **USD Value:** `${usd_value:,.2f}` (at `${eth_price:,.2f}/ETH`)"
                else:
                    result_message += "\n\n⚠️ _Could not fetch live ETH price for USD conversion._"
            
            active_wallets_count = sum(1 for chain_balances in all_balances.values() for balance in chain_balances.values() if balance > 0)
            
            if active_wallets_count > 0:
                total_checks = len(addresses) * len(self.rpc_endpoints)
                result_message += f"\n\n📈 Found balances in **{active_wallets_count}** of **{total_checks}** checked chain wallets."

            await status_message.edit_text(result_message, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Error processing balance request: {e}")
            await status_message.edit_text(
                f"❌ An error occurred while checking balances: {str(e)}\n\n"
                "Please try again or contact support if the issue persists."
            )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle regular messages containing addresses"""
        text = update.message.text
        if self.parse_addresses(text):
            await self.balance_command(update, context)
        else:
            await update.message.reply_text(
                "I didn't find any valid Ethereum addresses in your message.\n\n"
                "Use the /start command to see instructions."
            )

    def run(self):
        """Run the bot"""
        application = Application.builder().token(self.telegram_token).build()
        
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("balance", self.balance_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
        logger.info("Starting Telegram bot...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

def main():
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    
    if not TELEGRAM_TOKEN:
        logger.critical("CRITICAL: TELEGRAM_TOKEN environment variable not set.")
        logger.critical("Please set this variable in your deployment environment and redeploy.")
        return
    
    bot = CryptoBalanceBot(TELEGRAM_TOKEN)
    bot.run()

if __name__ == "__main__":
    main()
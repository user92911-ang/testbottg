import asyncio
import logging
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import aiohttp
import json
from typing import Dict, List
import re

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
            'arbitrum': 'https://arb1.arbitrum.io/rpc',
            'polygon': 'https://polygon-rpc.com',
            'optimism': 'https://mainnet.optimism.io',
            'bsc': 'https://bsc-dataseed.binance.org',
            # Add more chains as needed
            # 'ink': 'YOUR_INK_RPC_ENDPOINT',
            # 'hyperliquid': 'YOUR_HYPERLIQUID_RPC_ENDPOINT',
        }
        
        # Chain names for display
        self.chain_names = {
            'ethereum': 'Ethereum',
            'base': 'Base',
            'arbitrum': 'Arbitrum',
            'polygon': 'Polygon',
            'optimism': 'Optimism',
            'bsc': 'BSC',
        }

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
                        # Convert from Wei to ETH
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
        
        # Process addresses in batches to avoid rate limits
        batch_size = 10
        for i in range(0, len(addresses), batch_size):
            batch = addresses[i:i+batch_size]
            tasks = [self.get_eth_balance(session, rpc_url, addr) for addr in batch]
            batch_balances = await asyncio.gather(*tasks)
            
            for addr, balance in zip(batch, batch_balances):
                balances[addr] = balance
            
            # Small delay between batches
            await asyncio.sleep(0.1)
        
        return balances

    async def get_all_balances(self, addresses: List[str]) -> Dict[str, Dict[str, float]]:
        """Get balances for all addresses across all chains"""
        all_balances = {}
        
        async with aiohttp.ClientSession() as session:
            tasks = []
            for chain in self.rpc_endpoints.keys():
                task = self.get_balances_for_chain(session, chain, addresses)
                tasks.append((chain, task))
            
            for chain, task in tasks:
                try:
                    balances = await task
                    all_balances[chain] = balances
                    logger.info(f"Completed {chain} balances")
                except Exception as e:
                    logger.error(f"Error getting balances for {chain}: {e}")
                    all_balances[chain] = {addr: 0.0 for addr in addresses}
        
        return all_balances

    def parse_addresses(self, text: str) -> List[str]:
        """Parse Ethereum addresses from text"""
        # Regex for Ethereum addresses (0x followed by 40 hex characters)
        address_pattern = r'0x[a-fA-F0-9]{40}'
        addresses = re.findall(address_pattern, text)
        
        # Remove duplicates while preserving order
        unique_addresses = []
        seen = set()
        for addr in addresses:
            addr_lower = addr.lower()
            if addr_lower not in seen:
                unique_addresses.append(addr_lower)
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
• Arbitrum
• Polygon
• Optimism
• BSC

**Usage:**
1. Type `/balance` followed by your wallet addresses
2. You can paste up to 100+ addresses
3. I'll sum up all ETH across all chains

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
        """Handle /balance command"""
        if not context.args:
            await update.message.reply_text(
                "Please provide wallet addresses after the /balance command.\n\n"
                "Example:\n/balance 0x742d35Cc6634C0532925a3b8D5C9E49C7F59c2c4 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
            )
            return
        
        # Parse addresses from command arguments and message text
        full_text = update.message.text
        addresses = self.parse_addresses(full_text)
        
        if not addresses:
            await update.message.reply_text("❌ No valid Ethereum addresses found. Please check your input.")
            return
        
        if len(addresses) > 200:  # Safety limit
            await update.message.reply_text(f"❌ Too many addresses ({len(addresses)}). Please limit to 200 addresses per request.")
            return
        
        # Send initial message
        status_message = await update.message.reply_text(
            f"🔍 Checking balances for {len(addresses)} addresses across {len(self.rpc_endpoints)} chains...\n"
            "This may take a moment..."
        )
        
        try:
            # Get all balances
            all_balances = await self.get_all_balances(addresses)
            
            # Calculate totals
            chain_totals = {}
            grand_total = 0.0
            
            for chain, balances in all_balances.items():
                chain_total = sum(balances.values())
                chain_totals[chain] = chain_total
                grand_total += chain_total
            
            # Format results
            result_message = f"📊 **Balance Summary for {len(addresses)} addresses:**\n\n"
            
            for chain, total in chain_totals.items():
                chain_name = self.chain_names.get(chain, chain.title())
                if total > 0:
                    result_message += f"• **{chain_name}:** {total:.6f} ETH\n"
                else:
                    result_message += f"• **{chain_name}:** 0 ETH\n"
            
            result_message += f"\n🎯 **TOTAL:** {grand_total:.6f} ETH"
            
            if grand_total > 0:
                result_message += f"\n💰 **USD Value:** ~${grand_total * 3500:.2f} (approx @ $3,500/ETH)"
            
            # Add breakdown for non-zero addresses if requested
            non_zero_count = 0
            for chain, balances in all_balances.items():
                non_zero_count += len([b for b in balances.values() if b > 0])
            
            if non_zero_count > 0:
                result_message += f"\n\n📈 **Active wallets:** {non_zero_count} out of {len(addresses) * len(self.rpc_endpoints)} total checks"
            
            await status_message.edit_text(result_message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error processing balance request: {e}")
            await status_message.edit_text(
                f"❌ Error occurred while checking balances: {str(e)}\n\n"
                "Please try again or contact support if the issue persists."
            )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle regular messages (for pasting addresses)"""
        text = update.message.text
        addresses = self.parse_addresses(text)
        
        if addresses:
            # Treat as balance check
            context.args = addresses
            await self.balance_command(update, context)
        else:
            await update.message.reply_text(
                "I didn't find any valid Ethereum addresses in your message.\n\n"
                "Use /balance followed by your wallet addresses, or just paste addresses directly."
            )

    def run(self):
        """Run the bot"""
        application = Application.builder().token(self.telegram_token).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("balance", self.balance_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
        # Start the bot
        logger.info("Starting Telegram bot...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

def main():
    """
    Main function to set up and run the bot.
    It retrieves the Telegram token from an environment variable.
    """
    # Get the token from an environment variable called 'TELEGRAM_TOKEN'
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    
    # Check if the token was found. If not, print an error and exit.
    if not TELEGRAM_TOKEN:
        logger.error("CRITICAL: TELEGRAM_TOKEN environment variable not set.")
        logger.error("Please set this variable in your deployment environment (e.g., Render) and redeploy.")
        return # Exit the program
    
    # If the token is found, create the bot instance and run it
    bot = CryptoBalanceBot(TELEGRAM_TOKEN)
    bot.run()

if __name__ == "__main__":
    main()
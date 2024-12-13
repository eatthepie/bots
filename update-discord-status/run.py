import os
import asyncio
import discord
from web3 import Web3
from discord.ext import tasks
from dotenv import load_dotenv
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Configuration variables remain the same
ETH_NODE_URL = os.getenv('ETH_NODE_URL')
ETH_CONTRACT_ADDRESS = os.getenv('ETH_CONTRACT_ADDRESS')
ETH_GAME_BOT_TOKEN = os.getenv('ETH_GAME_BOT_TOKEN')
ETH_PRIZE_BOT_TOKEN = os.getenv('ETH_PRIZE_BOT_TOKEN')

WORLD_NODE_URL = os.getenv('WORLD_NODE_URL')
WORLD_CONTRACT_ADDRESS = os.getenv('WORLD_CONTRACT_ADDRESS')
WORLD_GAME_BOT_TOKEN = os.getenv('WORLD_GAME_BOT_TOKEN')
WORLD_PRIZE_BOT_TOKEN = os.getenv('WORLD_PRIZE_BOT_TOKEN')

UPDATE_INTERVAL = 900  # 15 minutes in seconds

# Verify tokens exist
required_tokens = {
    'ETH_GAME_BOT_TOKEN': ETH_GAME_BOT_TOKEN,
    'ETH_PRIZE_BOT_TOKEN': ETH_PRIZE_BOT_TOKEN,
    'WORLD_GAME_BOT_TOKEN': WORLD_GAME_BOT_TOKEN,
    'WORLD_PRIZE_BOT_TOKEN': WORLD_PRIZE_BOT_TOKEN
}

for token_name, token in required_tokens.items():
    if not token:
        raise ValueError(f"Missing {token_name} in .env file")

# Initialize Web3 connections
eth_w3 = Web3(Web3.HTTPProvider(ETH_NODE_URL))
world_w3 = Web3(Web3.HTTPProvider(WORLD_NODE_URL))

# Contract ABI remains the same
CONTRACT_ABI = [
    {
        "inputs": [],
        "name": "currentGameNumber",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"type": "uint256"}],
        "name": "gamePrizePool",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# Initialize contracts
eth_contract = eth_w3.eth.contract(address=ETH_CONTRACT_ADDRESS, abi=CONTRACT_ABI)
world_contract = world_w3.eth.contract(address=WORLD_CONTRACT_ADDRESS, abi=CONTRACT_ABI)

class StatusBot(discord.Client):
    def __init__(self, update_func, bot_type, network):
        intents = discord.Intents.default()
        super().__init__(intents=intents, activity=discord.Game(name="Initializing..."))
        self.update_func = update_func
        self.bot_type = bot_type
        self.network = network
        self.last_title = None
        self.last_value = None

    async def setup_hook(self):
        self.status_update.start()
        logger.info(f"{self.network} {self.bot_type} Bot: Setup completed")

    @tasks.loop(seconds=UPDATE_INTERVAL)
    async def status_update(self):
        try:
            title, value = await self.update_func()
            
            if title != self.last_title or value != self.last_value:
                self.last_title = title
                self.last_value = value
                
                # Update bot's nickname with the title
                for guild in self.guilds:
                    try:
                        await guild.me.edit(nick=title)
                    except discord.errors.Forbidden:
                        logger.error(f"{self.network} {self.bot_type} Bot: Missing permissions in {guild.name}")
                    except Exception as e:
                        logger.error(f"{self.network} {self.bot_type} Bot: Error in {guild.name}: {str(e)}")
                
                # Update bot's activity with the value
                activity = discord.Game(name=value)
                await self.change_presence(activity=activity)
                
                logger.info(f"{self.network} {self.bot_type} Bot: Updated status - Title: {title}, Value: {value}")
        except Exception as e:
            logger.error(f"{self.network} {self.bot_type} Bot: Update error: {str(e)}")

    @status_update.before_loop
    async def before_status_update(self):
        await self.wait_until_ready()
        logger.info(f"{self.network} {self.bot_type} Bot: Ready!")

async def run_in_executor(func, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)

async def get_game_number(contract, network):
    """Query current game number from smart contract"""
    try:
        game_number = await run_in_executor(
            contract.functions.currentGameNumber().call
        )
        return f"{network} Game", f"#{game_number}"  # Returns tuple of (title, value)
    except Exception as e:
        logger.error(f"Error getting {network} game number: {str(e)}")
        return f"{network} Game", "Error"

async def get_prize_pool(contract, w3, network):
    """Query current prize pool from smart contract"""
    try:
        current_game = await run_in_executor(
            contract.functions.currentGameNumber().call
        )
        prize_pool_wei = await run_in_executor(
            contract.functions.gamePrizePool(current_game).call
        )
        prize_pool_amount = w3.from_wei(prize_pool_wei, 'ether')
        
        # Use appropriate currency symbol based on network
        currency = "WLD" if network.upper() == "WORLD" else "ETH"
        
        return f"{network} Prize", f"{prize_pool_amount:.2f} {currency}"  # Returns tuple of (title, value)
    except Exception as e:
        logger.error(f"Error getting {network} prize pool: {str(e)}")
        return f"{network} Prize", "Error"

async def main():
    try:
        # Ethereum bots
        eth_game_bot = StatusBot(
            update_func=lambda: get_game_number(eth_contract, "Ethereum"),
            bot_type="Game",
            network="Ethereum"
        )
        
        eth_prize_bot = StatusBot(
            update_func=lambda: get_prize_pool(eth_contract, eth_w3, "Ethereum"),
            bot_type="Prize",
            network="Ethereum"
        )

        # World Chain bots
        world_game_bot = StatusBot(
            update_func=lambda: get_game_number(world_contract, "World"),
            bot_type="Game",
            network="World"
        )
        
        world_prize_bot = StatusBot(
            update_func=lambda: get_prize_pool(world_contract, world_w3, "World"),
            bot_type="Prize",
            network="World"
        )

        await asyncio.gather(
            eth_game_bot.start(ETH_GAME_BOT_TOKEN),
            eth_prize_bot.start(ETH_PRIZE_BOT_TOKEN),
            world_game_bot.start(WORLD_GAME_BOT_TOKEN),
            world_prize_bot.start(WORLD_PRIZE_BOT_TOKEN)
        )
    except Exception as e:
        logger.error(f"Error running bots: {str(e)}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Received shutdown signal, closing bots...")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
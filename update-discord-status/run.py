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
WORLD_NODE_URL = os.getenv('WORLD_NODE_URL')
WORLD_CONTRACT_ADDRESS = os.getenv('WORLD_CONTRACT_ADDRESS')
WORLD_PRIZE_BOT_TOKEN = os.getenv('WORLD_PRIZE_BOT_TOKEN')
WORLD_COUNTDOWN_TOKEN = os.getenv('WORLD_COUNTDOWN_TOKEN')

UPDATE_INTERVAL = 900  # 15 minutes in seconds

# Verify tokens exist
required_tokens = {
    'WORLD_COUNTDOWN_TOKEN': WORLD_COUNTDOWN_TOKEN,
    'WORLD_PRIZE_BOT_TOKEN': WORLD_PRIZE_BOT_TOKEN
}

for token_name, token in required_tokens.items():
    if not token:
        raise ValueError(f"Missing {token_name} in .env file")

# Initialize Web3 connections
world_w3 = Web3(Web3.HTTPProvider(WORLD_NODE_URL))

# Contract ABI remains the same
CONTRACT_ABI = [
    {
        "type": "function",
        "name": "getCurrentGameInfo",
        "inputs": [],
        "outputs": [
          { "name": "gameNumber", "type": "uint256", "internalType": "uint256" },
          {
            "name": "difficulty",
            "type": "uint8",
            "internalType": "enum Lottery.Difficulty",
          },
          { "name": "prizePool", "type": "uint256", "internalType": "uint256" },
          { "name": "drawTime", "type": "uint256", "internalType": "uint256" },
          {
            "name": "timeUntilDraw",
            "type": "uint256",
            "internalType": "uint256",
          },
        ],
        "stateMutability": "view",
    },
    {
        "inputs": [{"type": "uint256"}],
        "name": "gamePrizePool",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "currentGameNumber",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# Initialize contracts
world_contract = world_w3.eth.contract(address=WORLD_CONTRACT_ADDRESS, abi=CONTRACT_ABI)

class StatusBot(discord.Client):
    def __init__(self, update_func, bot_type):
        intents = discord.Intents.default()
        super().__init__(intents=intents, activity=discord.Game(name="Initializing..."))
        self.update_func = update_func
        self.bot_type = bot_type
        self.last_title = None
        self.last_value = None

    async def setup_hook(self):
        self.status_update.start()
        logger.info(f"{self.bot_type} Bot: Setup completed")

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
                        logger.error(f"{self.bot_type} Bot: Missing permissions in {guild.name}")
                    except Exception as e:
                        logger.error(f"{self.bot_type} Bot: Error in {guild.name}: {str(e)}")
                
                # Update bot's activity with the value
                activity = discord.Game(name=value)
                await self.change_presence(activity=activity)
                
                logger.info(f"{self.bot_type} Bot: Updated status - Title: {title}, Value: {value}")
        except Exception as e:
            logger.error(f"{self.bot_type} Bot: Update error: {str(e)}")

    @status_update.before_loop
    async def before_status_update(self):
        await self.wait_until_ready()
        logger.info(f"{self.bot_type} Bot: Ready!")

async def run_in_executor(func, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)

async def get_prize_pool(contract, w3):
    """Query current prize pool from smart contract"""
    try:
        current_game = await run_in_executor(
            contract.functions.currentGameNumber().call
        )
        prize_pool_wei = await run_in_executor(
            contract.functions.gamePrizePool(current_game).call
        )
        prize_pool_amount = w3.from_wei(prize_pool_wei, 'ether')

        return f"Jackpot", f"{prize_pool_amount:.2f} WLD"
    except Exception as e:
        logger.error(f"Error getting jackpot: {str(e)}")
        return f"Jackpot", "Error"

async def get_time_until_draw(contract):
    """Query time until next draw from smart contract and round to nearest hour"""
    try:
        game_info = await run_in_executor(
            contract.functions.getCurrentGameInfo().call
        )
        seconds_until_draw = game_info[4]
        hours = (seconds_until_draw + 3599) // 3600  # Round up by adding 3599 seconds
        hour_text = "hour" if hours == 1 else "hours"
        return f"Next Draw", f"in {hours} {hour_text}"
    except Exception as e:
        logger.error(f"Error getting draw time: {str(e)}")
        return f"Next Draw", "Error"

async def main():
    try:        
        world_prize_bot = StatusBot(
            update_func=lambda: get_prize_pool(world_contract, world_w3),
            bot_type="Jackpot"
        )

        world_draw_bot = StatusBot(
            update_func=lambda: get_time_until_draw(world_contract),
            bot_type="Draw"
        )

        await asyncio.gather(
            world_prize_bot.start(WORLD_PRIZE_BOT_TOKEN),
            world_draw_bot.start(WORLD_COUNTDOWN_TOKEN)
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
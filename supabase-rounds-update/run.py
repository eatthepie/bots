import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
from web3 import Web3, AsyncWeb3
import json

# Load environment variables
load_dotenv()

# Configuration
CONTRACT_ADDRESS = Web3.to_checksum_address(os.getenv('CONTRACT_ADDRESS'))
RPC_URL = os.getenv('RPC_URL')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
CHECK_INTERVAL = 60  # 1 minute

# Initialize clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(RPC_URL))

# Load contract ABI
with open('contract_abi.json', 'r') as f:
    contract_abi = json.load(f)

contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=contract_abi)

async def get_detailed_game_info(game_number: int) -> dict:
    """Get detailed game information from the smart contract."""
    result = await contract.functions.getDetailedGameInfo(game_number).call()
    
    return {
        'gameId': result[0],
        'status': result[1],
        'prizePool': result[2],
        'numberOfWinners': result[3],
        'goldWinners': result[4],
        'silverWinners': result[5],
        'bronzeWinners': result[6],
        'winningNumbers': result[7],
        'difficulty': result[8],
        'drawInitiatedBlock': result[9],
        'randaoBlock': result[10],
        'randaoValue': result[11],
        'payouts': result[12]
    }

async def get_next_unprocessed_game() -> int:
    """Get the next game number to process."""
    response = supabase.table('rounds') \
        .select('game_number') \
        .order('game_number', desc=True) \
        .limit(1) \
        .execute()
    
    if response.data:
        return response.data[0]['game_number'] + 1
    return 1

async def get_ticket_count(game_number: int) -> int:
    """Get the count of tickets for a specific game."""
    response = supabase.table('tickets') \
        .select('*', count='exact') \
        .eq('game_number', game_number) \
        .execute()
    
    return response.count

async def has_next_game_tickets(game_number: int) -> bool:
    """Check if there are tickets for the next game."""
    response = supabase.table('tickets') \
        .select('*', count='exact') \
        .eq('game_number', game_number + 1) \
        .limit(1) \
        .execute()
    
    return response.count > 0

async def update_or_insert_round(game_number: int, game_info: dict, total_tickets: int, completed: bool) -> None:
    """Update or insert round information."""
    round_data = {
        'game_number': game_number,
        'total_tickets': total_tickets,
        'processed_at': datetime.utcnow().isoformat(),
        'winning_numbers': game_info['winningNumbers'],
        'completed': completed
    }
    
    supabase.table('rounds').upsert(round_data).execute()

async def process_games():
    """Main function to process games."""
    try:
        game_number = await get_next_unprocessed_game()
        
        while True:
            print(f"Processing game {game_number}")
            
            # Get current game info
            game_info = await get_detailed_game_info(game_number)
            total_tickets = await get_ticket_count(game_number)
            
            # Check if game is completed (has tickets for next game)
            completed = await has_next_game_tickets(game_number)
            
            # Update or insert round information
            await update_or_insert_round(game_number, game_info, total_tickets, completed)
            
            if completed:
                print(f"Game {game_number} completed, moving to next game")
                game_number += 1
            else:
                print(f"Game {game_number} still in progress, waiting for next check")
                await asyncio.sleep(CHECK_INTERVAL)
            
    except Exception as e:
        print(f"Error processing games: {str(e)}")
        # Wait before retrying
        await asyncio.sleep(CHECK_INTERVAL)

async def main():
    """Main loop."""
    while True:
        await process_games()

if __name__ == "__main__":
    print("Starting round processor...")
    asyncio.run(main())
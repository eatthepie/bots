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

# Event signatures
EVENT_SIGNATURES = {
    'DrawInitiated': w3.keccak(text="DrawInitiated(uint256,uint256)").hex(),
    'RandomSet': w3.keccak(text="RandomSet(uint256,uint256)").hex(),
    'VDFProofSubmitted': w3.keccak(text="VDFProofSubmitted(address,uint256)").hex(),
    'GamePrizePayoutInfo': w3.keccak(text="GamePrizePayoutInfo(uint256,uint256,uint256,uint256)").hex()
}

async def get_latest_round() -> int:
    """Get the latest game number from rounds table."""
    response = supabase.table('rounds') \
        .select('game_number') \
        .order('game_number', desc=True) \
        .limit(1) \
        .execute()
    
    if not response.data:
        print("No rounds found in database")
        return 1
    return response.data[0]['game_number']

async def get_missing_metadata_games() -> list[int]:
    """Get list of games missing metadata between 1 and latest round."""
    try:
        latest_round = await get_latest_round()
        if latest_round <= 1:
            return []

        # Get all games that have metadata
        response = supabase.table('game_metadata') \
            .select('game_number') \
            .lt('game_number', latest_round) \
            .execute()
        
        existing_games = set(game['game_number'] for game in response.data) if response.data else set()
        
        # Find missing games
        all_games = set(range(1, latest_round))  # Up to but not including latest_round
        missing_games = all_games - existing_games
        
        return sorted(list(missing_games))
    except Exception as e:
        print(f"Error getting missing games: {str(e)}")
        return []

async def store_empty_metadata(game_number: int) -> None:
    """Store empty metadata record for a game."""
    metadata = {
        'game_number': game_number,
        'draw_initiated_tx': None,
        'draw_initiated_time': None,
        'random_set_tx': None,
        'random_set_time': None,
        'vdf_proof_tx': None,
        'vdf_proof_time': None,
        'prize_payout_tx': None,
        'prize_payout_time': None,
    }
    supabase.table('game_metadata').upsert(metadata, on_conflict='game_number').execute()

async def store_game_metadata(game_number: int, event_data: dict) -> None:
    """Store game metadata in the database."""
    try:
        # Get block timestamp
        block = await w3.eth.get_block(event_data['blockNumber'])
        
        # Get existing metadata or create new
        response = supabase.table('game_metadata') \
            .select('*') \
            .eq('game_number', game_number) \
            .execute()
        
        metadata = response.data[0] if response.data else {
            'game_number': game_number,
            'draw_initiated_tx': None,
            'draw_initiated_time': None,
            'random_set_tx': None,
            'random_set_time': None,
            'vdf_proof_tx': None,
            'vdf_proof_time': None,
            'prize_payout_tx': None,
            'prize_payout_time': None,
        }
        
        # Update the specific event fields
        event_type = event_data['event']
        if event_type == 'DrawInitiated':
            metadata['draw_initiated_tx'] = event_data['transactionHash']
            metadata['draw_initiated_time'] = datetime.fromtimestamp(block['timestamp']).isoformat()
        elif event_type == 'RandomSet':
            metadata['random_set_tx'] = event_data['transactionHash']
            metadata['random_set_time'] = datetime.fromtimestamp(block['timestamp']).isoformat()
        elif event_type == 'VDFProofSubmitted':
            metadata['vdf_proof_tx'] = event_data['transactionHash']
            metadata['vdf_proof_time'] = datetime.fromtimestamp(block['timestamp']).isoformat()
        elif event_type == 'GamePrizePayoutInfo':
            metadata['prize_payout_tx'] = event_data['transactionHash']
            metadata['prize_payout_time'] = datetime.fromtimestamp(block['timestamp']).isoformat()
        
        # Upsert the metadata
        supabase.table('game_metadata').upsert(metadata, on_conflict='game_number').execute()
    except Exception as e:
        print(f"Error storing metadata for game {game_number}: {str(e)}")

async def process_game_events():
    """Process game events for missing metadata."""
    try:
        while True:
            missing_games = await get_missing_metadata_games()
            current_block = await w3.eth.block_number
            
            if missing_games:
                print(f"Found {len(missing_games)} games missing metadata")
                
                for game_number in missing_games:
                    print(f"Processing events for game {game_number}")
                    events_found = False
                    
                    # Prepare game number for topic filtering
                    game_number_hex = '0x' + hex(game_number)[2:].zfill(64)
                    
                    # Query all event types for this game
                    for event_name, event_signature in EVENT_SIGNATURES.items():
                        filter_params = {
                            'fromBlock': 0,  # or contract deployment block
                            'toBlock': current_block,
                            'address': CONTRACT_ADDRESS,
                            'topics': [event_signature]
                        }

                        # Add game number topic where appropriate
                        if event_name != 'VDFProofSubmitted':
                            filter_params['topics'].append(game_number_hex)
                        
                        try:
                            logs = await w3.eth.get_logs(filter_params)
                            
                            for log in logs:
                                try:
                                    # Decode the event data
                                    decoded_log = getattr(contract.events, event_name)().process_log(log)
                                    
                                    # Filter VDFProofSubmitted events by game number
                                    if event_name == 'VDFProofSubmitted' and decoded_log['args']['gameNumber'] != game_number:
                                        continue
                                    
                                    events_found = True
                                    await store_game_metadata(
                                        game_number=game_number,
                                        event_data={
                                            'event': event_name,
                                            'blockNumber': log['blockNumber'],
                                            'transactionHash': log['transactionHash'].hex()
                                        }
                                    )
                                    print(f"Processed {event_name} event for game {game_number}")
                                except Exception as e:
                                    print(f"Error processing log for game {game_number}: {str(e)}")
                                    continue
                        except Exception as e:
                            print(f"Error getting logs for {event_name} game {game_number}: {str(e)}")
                            continue
                    
                    if not events_found:
                        print(f"No events found for game {game_number}, storing empty record")
                        await store_empty_metadata(game_number)
            
            await asyncio.sleep(CHECK_INTERVAL)
            
    except Exception as e:
        print(f"Error in main process_game_events loop: {str(e)}")
        await asyncio.sleep(CHECK_INTERVAL)

async def main():
    """Main loop."""
    while True:
        try:
            await process_game_events()
        except Exception as e:
            print(f"Error in main loop: {str(e)}")
            await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    print("Starting game events processor...")
    asyncio.run(main())
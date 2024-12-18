import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
from web3 import Web3, AsyncWeb3
import json
from typing import List
import aiohttp

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

async def get_missing_metadata_games() -> List[int]:
    """Get list of games missing metadata between 1 and latest round."""
    try:
        latest_round = await get_latest_round()
        if latest_round <= 1:
            return []

        response = supabase.table('game_metadata') \
            .select('game_number') \
            .lt('game_number', latest_round) \
            .execute()
        
        existing_games = set(game['game_number'] for game in response.data) if response.data else set()
        all_games = set(range(1, latest_round))
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

async def get_event_logs(event_name: str, event_signature: str, game_number: int) -> List[dict]:
    """Get events using eth_getLogs."""
    game_number_hex = '0x' + hex(game_number)[2:].zfill(64)
    
    # Prepare filter parameters
    filter_params = {
        "address": Web3.to_checksum_address(CONTRACT_ADDRESS),
        "topics": [event_signature]
    }
    
    # Add game number topic where appropriate
    if event_name != 'VDFProofSubmitted':
        filter_params["topics"].append(game_number_hex)
    
    # Make direct request to Alchemy
    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getLogs",
            "params": [filter_params]
        }
        
        async with session.post(RPC_URL, json=payload) as response:
            result = await response.json()
            
            if 'error' in result:
                print(f"API error for {event_name}: {result['error']}")
                return []
            
            logs = result.get('result', [])
            
            # For VDFProofSubmitted, filter by game number after getting logs
            if event_name == 'VDFProofSubmitted' and logs:
                filtered_logs = []
                for log in logs:
                    try:
                        decoded_log = contract.events.VDFProofSubmitted().process_log(log)
                        if decoded_log['args']['gameNumber'] == game_number:
                            filtered_logs.append(log)
                    except Exception as e:
                        print(f"Error decoding VDFProofSubmitted log: {str(e)}")
                return filtered_logs
            
            return logs

async def store_game_metadata(game_number: int, event_data: dict) -> None:
    """Store game metadata in the database."""
    try:
        block = await w3.eth.get_block(event_data['blockNumber'])
        
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
        
        supabase.table('game_metadata').upsert(metadata, on_conflict='game_number').execute()
    except Exception as e:
        print(f"Error storing metadata for game {game_number}: {str(e)}")

async def process_game_events():
    """Process game events for missing metadata."""
    try:
        while True:
            missing_games = await get_missing_metadata_games()
            
            if missing_games:
                print(f"Found {len(missing_games)} games missing metadata")
                
                for game_number in missing_games:
                    print(f"Processing events for game {game_number}")
                    events_found = False
                    
                    for event_name, event_signature in EVENT_SIGNATURES.items():
                        try:
                            logs = await get_event_logs(event_name, event_signature, game_number)
                            
                            for log in logs:
                                try:
                                    decoded_log = getattr(contract.events, event_name)().process_log(log)
                                    
                                    events_found = True
                                    await store_game_metadata(
                                        game_number=game_number,
                                        event_data={
                                            'event': event_name,
                                            'blockNumber': int(log['blockNumber'], 16),
                                            'transactionHash': log['transactionHash']
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
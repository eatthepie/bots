import os
import time
import json
import asyncio
import aiohttp
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables
load_dotenv()

# Configuration
CONTRACT_ADDRESS = os.getenv('CONTRACT_ADDRESS')
TICKET_PURCHASE_TOPIC = '0xc6e62c0043bd1f57ec9f9a5aacf40298041ce01894adf29dc17cfa28a2f5a5bd'  # Your ticket purchase event topic
RPC_URL = os.getenv('RPC_URL')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
SYNC_INTERVAL = 15 * 60  # 15 minutes in seconds
BATCH_SIZE = 2000

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def decode_ticket_numbers(log: dict) -> tuple:
    """
    Decode the ticket numbers from the TicketPurchased event
    Format: (address indexed player, uint256 gameNumber, uint256[3] numbers, uint256 etherball)
    """
    # Get game number from the non-indexed parameters
    data = log['data'][2:]  # remove '0x' prefix
    # Each parameter is 32 bytes (64 chars in hex)
    game_number = int(data[0:64], 16)
    # Next three numbers are in an array
    number1 = int(data[64:128], 16)
    number2 = int(data[128:192], 16)
    number3 = int(data[192:256], 16)
    # Last parameter is etherball
    etherball = int(data[256:320], 16)
    
    return number1, number2, number3, etherball, game_number

def create_event_signature(tx_hash: str, log_index: int) -> str:
    """Create a unique event signature from transaction hash and log index."""
    return f"{tx_hash}_{log_index}"

async def fetch_logs(session: aiohttp.ClientSession, from_block: int, to_block: int) -> list:
    """Fetch logs from the blockchain for the specified block range."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getLogs",
        "params": [{
            "address": CONTRACT_ADDRESS,
            "topics": [TICKET_PURCHASE_TOPIC],
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block)
        }]
    }

    async with session.post(RPC_URL, json=payload) as response:
        data = await response.json()
        if 'error' in data:
            raise Exception(f"RPC Error: {data['error']}")
        return data['result']

async def get_current_block(session: aiohttp.ClientSession) -> int:
    """Get the current block number."""
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_blockNumber",
        "params": [],
        "id": 1
    }

    async with session.post(RPC_URL, json=payload) as response:
        data = await response.json()
        return int(data['result'], 16)

def get_last_processed_block() -> int:
    """Get the last processed block from Supabase."""
    response = supabase.table('sync_metadata').select('last_block').single().execute()
    if response.data:
        return response.data['last_block']
    return 0

def update_last_processed_block(block_number: int) -> None:
    """Update the last processed block in Supabase."""
    supabase.table('sync_metadata').upsert({
        'id': 1,
        'last_block': block_number
    }).execute()

async def process_logs() -> None:
    """Main function to process logs in batches."""
    try:
        async with aiohttp.ClientSession() as session:
            current_block = await get_current_block(session)
            last_processed_block = get_last_processed_block()
            
            print(f"Syncing tickets from block {last_processed_block} to {current_block}")
            
            for from_block in range(last_processed_block + 1, current_block + 1, BATCH_SIZE):
                to_block = min(from_block + BATCH_SIZE - 1, current_block)
                
                try:
                    logs = await fetch_logs(session, from_block, to_block)
                    
                    if logs:
                        # Transform logs into ticket records
                        tickets = []
                        for log in logs:
                            num1, num2, num3, etherball, game_number = decode_ticket_numbers(log)
                            log_index = int(log['logIndex'], 16)
                            event_signature = create_event_signature(log['transactionHash'], log_index)
                            
                            tickets.append({
                                'event_signature': event_signature,
                                'transaction_hash': log['transactionHash'],
                                'log_index': log_index,
                                'block_number': int(log['blockNumber'], 16),
                                'number1': num1,
                                'number2': num2,
                                'number3': num3,
                                'number4': etherball,  # This is the etherball number
                                'game_number': game_number,
                                'is_winner': False,
                                'is_processed': False,
                                'created_at': datetime.utcnow().isoformat()
                            })
                        
                        # Batch insert into Supabase with conflict handling
                        supabase.table('tickets').upsert(
                            tickets,
                            on_conflict='event_signature'  # Use event_signature as unique constraint
                        ).execute()
                        
                        print(f"Processed {len(tickets)} tickets from blocks {from_block}-{to_block}")
                    
                    update_last_processed_block(to_block)
                    
                except Exception as e:
                    print(f"Error processing batch {from_block}-{to_block}: {str(e)}")
                    continue
                
    except Exception as e:
        print(f"Error in process_logs: {str(e)}")

async def main():
    """Main loop to run the sync process periodically."""
    while True:
        await process_logs()
        await asyncio.sleep(SYNC_INTERVAL)

if __name__ == "__main__":
    print("Starting ticket sync process...")
    asyncio.run(main())
import os
import csv
import sys
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
from typing import List, Dict

# Load environment variables
load_dotenv()

# Initialize Supabase client
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def process_transactions(csv_path: str, starting_game: int) -> List[Dict]:
    """Process CSV file into game metadata."""
    # Read CSV
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    # Group transactions by game
    current_game = starting_game
    game_metadata = {}
    
    for row in rows:
        tx_hash = row['Txhash'].strip('"')  # Remove quotes
        timestamp = datetime.fromtimestamp(int(row['UnixTimestamp'].strip('"'))).isoformat()
        method = row['Method'].strip('"')
        
        # Initialize game metadata if not exists
        if current_game not in game_metadata:
            game_metadata[current_game] = {
                'game_number': current_game,
                'draw_initiated_tx': None,
                'draw_initiated_time': None,
                'random_set_tx': None,
                'random_set_time': None,
                'vdf_proof_tx': None,
                'vdf_proof_time': None,
                'prize_payout_tx': None,
                'prize_payout_time': None
            }
        
        # Map method to metadata fields
        if method == "Initiate Draw":
            game_metadata[current_game]['draw_initiated_tx'] = tx_hash
            game_metadata[current_game]['draw_initiated_time'] = timestamp
        elif method == "Set Random":
            game_metadata[current_game]['random_set_tx'] = tx_hash
            game_metadata[current_game]['random_set_time'] = timestamp
        elif method == "Submit VDF Proof":
            game_metadata[current_game]['vdf_proof_tx'] = tx_hash
            game_metadata[current_game]['vdf_proof_time'] = timestamp
        elif method == "Calculate Payouts":
            game_metadata[current_game]['prize_payout_tx'] = tx_hash
            game_metadata[current_game]['prize_payout_time'] = timestamp
            # Move to next game after payouts
            current_game += 1
    
    return list(game_metadata.values())

def store_metadata(metadata_list: List[Dict]):
    """Store metadata in Supabase."""
    for metadata in metadata_list:
        print(f"Storing metadata for game {metadata['game_number']}:")
        print(metadata)
        response = supabase.table('game_metadata').upsert(metadata, on_conflict='game_number').execute()
        print(f"Stored successfully\n")

def main(csv_path: str, starting_game: int):
    """Main function to process CSV and store metadata."""
    print(f"Processing metadata starting from game {starting_game}")
    metadata_list = process_transactions(csv_path, starting_game)
    store_metadata(metadata_list)
    print("Processing complete!")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 process_metadata.py <csv_path> <starting_game_number>")
        sys.exit(1)
        
    csv_path = sys.argv[1]
    starting_game = int(sys.argv[2])
    
    main(csv_path, starting_game)
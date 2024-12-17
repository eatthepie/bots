# vdf_monitor.py
import json
import time
import os
import sys
from pathlib import Path

WATCH_FILE = 'vdf-needed.json'
COMPLETE_FILE = 'vdf-complete.json'

def check_for_work():
    if not Path(WATCH_FILE).exists():
        return None
    
    try:
        with open(WATCH_FILE, 'r') as f:
            data = json.load(f)
            # Move the file to show we're processing it
            os.rename(WATCH_FILE, 'vdf-processing.json')
            return data
    except:
        return None

def run_prover(randao_value):
    # Run your existing prover script
    os.system(f'python3 prover.py {randao_value}')
    
    # Once complete, signal completion
    if os.path.exists('proof.json'):
        # Mark as complete
        with open(COMPLETE_FILE, 'w') as f:
            json.dump({'status': 'complete'}, f)
        # Clean up processing file
        if os.path.exists('vdf-processing.json'):
            os.remove('vdf-processing.json')

def main():
    print("VDF Monitor started...")
    while True:
        work = check_for_work()
        if work:
            print(f"Found work to do for randao value: {work['randaoValue']}")
            run_prover(work['randaoValue'])
        time.sleep(60)  # Check every minute

if __name__ == "__main__":
    main()
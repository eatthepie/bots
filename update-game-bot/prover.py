# This file is adapted from heun630/VDF-Prover-Optimization
# Source: https://github.com/heun630/VDF-Prover-Optimization/blob/main/python/pietrzak_vdf.py

import sys
import logging
import time
from sympy import Integer
import json
from web3 import Web3
from typing import List, Tuple, Dict, Any
import pickle

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('Pietrzak_VDF')

# RSA modulus used for the VDF computation
N = 25195908475657893494027183240048398571429282126204032027777137836043662020707595556264018525880784406918290641249515082189298559149176184502808489120072844992687392807287776735971418347270261896375014971824691165077613379859095700097330459748808428401797429100642458691817195118746121515172654632282216869987549182422433637259085141865462043576798423387184774447920739934236584823824281198163815010674810451660377306056201619676256133844143603833904414952634432190114657544454178424020924616515723350778707749817125772467962926386356373289912154831438167899885040445364023527381951378636564391212010397122822120720357

# Time parameter - affects the delay duration
# Higher values increase the VDF computation time
T = 2**26

def pad_hex(x: str) -> str:
    """Ensures hex strings are correctly padded to 64 bytes for Keccak input"""
    n = (64 - (len(x) % 64)) % 64
    return ('0' * n) + x

def hash_eth(*integers: int) -> int:
    """
    Deterministic hash function using Keccak-256
    Formats integers as hex strings to ensure consistent hashing
    across different platforms and implementations
    """
    toHex = [format(int(x), '02x') for x in integers]
    padToEvenLen = [pad_hex(x) for x in toHex]
    input = ''.join(padToEvenLen)
    r = Web3.keccak(hexstr=input)
    return int(r.hex(), 16)

def hash_eth_128(*integers: int) -> int:
    """
    Returns a 128-bit hash by taking the upper half of hash_eth
    Using right shift ensures we get the most significant bits
    """
    r = hash_eth(*integers)
    return r >> 128

class VDFProver:
    def __init__(self, n: int, g: int, T: int):
        """
        Initialize VDF Prover with:
        n: RSA modulus
        g: Generator element
        T: Time parameter (affects delay duration)
        """
        self.n = n
        self.g = g
        self.T = T

    def vdf(self) -> Tuple[int, List[int]]:
        """
        Compute g^(2^T) mod n iteratively
        Returns both final result and intermediate values needed for proof
        Stores all steps to enable efficient proof generation
        """
        exp_list = []
        g = self.g
        exp_list.append(g)  # Store initial value for proof generation
        
        for _ in range(self.T):
            g = (g * g) % self.n
            exp_list.append(g)
            
        return g, exp_list

    @staticmethod
    def evaluate(exp_list: List[int], exp: int, n: int) -> int:
        """
        Fast modular exponentiation using precomputed values
        Used to efficiently compute intermediate values for proofs
        """
        res = 1
        i = 0
        while exp > 0:
            if exp & 1:
                res = (res * exp_list[i]) % n
            exp >>= 1
            i += 1
        return res

    @staticmethod
    def eval_v(n: int, x: int, T: int, r: int) -> int:
        """
        Compute intermediate verification value v
        This is a critical part of Pietrzak's VDF verification protocol
        Uses exact arithmetic with sympy.Integer to avoid precision issues
        """
        i = 1
        sum_part = 0
        Z = Integer  # Use sympy Integer for exact arithmetic
        for j in range(2 ** (i - 1)):
            product_part = 1
            for k in range(1, i - 1):
                if (j % 2 ** k) > 0:
                    product_part *= r[i - 1 - k]
            sum_part += product_part * pow(Z(2), ((T / Z(2) ** i) + j * pow(T, Z(2) ** (i - 1))))
        return pow(x, int(sum_part), n) % n

    def gen_single_halving_proof(self, claim: Tuple[int, int, int, int, int]) -> Tuple[int, int, int, int, int]:
        """
        Generate a single step in the iterative proof
        Implements one round of Pietrzak's proof protocol
        Returns intermediate values needed for verification
        """
        n, x, y, T, v = claim
        r = hash_eth_128(x, y, v)  # Challenge for Fiat-Shamir transform
        x_prime = pow(x, r, n) * v % n
        y_prime = pow(v, r, n) * y % n
        T_half = T // 2 if T % 2 == 0 else (T + 1) // 2
        v = self.eval_v(n, x_prime, T_half, r)
        return (n, x_prime, y_prime, T_half, v)

    def gen_recursive_halving_proof(self, claim: Tuple[int, int, int, int, int]) -> List[Tuple[int, int, int, int, int]]:
        """
        Generate complete proof through recursive halving
        Continues until T=1, resulting in a logarithmic-sized proof
        Each step halves T, leading to log(T) total steps
        """
        proof_list = [claim]
        while claim[3] > 1:  # Continue until T=1
            claim = self.gen_single_halving_proof(claim)
            proof_list.append(claim)
        return proof_list

    @staticmethod
    def format_proof_for_onchain(proof_list: List[Tuple[int, int, int, int, int]]) -> Dict[str, Any]:
        """
        Format proof for on-chain verification
        Converts values to hex format and includes necessary bit lengths
        Returns JSON-compatible structure for smart contract consumption
        """
        n_hex, x_hex, y_hex = map(hex, proof_list[0][:3])
        v_hex = [hex(p[4]) for p in proof_list[:-1]]

        return {
            "n": {"val": hex_to_bytes32(n_hex), "bitlen": compute_bitlen(n_hex)},
            "x": {"val": hex_to_bytes32(x_hex), "bitlen": compute_bitlen(x_hex)},
            "y": {"val": hex_to_bytes32(y_hex), "bitlen": compute_bitlen(y_hex)},
            "T": proof_list[0][3],
            "v": [{"val": hex_to_bytes32(val), "bitlen": compute_bitlen(val)} for val in v_hex]
        }

def hex_to_bytes32(hex_str: str) -> str:
    """Convert hex string to 32-byte format for EVM compatibility"""
    return hex_str[2:].zfill(64) if hex_str.startswith('0x') else hex_str.zfill(64)

def compute_bitlen(hex_str: str) -> int:
    """Calculate bit length of a hex value for gas optimization"""
    return int(hex_str, 16).bit_length()

def evaluate_vdf(g: int) -> Dict[str, Any]:
    """
    Main VDF evaluation function
    Computes VDF output, generates proof, and saves results
    Returns formatted proof suitable for on-chain verification
    """
    start_time = time.time()
    prover = VDFProver(N, g, T)

    print(f"Starting VDF evaluation with g={g}")
    y, exp_list = prover.vdf()
    T_half = T // 2 if T % 2 == 0 else (T + 1) // 2
    v = prover.evaluate(exp_list, pow(2, T_half), N)

    end_time = time.time()
    execution_time = end_time - start_time
    
    claim = (N, g, y, T, v)
    print(f"VDF evaluation completed in {execution_time:.2f} seconds")

    proof_list = prover.gen_recursive_halving_proof(claim)
    proof_json = prover.format_proof_for_onchain(proof_list)

    # Save proofs in both JSON and binary formats
    with open('proof.json', 'w') as f:
        json.dump(proof_json, f, indent=4)

    with open('proof.pickle', 'wb') as f:
        pickle.dump(proof_list, f)

    print("✅ Proof saved to proof.json and proof.pickle")
    return proof_json

def main():
    if len(sys.argv) < 2:
        print("Usage: python prover.py <g>")
        return

    g = int(sys.argv[1])
    evaluate_vdf(g)

if __name__ == '__main__':
    main()
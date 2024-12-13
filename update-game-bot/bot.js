// bot.js
import { createPublicClient, createWalletClient, http } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { mainnet } from "viem/chains";
import { exec } from "child_process";
import { promisify } from "util";
import fs from "fs/promises";
import dotenv from "dotenv";

dotenv.config();
const execAsync = promisify(exec);

const config = {
  RPC_URL: process.env.RPC_URL,
  PRIVATE_KEY: process.env.PRIVATE_KEY,
  CONTRACT_ADDRESS: process.env.CONTRACT_ADDRESS,
};

// Setup clients
const publicClient = createPublicClient({
  chain: mainnet,
  transport: http(config.RPC_URL),
});

const account = privateKeyToAccount(`0x${config.PRIVATE_KEY}`);
const walletClient = createWalletClient({
  account,
  chain: mainnet,
  transport: http(config.RPC_URL),
});

async function checkAndProcessGame() {
  try {
    // Get game info
    const gameNumber = await publicClient.readContract({
      address: config.CONTRACT_ADDRESS,
      abi: contractABI,
      functionName: "getCurrentGameNumber",
    });

    const gameInfo = await getDetailedGameInfo(
      publicClient,
      config.CONTRACT_ADDRESS,
      gameNumber
    );

    // Process based on game state
    if (gameInfo.status === 0) {
      // Initiate draw
      const hash = await initiateDraw(
        walletClient,
        publicClient,
        config.CONTRACT_ADDRESS
      );
      console.log("Draw initiated:", hash);
    } else if (gameInfo.status === 1) {
      // Check if buffer period passed
      const currentBlock = await publicClient.getBlockNumber();
      if (currentBlock >= gameInfo.drawInitiatedBlock + 5) {
        const hash = await setRandao(
          walletClient,
          publicClient,
          config.CONTRACT_ADDRESS,
          gameNumber
        );
        console.log("Randao set:", hash);
      }
    } else if (gameInfo.status === 2 && gameInfo.randaoValue) {
      // Run VDF proof
      await execAsync(`python prover.py ${gameInfo.randaoValue}`);

      // Read proof
      const proofData = JSON.parse(await fs.readFile("./proof.json", "utf8"));

      // Submit proof
      const y = {
        val: BigInt("0x" + proofData.y.val),
        bitlen: BigInt(proofData.y.bitlen),
      };
      const v = proofData.v.map((item) => ({
        val: BigInt("0x" + item.val),
        bitlen: BigInt(item.bitlen),
      }));

      const hash = await submitVDFProof(
        walletClient,
        publicClient,
        config.CONTRACT_ADDRESS,
        gameNumber,
        v,
        y
      );
      console.log("Proof submitted:", hash);
    }
  } catch (error) {
    console.error("Error:", error);
  }
}

// Run every minute
setInterval(checkAndProcessGame, 60000);
console.log("Bot started...");

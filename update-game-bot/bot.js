// bot.js
import { createPublicClient, createWalletClient, http, pad } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { mainnet, sepolia, worldchain } from "viem/chains";
import { exec } from "child_process";
import { promisify } from "util";
import fs from "fs/promises";
import dotenv from "dotenv";
import { setTimeout } from "timers/promises";
import { createRequire } from "module";

const require = createRequire(import.meta.url);
const abi = require("./abi.json");

dotenv.config();
const execAsync = promisify(exec);

const config = {
  RPC_URL: process.env.RPC_URL,
  PRIVATE_KEY: process.env.PRIVATE_KEY,
  CONTRACT_ADDRESS: process.env.CONTRACT_ADDRESS,
};

// Setup clients
const publicClient = createPublicClient({
  chain: worldchain,
  transport: http(config.RPC_URL),
});

const account = privateKeyToAccount(config.PRIVATE_KEY);
const walletClient = createWalletClient({
  account,
  chain: worldchain,
  transport: http(config.RPC_URL),
});

async function getDetailedGameInfo(publicClient, contractAddress, gameNumber) {
  const result = await publicClient.readContract({
    address: contractAddress,
    abi: abi,
    functionName: "getDetailedGameInfo",
    args: [BigInt(gameNumber)],
  });

  return {
    gameId: result.gameId,
    status: result.status,
    prizePool: result.prizePool,
    numberOfWinners: result.numberOfWinners,
    goldWinners: result.goldWinners,
    silverWinners: result.silverWinners,
    bronzeWinners: result.bronzeWinners,
    winningNumbers: result.winningNumbers,
    difficulty: result.difficulty,
    drawInitiatedBlock: result.drawInitiatedBlock,
    randaoBlock: result.randaoBlock,
    randaoValue: result.randaoValue,
    payouts: result.payouts,
  };
}

async function initiateDraw(walletClient, publicClient, contractAddress) {
  try {
    const { request } = await publicClient.simulateContract({
      address: contractAddress,
      abi: abi,
      functionName: "initiateDraw",
    });

    const hash = await walletClient.writeContract(request);
    return hash;
  } catch (error) {
    throw new Error(`Failed to initiate draw: ${error.message}`);
  }
}

async function setRandao(
  walletClient,
  publicClient,
  contractAddress,
  gameNumber
) {
  try {
    const { request } = await publicClient.simulateContract({
      address: contractAddress,
      abi: abi,
      functionName: "setRandom",
      args: [BigInt(gameNumber)],
    });

    const hash = await walletClient.writeContract(request);
    return hash;
  } catch (error) {
    throw new Error(`Failed to set RANDAO value: ${error.message}`);
  }
}

async function submitVDFProof(
  walletClient,
  publicClient,
  contractAddress,
  gameNumber,
  v,
  y
) {
  try {
    const { request } = await publicClient.simulateContract({
      address: contractAddress,
      abi: abi,
      functionName: "submitVDFProof",
      args: [BigInt(gameNumber), v, y],
    });

    const hash = await walletClient.writeContract(request);
    const receipt = await publicClient.waitForTransactionReceipt({ hash });
    return receipt.transactionHash;
  } catch (error) {
    throw new Error(`Failed to submit VDF proof: ${error.message}`);
  }
}

async function calculatePayouts(
  walletClient,
  publicClient,
  contractAddress,
  gameNumber
) {
  try {
    const { request } = await publicClient.simulateContract({
      address: contractAddress,
      abi: abi,
      functionName: "calculatePayouts",
      args: [BigInt(gameNumber)],
    });

    const hash = await walletClient.writeContract(request);
    return hash;
  } catch (error) {
    if (error.message.includes("Payouts already calculated for this game")) {
      console.log("Payouts have already been calculated. Exiting...");
      process.exit(0);
    }
    throw new Error(`Failed to calculate payouts: ${error.message}`);
  }
}

async function checkAndProcessGame(specificGameNumber = null) {
  try {
    // Get game info
    let gameNumber;
    if (specificGameNumber !== null) {
      gameNumber = BigInt(specificGameNumber);
      console.log(`\n🎮 Processing Game #${gameNumber.toString()}`);
    } else {
      gameNumber = await publicClient.readContract({
        address: config.CONTRACT_ADDRESS,
        abi: abi,
        functionName: "currentGameNumber",
      });
      console.log(`\n🎮 Processing Current Game #${gameNumber.toString()}`);
    }

    const gameInfo = await getDetailedGameInfo(
      publicClient,
      config.CONTRACT_ADDRESS,
      gameNumber
    );

    console.log("\n📊 Game Status:", {
      state: ["PENDING", "DRAWING", "COMPLETED"][gameInfo.status],
      prizePool: `${gameInfo.prizePool.toString()} wei`,
      winners: {
        gold: gameInfo.goldWinners.toString(),
        silver: gameInfo.silverWinners.toString(),
        bronze: gameInfo.bronzeWinners.toString(),
      },
      randaoValue: gameInfo.randaoValue.toString(),
    });

    // Process based on game state
    if (gameInfo.status === 0) {
      console.log("\n🎲 Initiating Draw...");
      const hash = await initiateDraw(
        walletClient,
        publicClient,
        config.CONTRACT_ADDRESS
      );
      console.log("✅ Draw initiated successfully!");
      console.log("📜 Transaction:", hash);
    } else if (gameInfo.status === 1 && gameInfo.randaoValue === BigInt(0)) {
      const currentBlock = await publicClient.getBlockNumber();
      const blocksRemaining =
        gameInfo.drawInitiatedBlock + BigInt(5) - currentBlock;

      if (currentBlock >= gameInfo.drawInitiatedBlock + BigInt(5)) {
        console.log("\n🎲 Setting RANDAO value...");
        const hash = await setRandao(
          walletClient,
          publicClient,
          config.CONTRACT_ADDRESS,
          gameNumber
        );
        console.log("✅ RANDAO value set successfully!");
        console.log("📜 Transaction:", hash);
      } else {
        console.log(
          `\n⏳ Waiting for buffer period... ${blocksRemaining} blocks remaining`
        );
      }
    } else if (gameInfo.status === 1 && gameInfo.randaoValue !== BigInt(0)) {
      console.log("\n🔢 Generating VDF proof...");
      await execAsync(`python3 prover.py ${gameInfo.randaoValue}`);
      console.log("✅ VDF proof generated");

      const proofData = JSON.parse(await fs.readFile("./proof.json", "utf8"));
      const { v, y } = prepareProofData(proofData);

      try {
        console.log("\n📤 Submitting proof to blockchain...");
        const hash = await submitVDFProof(
          walletClient,
          publicClient,
          config.CONTRACT_ADDRESS,
          gameNumber,
          v,
          y
        );
        console.log("✅ Proof submitted successfully!");
        console.log("📜 Transaction:", hash);
      } catch (error) {
        console.error("❌ Error submitting proof:", error);
      }

      await setTimeout(5000);

      console.log("\n💰 Calculating payouts...");
      const hashPayout = await calculatePayouts(
        walletClient,
        publicClient,
        config.CONTRACT_ADDRESS,
        gameNumber
      );
      console.log("✅ Payouts calculated successfully!");
      console.log("📜 Transaction:", hashPayout);

      // Stop the program after successful payout calculation
      process.exit(0);
    } else if (gameInfo.status === 2) {
      console.log("🎲 Game is completed. Exiting...");
      process.exit(0);
    }
  } catch (error) {
    console.error("❌ Error:", error);
  }
}

// Parse command line arguments
const gameArg = process.argv[2];
const specificGameNumber = gameArg ? parseInt(gameArg) : null;

if (specificGameNumber !== null) {
  // If a specific game number is provided, run once for that game
  console.log(`Processing specific game number: ${specificGameNumber}`);
  // checkAndProcessGame(specificGameNumber);
  setInterval(() => checkAndProcessGame(specificGameNumber), 60000 * 10);
} else {
  // Run every minute for current game
  setInterval(checkAndProcessGame, 60000 * 10);
  console.log("Bot started in continuous mode...");
}

function addHexPrefix(hex) {
  return hex.startsWith("0x") ? hex : `0x${hex}`;
}

function padHexValue(hex, bitlen) {
  return pad(hex, { size: Math.ceil(bitlen / 8) });
}

function prepareBigNumber(valHex, bitlen) {
  const prefixedHex = addHexPrefix(valHex);
  const paddedHex = padHexValue(prefixedHex, bitlen);

  return {
    val: paddedHex,
    bitlen: BigInt(bitlen),
  };
}

function prepareProofData(proofData) {
  const y = prepareBigNumber(proofData.y.val, proofData.y.bitlen);
  const v = proofData.v.map((bn) => prepareBigNumber(bn.val, bn.bitlen));
  return { v, y };
}

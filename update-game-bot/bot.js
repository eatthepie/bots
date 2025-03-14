import { createPublicClient, createWalletClient, http, parseEther } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { worldchain } from "viem/chains";
import { setTimeout } from "timers/promises";
import dotenv from "dotenv";
import { createRequire } from "module";

// Load environment variables
dotenv.config();

const require = createRequire(import.meta.url);
const abi = require("./abi.json");

// ---------------------------------------------------
// Configuration
// ---------------------------------------------------
const config = {
  RPC_URL: process.env.RPC_URL, // e.g. "https://rpc.worldchain.io"
  PRIVATE_KEY: process.env.PRIVATE_KEY, // A private key with funds on Worldchain
  CONTRACT_ADDRESS: process.env.CONTRACT_ADDRESS,
  // You can tweak these if you'd like to attempt multiple small amounts
  // for Witnet calls. We'll try them in ascending order if the TX reverts.
  WITNET_FUNDING_ATTEMPTS: [0.000001, 0.0000005, 0.000005],
};

// ---------------------------------------------------
// Setup Clients
// ---------------------------------------------------
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

// ---------------------------------------------------
// Helper: Get Detailed Game Info
// ---------------------------------------------------
async function getDetailedGameInfo(gameNumber) {
  const result = await publicClient.readContract({
    address: config.CONTRACT_ADDRESS,
    abi,
    functionName: "getDetailedGameInfo",
    args: [BigInt(gameNumber)],
  });

  // The contract returns a struct with many fields; map them neatly:
  return {
    gameId: result.gameId,
    status: result.status, // 0 => InPlay, 1 => Drawing, 2 => Completed
    prizePool: result.prizePool,
    numberOfWinners: result.numberOfWinners,
    goldWinners: result.goldWinners,
    silverWinners: result.silverWinners,
    bronzeWinners: result.bronzeWinners,
    winningNumbers: result.winningNumbers,
    difficulty: result.difficulty,
    drawInitiatedBlock: result.drawInitiatedBlock,
    randomValue: result.randomSeed,
    payouts: result.payouts,
  };
}

// ---------------------------------------------------
// Step 1: Initiate Draw
// ---------------------------------------------------
async function initiateDraw() {
  // We need to send some ETH for Witnet's randomness request.
  // We'll attempt multiple small amounts in ascending order.
  for (let i = 0; i < config.WITNET_FUNDING_ATTEMPTS.length; i++) {
    const attemptValue = config.WITNET_FUNDING_ATTEMPTS[i];
    try {
      console.log(
        `Attempting initiateDraw with value = ${attemptValue} ETH...`
      );

      // Actually send transaction with `value`
      const hash = await walletClient.writeContract({
        address: config.CONTRACT_ADDRESS,
        abi,
        functionName: "initiateDraw",
        value: parseEther(attemptValue.toString()),
      });
      console.log(`✅ initiateDraw success! TX Hash: ${hash}`);
      return hash;
    } catch (error) {
      // If we detect a revert about insufficient funds for Witnet,
      // we try the next higher amount. Otherwise, throw it.
      const errorMsg = error?.cause?.reason || error.message;
      console.warn(
        `initiateDraw attempt with ${attemptValue} ETH failed: ${errorMsg}`
      );

      // If the error is something else (like "Time interval not passed"), rethrow:
      if (
        !errorMsg.toLowerCase().includes("value not enough") &&
        !errorMsg.toLowerCase().includes("insufficient") &&
        !errorMsg.toLowerCase().includes("underpriced")
      ) {
        throw error;
      }
    }
  }
  throw new Error(
    "All attempts to fund Witnet randomness have failed. Increase WITNET_FUNDING_ATTEMPTS or check contract conditions."
  );
}

// ---------------------------------------------------
// Step 2: Set Random & Winning Numbers
// ---------------------------------------------------
async function setRandomAndWinningNumbers(gameNumber) {
  try {
    const { request } = await publicClient.simulateContract({
      address: config.CONTRACT_ADDRESS,
      abi,
      functionName: "setRandomAndWinningNumbers",
      args: [BigInt(gameNumber)],
    });
    const hash = await walletClient.writeContract(request);
    console.log(`✅ setRandomAndWinningNumbers success! TX Hash: ${hash}`);
    return hash;
  } catch (error) {
    throw new Error(`Failed to set random/winning numbers: ${error.message}`);
  }
}

// ---------------------------------------------------
// Step 3: Calculate Payouts
// ---------------------------------------------------
async function calculatePayouts(gameNumber) {
  try {
    const { request } = await publicClient.simulateContract({
      address: config.CONTRACT_ADDRESS,
      abi,
      functionName: "calculatePayouts",
      args: [BigInt(gameNumber)],
    });
    const hash = await walletClient.writeContract(request);
    console.log(`✅ calculatePayouts success! TX Hash: ${hash}`);
    return hash;
  } catch (error) {
    // Possibly "Payouts already calculated for this game"
    if (
      error.message.includes("Payouts already calculated for this game") ||
      error?.cause?.reason?.includes("Payouts already calculated for this game")
    ) {
      console.log("Payouts have already been calculated. Nothing to do here.");
      return null;
    }
    throw new Error(`Failed to calculate payouts: ${error.message}`);
  }
}

// ---------------------------------------------------
// Main Flow: Check Specific Game & Move It Forward
// ---------------------------------------------------
async function checkGameFlow(gameNum) {
  try {
    // Retrieve the detailed info for the specified game
    const info = await getDetailedGameInfo(gameNum);

    // The contract's `GameStatus`:
    // enum GameStatus { InPlay, Drawing, Completed }
    // 0 => InPlay, 1 => Drawing, 2 => Completed
    const status = Number(info.status);
    console.log(`\n--- Checking Game #${gameNum} ---`);
    console.log(
      `Status: ${["InPlay", "Drawing", "Completed"][status]}. RandomValue: ${
        info.randomValue
      }`
    );

    // If InPlay => try to initiate draw
    if (status === 0) {
      // Contract requires that the minimum time (4 days) has passed.
      // If it's not, the TX will revert with "Time interval not passed."
      console.log("Game is InPlay. Attempting to initiate draw...");
      await initiateDraw();
    }
    // If Drawing => we check randomValue
    else if (status === 1) {
      // If randomValue is 0 => we still need to call setRandomAndWinningNumbers
      if (info.randomValue === 0n) {
        console.log(
          "Game is Drawing. Random not set. Calling setRandomAndWinningNumbers..."
        );
        await setRandomAndWinningNumbers(gameNum);
      } else {
        // If randomValue != 0 => we can proceed to calculatePayouts
        console.log("Random is set. Calculating payouts...");
        await calculatePayouts(gameNum);
      }
    }
    // If Completed => do nothing
    else if (status === 2) {
      console.log("Game is Completed. Nothing to do.");
      process.exit(0);
    }
  } catch (err) {
    console.error(`❌ Error: ${err.message}`);
  }
}

// ---------------------------------------------------
// Scheduling / Interval
// ---------------------------------------------------

// Parse command line arguments
// First arg (process.argv[2]) is the game number
// Second arg (process.argv[3]) is the interval in minutes (optional)
const gameNumber = parseInt(process.argv[2], 10);
if (isNaN(gameNumber)) {
  console.error("Please provide a valid game number as the first argument");
  process.exit(1);
}

const argInterval = parseInt(process.argv[3], 10);
const intervalMinutes = isNaN(argInterval) ? 1 : argInterval;
const intervalMs = intervalMinutes * 60_000;

console.log(
  `\nLottery Bot started. Checking game #${gameNumber} every ${intervalMinutes} minute(s)...`
);

async function mainLoop() {
  while (true) {
    await checkGameFlow(gameNumber);
    // Wait the specified interval before checking again
    console.log(`Sleeping for ${intervalMinutes} minute(s)...`);
    await setTimeout(intervalMs);
  }
}

mainLoop().catch((e) => {
  console.error(`Fatal error in main loop: ${e.message}`);
  process.exit(1);
});

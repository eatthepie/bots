import { createPublicClient, createWalletClient, http, parseEther, formatEther } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { worldchain } from "viem/chains";
import { setTimeout } from "timers/promises";
import dotenv from "dotenv";
import { createRequire } from "module";
import https from "https";

// Load environment variables
dotenv.config();

const require = createRequire(import.meta.url);
const abi = require("./abi.json");

// ---------------------------------------------------
// Configuration
// ---------------------------------------------------
const config = {
  RPC_URL: process.env.RPC_URL,
  PRIVATE_KEY: process.env.PRIVATE_KEY,
  CONTRACT_ADDRESS: process.env.CONTRACT_ADDRESS,
  WITNET_FUNDING_ATTEMPTS: [0.000001, 0.0000005, 0.000005],
  // Social media Lambda config
  ROUND_COMPLETE_LAMBDA_URL: process.env.ROUND_COMPLETE_LAMBDA_URL,
  LAMBDA_PASSWORD: process.env.LAMBDA_PASSWORD,
};

// ---------------------------------------------------
// Interval Settings (in minutes)
// ---------------------------------------------------
const INTERVALS = {
  FAR_FROM_DRAW: 60,        // 1 hour when > 12 hours from draw
  APPROACHING_DRAW: 30,     // 30 mins when 2-12 hours from draw
  CLOSE_TO_DRAW: 10,        // 10 mins when 30min-2hr from draw
  VERY_CLOSE: 2,            // 2 mins when < 30 mins from draw
  DRAWING_IN_PROGRESS: 1,   // 1 min when drawing/completing
  WAITING_FOR_RANDOM: 5,    // 5 mins when waiting for Witnet random
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
// Helper: Get Current Game Info
// ---------------------------------------------------
async function getCurrentGameInfo() {
  const result = await publicClient.readContract({
    address: config.CONTRACT_ADDRESS,
    abi,
    functionName: "getCurrentGameInfo",
  });

  return {
    gameNumber: Number(result[0]),
    difficulty: Number(result[1]),
    prizePool: formatEther(result[2]),
    drawTime: Number(result[3]),
    timeUntilDraw: Number(result[4]),
  };
}

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

  return {
    gameId: Number(result.gameId),
    status: Number(result.status), // 0 => InPlay, 1 => Drawing, 2 => Completed
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
  for (let i = 0; i < config.WITNET_FUNDING_ATTEMPTS.length; i++) {
    const attemptValue = config.WITNET_FUNDING_ATTEMPTS[i];
    try {
      console.log(`Attempting initiateDraw with value = ${attemptValue} ETH...`);
      const ethValue = attemptValue.toFixed(18);
      const hash = await walletClient.writeContract({
        address: config.CONTRACT_ADDRESS,
        abi,
        functionName: "initiateDraw",
        value: parseEther(ethValue),
      });
      console.log(`✅ initiateDraw success! TX Hash: ${hash}`);
      return hash;
    } catch (error) {
      const errorMsg = error?.cause?.reason || error.message;
      console.warn(`initiateDraw attempt with ${attemptValue} ETH failed: ${errorMsg}`);

      if (
        !errorMsg.toLowerCase().includes("value not enough") &&
        !errorMsg.toLowerCase().includes("insufficient") &&
        !errorMsg.toLowerCase().includes("underpriced")
      ) {
        throw error;
      }
    }
  }
  throw new Error("All attempts to fund Witnet randomness have failed.");
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
    if (
      error.message.includes("Payouts already calculated") ||
      error?.cause?.reason?.includes("Payouts already calculated")
    ) {
      console.log("Payouts already calculated.");
      return null;
    }
    throw new Error(`Failed to calculate payouts: ${error.message}`);
  }
}

// ---------------------------------------------------
// Step 4: Notify Social Media
// ---------------------------------------------------
async function notifyRoundComplete(gameNumber) {
  if (!config.ROUND_COMPLETE_LAMBDA_URL || !config.LAMBDA_PASSWORD) {
    console.log("⚠️  Social media notifications not configured");
    return;
  }

  return new Promise((resolve, reject) => {
    const url = new URL(config.ROUND_COMPLETE_LAMBDA_URL);
    const body = JSON.stringify({
      roundNumber: gameNumber,
      password: config.LAMBDA_PASSWORD,
    });

    const options = {
      hostname: url.hostname,
      path: url.pathname,
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
      },
    };

    const req = https.request(options, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          console.log(`✅ Social media notification sent for round ${gameNumber}`);
          resolve(data);
        } else {
          console.error(`❌ Social media notification failed: ${res.statusCode}`);
          reject(new Error(`HTTP ${res.statusCode}: ${data}`));
        }
      });
    });

    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

// ---------------------------------------------------
// Calculate Smart Interval Based on Game State
// ---------------------------------------------------
function calculateInterval(status, timeUntilDraw, randomValue) {
  // If drawing in progress
  if (status === 1) {
    // Waiting for Witnet random
    if (randomValue === 0n) {
      return INTERVALS.WAITING_FOR_RANDOM;
    }
    // Random received, ready to calculate payouts
    return INTERVALS.DRAWING_IN_PROGRESS;
  }

  // If in play, base interval on time until draw
  if (status === 0) {
    const hoursUntilDraw = timeUntilDraw / 3600;

    if (hoursUntilDraw > 12) {
      return INTERVALS.FAR_FROM_DRAW;
    } else if (hoursUntilDraw > 2) {
      return INTERVALS.APPROACHING_DRAW;
    } else if (hoursUntilDraw > 0.5) {
      return INTERVALS.CLOSE_TO_DRAW;
    } else {
      return INTERVALS.VERY_CLOSE;
    }
  }

  // Default
  return INTERVALS.FAR_FROM_DRAW;
}

// ---------------------------------------------------
// Format time for logging
// ---------------------------------------------------
function formatTime(seconds) {
  if (seconds <= 0) return "0s";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);

  let parts = [];
  if (days > 0) parts.push(`${days}d`);
  if (hours > 0) parts.push(`${hours}h`);
  if (mins > 0) parts.push(`${mins}m`);
  return parts.join(" ") || "< 1m";
}

// ---------------------------------------------------
// Track notified games to avoid duplicate notifications
// ---------------------------------------------------
const notifiedGames = new Set();

// Track the game we're currently processing (to not lose track after initiateDraw)
let activeDrawingGame = null;

// ---------------------------------------------------
// Main Flow: Autonomous Game Management
// ---------------------------------------------------
async function checkAndProcessGame() {
  try {
    // Get current game info from contract
    const currentInfo = await getCurrentGameInfo();

    // If we have an active drawing game, keep processing it until completed
    // (After initiateDraw, getCurrentGameInfo returns the NEXT game, not the drawing one)
    let gameNum = currentInfo.gameNumber;

    if (activeDrawingGame !== null) {
      // Check if our active drawing game is still in progress
      const activeGameInfo = await getDetailedGameInfo(activeDrawingGame);
      if (activeGameInfo.status !== 2) {
        // Still drawing, keep working on it
        gameNum = activeDrawingGame;
        console.log(`\n${"=".repeat(50)}`);
        console.log(`📊 Game #${gameNum} | Status: ${["InPlay", "Drawing", "Completed"][activeGameInfo.status]} (active draw)`);

        const info = activeGameInfo;
        const status = info.status;

        if (status === 1) {
          // Drawing
          if (info.randomValue === 0n) {
            console.log("🎲 Waiting for Witnet random number...");
            try {
              await setRandomAndWinningNumbers(gameNum);
            } catch (err) {
              if (err.message.includes("Random number not yet available")) {
                console.log("   Random not ready yet, will retry...");
              } else {
                throw err;
              }
            }
            return INTERVALS.WAITING_FOR_RANDOM;
          } else {
            console.log("🎯 Random received! Calculating payouts...");
            await calculatePayouts(gameNum);
            return INTERVALS.DRAWING_IN_PROGRESS;
          }
        }

        // Shouldn't get here normally (status 0 with activeDrawingGame set)
        return INTERVALS.DRAWING_IN_PROGRESS;
      } else {
        // Game completed, send notification and clear active game
        if (!notifiedGames.has(activeDrawingGame)) {
          console.log(`\n✅ Game #${activeDrawingGame} completed! Sending social media notification...`);
          await setTimeout(3000);
          try {
            await notifyRoundComplete(activeDrawingGame);
            notifiedGames.add(activeDrawingGame);
          } catch (err) {
            console.error(`Failed to notify: ${err.message}`);
          }
        }
        console.log(`\n🆕 Moving to game #${currentInfo.gameNumber}`);
        activeDrawingGame = null;
        // Continue to process the current game below
      }
    }

    // Get detailed info for current game
    const info = await getDetailedGameInfo(gameNum);
    const status = info.status;
    const statusName = ["InPlay", "Drawing", "Completed"][status];

    console.log(`\n${"=".repeat(50)}`);
    console.log(`📊 Game #${gameNum} | Status: ${statusName}`);
    console.log(`💰 Prize Pool: ${currentInfo.prizePool} WLD`);

    if (status === 0) {
      console.log(`⏳ Time until draw: ${formatTime(currentInfo.timeUntilDraw)}`);
    }

    // Handle based on status
    if (status === 0) {
      // InPlay - check if draw time has passed
      if (currentInfo.timeUntilDraw <= 0) {
        console.log("⏰ Draw time reached! Initiating draw...");
        await initiateDraw();
        // Remember this game so we complete its draw process
        activeDrawingGame = gameNum;
        return INTERVALS.DRAWING_IN_PROGRESS;
      } else {
        console.log("⏳ Waiting for draw time...");
      }
    }
    else if (status === 1) {
      // Drawing (can happen if bot restarts mid-draw)
      activeDrawingGame = gameNum;
      if (info.randomValue === 0n) {
        console.log("🎲 Waiting for Witnet random number...");
        try {
          await setRandomAndWinningNumbers(gameNum);
        } catch (err) {
          if (err.message.includes("Random number not yet available")) {
            console.log("   Random not ready yet, will retry...");
          } else {
            throw err;
          }
        }
        return INTERVALS.WAITING_FOR_RANDOM;
      } else {
        console.log("🎯 Random received! Calculating payouts...");
        await calculatePayouts(gameNum);
        return INTERVALS.DRAWING_IN_PROGRESS;
      }
    }
    else if (status === 2) {
      // Completed - notify social media (only once per game)
      if (!notifiedGames.has(gameNum)) {
        console.log("✅ Game completed! Sending social media notification...");
        await setTimeout(3000);
        try {
          await notifyRoundComplete(gameNum);
          notifiedGames.add(gameNum);
        } catch (err) {
          console.error(`Failed to notify: ${err.message}`);
        }
      }

      // Check if next game has started
      const nextGameInfo = await getCurrentGameInfo();
      if (nextGameInfo.gameNumber > gameNum) {
        console.log(`\n🆕 New game #${nextGameInfo.gameNumber} detected!`);
      }
    }

    // Calculate smart interval
    return calculateInterval(status, currentInfo.timeUntilDraw, info.randomValue);

  } catch (err) {
    console.error(`❌ Error: ${err.message}`);
    return INTERVALS.FAR_FROM_DRAW; // Back off on error
  }
}

// ---------------------------------------------------
// Process a specific game to completion (one-shot)
// ---------------------------------------------------
async function processSpecificGame(gameNum) {
  console.log(`\n🔧 Processing game #${gameNum} to completion...`);

  while (true) {
    const info = await getDetailedGameInfo(gameNum);
    const status = info.status;
    const statusName = ["InPlay", "Drawing", "Completed"][status];

    console.log(`\n${"=".repeat(50)}`);
    console.log(`📊 Game #${gameNum} | Status: ${statusName}`);

    if (status === 0) {
      console.log("Game is InPlay. Initiating draw...");
      await initiateDraw();
      console.log("Draw initiated. Waiting 30 seconds...");
      await setTimeout(30000);
    }
    else if (status === 1) {
      if (info.randomValue === 0n) {
        console.log("🎲 Waiting for Witnet random number...");
        try {
          await setRandomAndWinningNumbers(gameNum);
          console.log("Random set! Waiting 10 seconds...");
          await setTimeout(10000);
        } catch (err) {
          if (err.message.includes("Random number not yet available")) {
            console.log("   Random not ready yet. Waiting 30 seconds...");
            await setTimeout(30000);
          } else {
            throw err;
          }
        }
      } else {
        console.log("🎯 Random received! Calculating payouts...");
        await calculatePayouts(gameNum);
        console.log("Payouts calculated. Waiting 10 seconds...");
        await setTimeout(10000);
      }
    }
    else if (status === 2) {
      console.log(`\n✅ Game #${gameNum} is COMPLETED!`);
      console.log(`   Winning numbers: ${info.winningNumbers.join(", ")}`);

      // Send notification
      try {
        await notifyRoundComplete(gameNum);
      } catch (err) {
        console.error(`Failed to notify: ${err.message}`);
      }

      console.log("\nDone processing this game.");
      process.exit(0);
    }
  }
}

// ---------------------------------------------------
// Main Loop
// ---------------------------------------------------

// Check for command line argument to process specific game
const forceGameNumber = parseInt(process.argv[2], 10);
if (!isNaN(forceGameNumber)) {
  console.log(`
╔═══════════════════════════════════════════════════╗
║         🥧 EAT THE PIE - LOTTERY BOT 🥧           ║
║              SINGLE GAME MODE                      ║
╚═══════════════════════════════════════════════════╝
`);
  processSpecificGame(forceGameNumber).catch((e) => {
    console.error(`Fatal error: ${e.message}`);
    process.exit(1);
  });
} else {
  console.log(`
╔═══════════════════════════════════════════════════╗
║         🥧 EAT THE PIE - LOTTERY BOT 🥧           ║
║                 AUTONOMOUS MODE                    ║
╚═══════════════════════════════════════════════════╝
`);

  console.log("Bot is running autonomously. It will:");
  console.log("  • Auto-detect current game");
  console.log("  • Initiate draw when time is up");
  console.log("  • Complete the drawing process");
  console.log("  • Post results to social media");
  console.log("  • Move to next game automatically");
  console.log("\nInterval adjusts based on game state:");
  console.log(`  • Far from draw (>12h): ${INTERVALS.FAR_FROM_DRAW} min`);
  console.log(`  • Approaching (2-12h): ${INTERVALS.APPROACHING_DRAW} min`);
  console.log(`  • Close (30m-2h): ${INTERVALS.CLOSE_TO_DRAW} min`);
  console.log(`  • Very close (<30m): ${INTERVALS.VERY_CLOSE} min`);
  console.log(`  • Drawing in progress: ${INTERVALS.DRAWING_IN_PROGRESS} min`);
  console.log("\nTip: Run with a game number to process a specific game:");
  console.log("     node bot.js 95");

  async function mainLoop() {
    while (true) {
      const intervalMinutes = await checkAndProcessGame();
      const intervalMs = intervalMinutes * 60_000;

      console.log(`\n💤 Next check in ${intervalMinutes} minute(s)...`);
      console.log(`   (${new Date(Date.now() + intervalMs).toLocaleTimeString()})`);

      await setTimeout(intervalMs);
    }
  }

  mainLoop().catch((e) => {
    console.error(`Fatal error: ${e.message}`);
    process.exit(1);
  });
}

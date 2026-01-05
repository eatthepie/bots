# bots

Various bots to automate tasks for Eat The Pie

# discord-status

A bot to update Discord with status of lottery

# game-bot

A bot to process game events. Check status, initiate draw, set randao, compute vdf, submit vdf, and calculate payouts.

# ticket-indexer

A bot to index tickets into a database.

# prize-bot

A bot to update processed_games table. The total_players and winners.

---

# Running with PM2

PM2 keeps bots running 24/7 and auto-restarts on crashes.

## Install PM2
```bash
npm install -g pm2
```

## Start the Lottery Bot
```bash
cd update-game-bot
pm2 start bot.js --name "lottery-bot"
```

## Common Commands
```bash
# View logs
pm2 logs lottery-bot

# Check status
pm2 status

# Restart
pm2 restart lottery-bot

# Stop
pm2 stop lottery-bot

# Delete from PM2
pm2 delete lottery-bot
```

## Auto-Start on Server Reboot
```bash
pm2 startup
pm2 save
```

This ensures the bot automatically restarts if the server reboots.

## Smart Polling Intervals

The lottery bot adjusts its check frequency based on game state:

| Condition | Interval |
|-----------|----------|
| Far from draw (>12h) | 60 min |
| Approaching (2-12h) | 30 min |
| Close (30m-2h) | 10 min |
| Very close (<30m) | 2 min |
| Drawing in progress | 1 min |
| Waiting for random | 5 min |

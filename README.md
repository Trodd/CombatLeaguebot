# 🏆 Discord Echo Combat League Management Bot

A full-featured Discord bot for running competitive leagues with Google Sheets as the database backend. Built with modularity, crash prevention, and full UI interactivity using `discord.py`.

---

## 📌 Features

### 🎮 Player Features
- Sign up with time zone and role (Player or League Sub)
- Request to join or leave teams
- Propose matches and submit scores (map-by-map with gamemode)
- Auto-pings teammates when match proposals or results are submitted

### 🛡️ Team & Match Management
- Create, rename, disband teams
- Promote captain or co-captain with Discord role sync
- Weekly auto-generated matches with fallback DMs
- Challenge match support with acceptance flow
- Score submission with interactive confirmation and map breakdown
- ELO-based **Team Leaderboard** and **Player Leaderboard**
- Forfeit handling, double forfeits, and historical archiving

### 🧰 Admin Tools
- Force schedule matches, scores, or weekly matchups
- Undo scores or clear proposed matches via panel
- Adjust team ELO or force disband teams
- Kick/ban players and auto-remove departed members
- Persistent views & panel rehydration after bot restart
- Real-time team embed updates showing captains, co-captains, and members

---

## ✅ Full Setup Guide

### 1. **Clone the Repository**

git clone https://github.com/Trodd/CombatLeaguebot.git
cd CombatLeaguebot

### 2. **Create Google Service Account Credentials**
        1. Go to https://console.cloud.google.com/
        2. Create or select a project
        3. Enable the following APIs:
           - Google Sheets API
           - Google Drive API
        4. Go to "APIs & Services > Credentials"
        5. Create a Service Account
        6. Generate a JSON key file
        7. Rename the downloaded file to: credentials.json
        8. Move it to your project root folder

### 3. **Set Up Google Sheet**
        1. Go to https://sheets.google.com
        2. Create a new spreadsheet (e.g. Leaguename)
        3. Share it with your service account email
           (found in credentials.json → "client_email")
        4. The bot will automatically create all required tabs on first run.

### 4. **Create Your config.json File**
          🔒 Replace all 1234567890 and "YOUR_..." values with real channel/user IDs and your bot token.

            {
          "bot_token": "YOUR_DISCORD_BOT_TOKEN",
          "sheet_name": "LeagueData",
        
          "notifications_channel_id": "1234567890",
          "match_channel_id": "1234567890",
          "score_channel_id": "1234567890",
          "results_channel_id": "1234567890",
          "panel_channel_id": "1234567890",
          "weekly_channel_id": "1234567890",
          "fallback_category_id": "1234567890",
          "dev_channel_id": "1234567890",
          "propose_channel_id": "1234567890",
          "scheduled_channel_id": "1234567890",
        
          "match_ping_full_team": true,
          "minimum_teams_start": 4,
          "team_min_players": 3,
          "team_max_players": 6,
          "elo_win_points": 25,
          "elo_loss_points": -25,
        
          "dev_override_ids": ["YOUR_DISCORD_USER_ID"]
        }

### **5. Install Python Dependencies**
      pip install -r requirements.txt

### **6. Run the bot
    Either do **Python league.py** in the cmd directory
    or just press on the **start.bat** file within the folder.



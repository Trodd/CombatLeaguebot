import discord
import gspread
import json
import os
from discord.ext import commands
from oauth2client.service_account import ServiceAccountCredentials
from discord.ext import tasks
import asyncio

print("🤖 Bot starting leaderboard check...")
# === Load config ===
with open("config.json") as f:
    config = json.load(f)

SHEET_NAME = config["sheet_name"]
CHANNEL_ID = int(config["leaderboard_channel_id"])
MESSAGE_ID_FILE = "leaderboard_msg_id.txt"

# === Google Sheets setup ===
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)
spreadsheet = client.open(SHEET_NAME)
leaderboard_sheet = spreadsheet.worksheet("Leaderboard")

# === Bot setup ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def get_tier_label(rating: int) -> str:
    r = int(rating)
    if r >= 1600: return "**🟪 IV**"
    elif r >= 1550: return "**🟪 III**"
    elif r >= 1500: return "**🟪 II**"
    elif r >= 1450: return "**🟪 I**"
    elif r >= 1400: return "**💎 IV**"
    elif r >= 1350: return "**💎 III**"
    elif r >= 1300: return "**💎 II**"
    elif r >= 1250: return "**💎 I**"
    elif r >= 1200: return "**🟦 IV**"
    elif r >= 1150: return "**🟦 III**"
    elif r >= 1100: return "**🟦 II**"
    elif r >= 1050: return "**🟦 I**"
    elif r >= 1000: return "**🟨 IV**"
    elif r >= 975: return "**🟨 III**"
    elif r >= 950: return "**🟨 II**"
    elif r >= 900: return "**🟨 I**"
    elif r >= 850: return "**⚪ IV**"
    elif r >= 825: return "**⚪ III**"
    elif r >= 800: return "**⚪ II**"
    elif r >= 750: return "**⚪ I**"
    elif r >= 700: return "**🟫 IV**"
    elif r >= 675: return "**🟫 III**"
    elif r >= 650: return "**🟫 II**"
    else: return "**🟫 I**"

@bot.event
async def on_ready():
    await post_or_update_leaderboard_embed()
    update_leaderboard_loop.start()

@tasks.loop(minutes=3600)               # Update leaderboard timer here
async def update_leaderboard_loop():
    await post_or_update_leaderboard_embed()

async def post_or_update_leaderboard_embed():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print("❗ Score channel not found.")
        return

    data = leaderboard_sheet.get_all_values()
    headers, rows = data[0], data[1:]

    if not rows:
        return

    sorted_rows = sorted(rows, key=lambda r: int(r[1]), reverse=True)

    # Load previous message IDs
    old_ids = []
    if os.path.exists(MESSAGE_ID_FILE):
        with open(MESSAGE_ID_FILE, "r") as f:
            try:
                old_ids = json.load(f)
            except:
                pass

    # --- Tier breakdown embed (FIRST) ---
    tier_legend = (
        "🟪 Master   → 1450–1600+\n"
        "🟦 Diamond  → 1250–1449\n"
        "💎 Platinum → 1050–1249\n"
        "🟨 Gold     →  900–1049\n"
        "⚪ Silver   →  750–899\n"
        "🟫 Bronze   → Below 750\n\n"
        "Tiers: I (lowest) → IV (highest)"
    )
    tier_embed = discord.Embed(
        title="📊 Rank Tier Breakdown",
        description=f"```{tier_legend}```",
        color=discord.Color.blue()
    )

    embeds = [tier_embed]

    # --- Paginate actual leaderboard entries ---
    chunks = [sorted_rows[i:i + 25] for i in range(0, len(sorted_rows), 25)]
    for page_num, chunk in enumerate(chunks, 1):
        embed = discord.Embed(
            title=f"🏆 Team Leaderboard (Page {page_num}/{len(chunks)})",
            description="Sorted by rating",
            color=discord.Color.purple()
        )
        embed.set_footer(text="Updated hourly")

        for i, row in enumerate(chunk, 1 + (page_num - 1) * 25):
            team, rating, wins, losses, matches = row[:5]
            tier = get_tier_label(int(rating))
            name_line = f"**#{i}** {tier} `{team}`"
            stats_line = f"🎯 {rating}  |  ✅ {wins}  ❌ {losses}  📊 {matches}"
            embed.add_field(name=name_line, value=stats_line, inline=False)

        embeds.append(embed)

    # --- Post or edit messages ---
    new_ids = []
    for idx, embed in enumerate(embeds):
        if idx < len(old_ids):
            try:
                msg = await channel.fetch_message(old_ids[idx])
                await msg.edit(embed=embed)
                await asyncio.sleep(10)
                new_ids.append(msg.id)
            except discord.NotFound:
                msg = await channel.send(embed=embed)
                new_ids.append(msg.id)
        else:
            msg = await channel.send(embed=embed)
            new_ids.append(msg.id)

    # --- Clean up old messages if needed ---
    for msg_id in old_ids[len(new_ids):]:
        try:
            msg = await channel.fetch_message(msg_id)
            await msg.delete()
        except:
            pass

    # --- Save updated IDs ---
    with open(MESSAGE_ID_FILE, "w") as f:
        json.dump(new_ids, f)

    print(f"✅ Team leaderboard updated across {len(new_ids)} embed(s).")

# --------- Run Player leaderboard ---------
import threading
import player_leaderboard  # Make sure this file exists and is structured correctly

def run_player_bot():
    player_leaderboard.bot.run(player_leaderboard.config["bot_token"])

if __name__ == "__main__":
    # Start player leaderboard in background thread
    threading.Thread(target=run_player_bot, daemon=True).start()

    # Start team leaderboard bot (this file)
    bot.run(config["bot_token"])




import discord
import gspread
import json
import os
from discord.ext import commands
from oauth2client.service_account import ServiceAccountCredentials
from discord.ext import tasks

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
bot = commands.Bot(command_prefix="!", intents=intents)

def get_tier_label(rating):
    r = int(rating)
    if r >= 1400:
        return "🟪 **Master**"
    elif r >= 1200:
        return "🟦 **Platinum**"
    elif r >= 1050:
        return "💎 **Diamond**"
    elif r >= 900:
        return "🟨 **Gold**"
    elif r >= 750:
        return "⚪ **Silver**"
    else:
        return "🟫 **Bronze**"

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
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
        await channel.send("📊 Leaderboard is currently empty.")
        return

    sorted_rows = sorted(rows, key=lambda r: int(r[1]), reverse=True)
    chunks = [sorted_rows[i:i + 25] for i in range(0, len(sorted_rows), 25)]

    # Load previous message IDs
    old_ids = []
    if os.path.exists(MESSAGE_ID_FILE):
        with open(MESSAGE_ID_FILE, "r") as f:
            try:
                old_ids = json.load(f)
            except:
                pass

    new_ids = []
    for page_num, chunk in enumerate(chunks, 1):
        embed = discord.Embed(
            title=f"🏆 League Leaderboard (Page {page_num}/{len(chunks)})",
            description="Sorted by rating",
            color=discord.Color.purple()
        )
        embed.set_footer(text="Mobile-friendly leaderboard • Updated hourly")

        for i, row in enumerate(chunk, 1 + (page_num - 1) * 25):
            team, rating, wins, losses, matches = row[:5]
            tier = get_tier_label(rating).split()[0]  # emoji only
            name_line = f"**#{i}** {tier} `{team}`"
            stats_line = f"🎯 {rating}  |  ✅ {wins}  ❌ {losses}  📊 {matches}"
            embed.add_field(name=name_line, value=stats_line, inline=False)

        if page_num <= len(old_ids):
            try:
                msg = await channel.fetch_message(old_ids[page_num - 1])
                await msg.edit(embed=embed)
                new_ids.append(msg.id)
            except discord.NotFound:
                msg = await channel.send(embed=embed)
                new_ids.append(msg.id)
        else:
            msg = await channel.send(embed=embed)
            new_ids.append(msg.id)

    # Delete any leftover old messages (if leaderboard has fewer pages now)
    if len(old_ids) > len(new_ids):
        for msg_id in old_ids[len(new_ids):]:
            try:
                msg = await channel.fetch_message(msg_id)
                await msg.delete()
            except:
                pass

    # Save new message IDs
    with open(MESSAGE_ID_FILE, "w") as f:
        json.dump(new_ids, f)

    print(f"✅ Leaderboard updated across {len(new_ids)} embed(s).")

# === Run bot ===
bot.run(config["bot_token"])

import discord
import gspread
import json
import os
import asyncio
from discord.ext import commands, tasks
from oauth2client.service_account import ServiceAccountCredentials

has_run_on_ready = False

# === Load config ===
with open("config.json") as f:
    config = json.load(f)

SHEET_NAME = config["sheet_name"]
TEAM_CHANNEL_ID = int(config["leaderboard_channel_id"])
PLAYER_CHANNEL_ID = int(config["player_leaderboard_channel_id"])
TEAM_MESSAGE_FILE = "leaderboard_msg_id.txt"
PLAYER_MESSAGE_FILE = "PLAYER_leaderboard_msg_id.txt"

# === Google Sheets setup ===
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)
spreadsheet = client.open(SHEET_NAME)
team_sheet = spreadsheet.worksheet("Leaderboard")
player_sheet = spreadsheet.worksheet("Player Leaderboard")

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


def build_team_embeds(rows):
    sorted_rows = sorted(rows, key=lambda r: int(r[1]), reverse=True)
    chunks = [sorted_rows[i:i + 25] for i in range(0, len(sorted_rows), 25)]

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
        title="📊 Team Rank Tier Breakdown (Page 1)",
        description=f"```{tier_legend}```",
        color=discord.Color.blue()
    )
    tier_embed.set_footer(text="Updated hourly • Leaderboard starts on next page")

    embeds = [tier_embed]
    for page_num, chunk in enumerate(chunks, 1):
        embed = discord.Embed(
            title=f"🏆 Team Leaderboard (Page {page_num + 1}/{len(chunks) + 1})",
            description="Sorted by rating",
            color=discord.Color.purple()
        )
        embed.set_footer(text="Updated hourly • Tier breakdown on Page 1")

        for i, row in enumerate(chunk, 1 + (page_num - 1) * 25):
            team, rating, wins, losses, matches = row[:5]
            tier = get_tier_label(int(rating))
            embed.add_field(
                name=f"**#{i}** {tier} `{team}`",
                value=f"🎯 {rating}  |  ✅ {wins}  ❌ {losses}  📊 {matches}",
                inline=False
            )
        embeds.append(embed)

    return embeds


def build_player_embeds(rows):
    sorted_rows = sorted(rows, key=lambda r: int(r[2]), reverse=True)
    chunks = [sorted_rows[i:i + 25] for i in range(0, len(sorted_rows), 25)]

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
        title="📊 Player Rank Tier Breakdown (Page 1)",
        description=f"```{tier_legend}```",
        color=discord.Color.blue()
    )
    tier_embed.set_footer(text="Updated hourly • Leaderboard starts on next page")

    embeds = [tier_embed]
    for page_num, chunk in enumerate(chunks, 1):
        embed = discord.Embed(
            title=f"🏆 Player Leaderboard (Page {page_num + 1}/{len(chunks) + 1})",
            description="Sorted by rating",
            color=discord.Color.purple()
        )
        embed.set_footer(text="Updated hourly • Tier breakdown on Page 1")

        for i, row in enumerate(chunk, 1 + (page_num - 1) * 25):
            username, user_id, rating, wins, losses, matches = row[:6]
            tier = get_tier_label(int(rating))
            embed.add_field(
                name=f"**#{i}** {tier} `{username}`",
                value=f"🎯 {rating}  |  ✅ {wins}  ❌ {losses}  📊 {matches}",
                inline=False
            )
        embeds.append(embed)

    return embeds


async def update_embeds(channel_id, message_file, embeds):
    channel = bot.get_channel(channel_id)
    if not channel:
        print(f"❗ Channel ID {channel_id} not found.")
        return

    old_ids = []
    if os.path.exists(message_file):
        try:
            with open(message_file, "r") as f:
                old_ids = json.load(f)
        except:
            pass

    new_ids = []
    for idx, embed in enumerate(embeds):
        try:
            if idx < len(old_ids):
                try:
                    msg = await channel.fetch_message(old_ids[idx])
                    await msg.edit(embed=embed)
                except (discord.NotFound, discord.Forbidden):
                    msg = await channel.send(embed=embed)
            else:
                msg = await channel.send(embed=embed)
            await asyncio.sleep(1)
            new_ids.append(msg.id)
        except Exception as e:
            print(f"❗ Failed to post/embed message: {e}")

    # Delete leftovers
    for msg_id in old_ids[len(new_ids):]:
        try:
            msg = await channel.fetch_message(msg_id)
            await msg.delete()
        except:
            pass

    with open(message_file, "w") as f:
        json.dump(new_ids, f)

    print(f"✅ Updated {len(new_ids)} messages in <#{channel_id}>")


@bot.event
async def on_ready():
    global has_run_on_ready
    if has_run_on_ready:
        return

    print(f"✅ Logged in as {bot.user}")
    has_run_on_ready = True

    await asyncio.sleep(10)
    leaderboard_updater.start()


@tasks.loop(minutes=3600)
async def leaderboard_updater():
    await update_leaderboards()

async def update_leaderboards():
    try:
        team_data = team_sheet.get_all_values()[1:]
        player_data = player_sheet.get_all_values()[1:]

        team_embeds = build_team_embeds(team_data)
        player_embeds = build_player_embeds(player_data)

        await update_embeds(TEAM_CHANNEL_ID, TEAM_MESSAGE_FILE, team_embeds)
        await update_embeds(PLAYER_CHANNEL_ID, PLAYER_MESSAGE_FILE, player_embeds)

    except Exception as e:
        print(f"❗ Error updating leaderboards: {e}")


if __name__ == "__main__":
    bot.run(config["bot_token"])



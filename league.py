import discord
from discord.ext import commands, tasks
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import match
import dev
import command_buttons  # <-- League Command Panel buttons
import asyncio, json
import re
import concurrent.futures
from command_buttons import SignupView
from discord import Embed, NotFound, HTTPException
from discord.ui import View, Button
import os
from command_buttons import AcceptDenyJoinRequestView

# -------------------- Load config --------------------

with open("config.json") as f:
    config = json.load(f)

BOT_TOKEN = config["bot_token"]
SHEET_NAME = config["sheet_name"]
DEV_OVERRIDE_IDS = config.get("dev_override_ids", [])
NOTIFICATIONS_CHANNEL_ID = config.get("notifications_channel_id")
MATCH_CHANNEL_ID = config.get("match_channel_id")
SCORE_CHANNEL_ID = config.get("score_channel_id")
RESULTS_CHANNEL_ID = config.get("results_channel_id")
PANEL_CHANNEL_ID = config.get("panel_channel_id")
TEAM_MIN_PLAYERS = int(config.get("team_min_players", 3))
TEAM_MAX_PLAYERS = int(config.get("team_max_players", 6))
ELO_WIN_POINTS = config.get("elo_win_points", 25)
ELO_LOSS_POINTS = config.get("elo_loss_points", -25)
TEAM_LIST_CHANNEL_ID = config.get("team_list_channel_id")

# -------------------- Google Sheets Setup --------------------

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)

try:
    spreadsheet = client.open(SHEET_NAME)
except gspread.SpreadsheetNotFound:
    spreadsheet = client.create(SHEET_NAME)

def get_or_create_sheet(spreadsheet, name, headers):
    try:
        sheet = spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=name, rows="100", cols=str(len(headers)))
        sheet.append_row(headers)
    return spreadsheet.worksheet(name)

players_sheet = get_or_create_sheet(spreadsheet, "Players", ["User ID", "Username", "Role", "Timezone"])
teams_sheet = get_or_create_sheet(spreadsheet, "Teams", ["Team Name", "Player 1", "Player 2", "Player 3", "Player 4", "Player 5", "Player 6", "Status"])
matches_sheet = get_or_create_sheet(spreadsheet, "Matches", ["match_id", "Team A", "Team B", "Proposed Date", "Scheduled Date", "Status", "Winner", "Loser", "Proposed By"])
scoring_sheet = get_or_create_sheet(spreadsheet, "Scoring", [
    "Match ID", "Team A", "Team B",
    "Map 1 Mode", "Map 1 A", "Map 1 B",
    "Map 2 Mode", "Map 2 A", "Map 2 B",
    "Map 3 Mode", "Map 3 A", "Map 3 B",
    "Total A", "Total B",
    "Maps Won A", "Maps Won B",
    "Winner"
])
leaderboard_sheet = get_or_create_sheet(spreadsheet, "Leaderboard", ["Team Name", "Rating", "Wins", "Losses", "Matches Played"])
player_leaderboard_sheet = get_or_create_sheet(spreadsheet, "Player Leaderboard", ["Username", "User ID", "Rating", "Wins", "Losses", "Matches Played"])
proposed_sheet = get_or_create_sheet(spreadsheet, "Match Proposed", ["Match ID", "Team A", "Team B", "Proposer ID", "Proposed Date", "Channel ID", "Message ID"])
proposed_scores_sheet = get_or_create_sheet(spreadsheet, "Proposed Scores", ["Match ID", "Team A", "Team B", "Proposer ID", "Proposed Date", "Channel ID", "Message ID"])
scheduled_sheet = get_or_create_sheet(spreadsheet, "Match Scheduled", ["Match ID","Team A", "Team B", "Scheduled Date"])
weekly_matches_sheet = get_or_create_sheet(spreadsheet, "Weekly Matches", ["Week", "Team A", "Team B", "Match ID", "Scheduled Date"])
challenge_sheet = get_or_create_sheet(spreadsheet, "Challenge Matches", ["Week", "Match ID", "Team A", "Team B", "Proposer ID", "Proposed Date", "Completion Date", "Status"])
banned_sheet = get_or_create_sheet(spreadsheet, "Banned", ["User ID", "Username", "Reason", "Banned By", "Date"])
match_history_sheet = get_or_create_sheet(spreadsheet, 
    "Match History",
    [
        "Week", "Match ID", "Team A", "Team B", "Proposed Date", "Scheduled Date",
        "Map 1 Mode", "Map 1 A", "Map 1 B",
        "Map 2 Mode", "Map 2 A", "Map 2 B",
        "Map 3 Mode", "Map 3 A", "Map 3 B",
        "Total A", "Total B", "Maps Won A", "Maps Won B", "Winner"
    ]
)
rename_log_sheet = get_or_create_sheet(spreadsheet, "Team Rename Log", ["Role ID", "Team Name", "Last Rename UTC"])

# -------------------- Bot Setup --------------------

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  
intents.guilds = True
intents.presences = False
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)
bot.players_sheet = players_sheet
bot.teams_sheet = teams_sheet
bot.proposed_scores_sheet = proposed_scores_sheet
bot.config = config  # ✅ Very important → allows match.py and others to access config
bot.spreadsheet = spreadsheet
bot.player_leaderboard_sheet = player_leaderboard_sheet
bot.leaderboard_sheet = leaderboard_sheet

match.setup_match_module(bot, spreadsheet)
@bot.event
async def on_ready():
    print(f"Bot is ready as {bot.user}")

    # ✅ Delete old Dev Panels first
    await dev.cleanup_dev_panels(bot)

@bot.event
async def on_message(message):
    if message.author.bot:
        return  # Ignore other bots
    return  # Don't process any commands

# -------------------- Helper Functions --------------------

async def validate_roles(bot):
    guild = discord.utils.get(bot.guilds)
    if not guild:
        print("❗ Bot is not connected to any guild.")
        return

    role_keys = {
        "player_role_id": "Player",
        "league_sub_role_id": "League Sub",
        "universal_captain_role_id": "Captain"
    }

    for key, name in role_keys.items():
        role_id = bot.config.get(key)
        if not guild.get_role(role_id):
            print(f"⚠️ Configured role '{name}' not found in guild.")

async def send_to_channel(channel_id, message=None, embed=None):
    if channel_id:
        channel = bot.get_channel(channel_id)
        if channel:
            await channel.send(content=message, embed=embed)

async def send_notification(message=None, embed=None):
    await send_to_channel(NOTIFICATIONS_CHANNEL_ID, message, embed)

def get_team_rating(team_name):
    for idx, row in enumerate(leaderboard_sheet.get_all_values(), 1):
        if row[0] == team_name:
            return idx, int(row[1]), int(row[2]), int(row[3]), int(row[4])
    return None

def update_team_rating(team_name, won):
    team = get_team_rating(team_name)
    if team:
        idx, rating, wins, losses, matches = team
        new_rating = rating + ELO_WIN_POINTS if won else rating + ELO_LOSS_POINTS
        leaderboard_sheet.update(f"B{idx}", [[new_rating, wins + (1 if won else 0), losses + (0 if won else 1), matches + 1]])
    else:
        starting = 1025 if won else 975
        leaderboard_sheet.append_row([team_name, starting, 1 if won else 0, 0 if won else 1, 1])

async def auto_update_team_embeds(bot, teams_sheet):
    await bot.wait_until_ready()

    cache_file = "team_message_cache.json"

    try:
        with open(cache_file, "r") as f:
            message_cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        message_cache = {}

    while not bot.is_closed():
        channel_id = bot.config.get("team_list_channel_id")
        if not channel_id:
            await asyncio.sleep(300)
            continue

        channel = bot.get_channel(channel_id)
        if not channel:
            await asyncio.sleep(300)
            continue

        # 🔁 Always rebuild cache if empty
        if not message_cache:
            async for msg in channel.history(limit=100):
                if msg.author == bot.user and msg.embeds:
                    embed = msg.embeds[0]
                    team_name = embed.title.strip() if embed.title else None
                    if team_name:
                        message_cache[team_name] = msg.id

        current_teams = {}
        updated_cache = {}

        def fetch_rows(sheet):
            return sheet.get_all_values()[1:]

        with concurrent.futures.ThreadPoolExecutor() as pool:
            try:
                team_rows = await asyncio.get_event_loop().run_in_executor(pool, fetch_rows, teams_sheet)
            except Exception as e:
                print(f"❗ Sheet fetch error: {e}")
                await asyncio.sleep(300)
                continue

        for row in team_rows:
            if not row or not row[0].strip():
                continue

            team_name = row[0].strip()
            members = row[1:7]
            mentions = []

            for idx, member in enumerate(members):
                if not member.strip():
                    continue

                # Skip captain (slot 1 / index 0)
                if idx == 0:
                    continue

                # Extract user ID
                user_id = None
                if "(" in member and ")" in member:
                    user_id = member.split("(")[-1].split(")")[0]
                    mention = f"<@{user_id}>"
                else:
                    mention = member.strip()

                # 🧢 Only add emoji if this is slot 2 AND they have co-captain role
                if idx == 1 and user_id:
                    co_captain_role_id = bot.config.get("co_captain_role_id")
                    guild = channel.guild
                    member_obj = guild.get_member(int(user_id)) if guild else None

                    if (
                        member_obj and
                        co_captain_role_id and
                        any(role.id == co_captain_role_id for role in member_obj.roles)
                    ):
                        mention += " 🧢"

                mentions.append(mention)

            # Slot 1 (index 0) is the captain
            if len(members) > 0 and "(" in members[0] and ")" in members[0]:
                captain_id = members[0].split("(")[-1].split(")")[0]
                captain = f"<@{captain_id}>"
            elif len(members) > 0 and members[0].strip():
                captain = members[0].strip()
            else:
                captain = "N/A"

            embed = Embed(title=team_name, color=0x3498db)
            embed.add_field(name="👑 Captain", value=captain, inline=False)
            embed.add_field(
                name="👥 Players",
                value="\n".join(mentions) if mentions else "No other players listed.",
                inline=False
            )

            current_teams[team_name] = True
            message_id = message_cache.get(team_name)

            if message_id:
                try:
                    msg = await channel.fetch_message(message_id)
                    current_embed = msg.embeds[0].to_dict() if msg.embeds else {}

                    if current_embed != embed.to_dict():
                        try:
                            await msg.edit(embed=embed)
                        except HTTPException as e:
                            if e.status == 429:
                                print(f"⚠️ Rate limit hit editing {team_name}. Backing off.")
                                await asyncio.sleep(5)
                                await msg.edit(embed=embed)
                    updated_cache[team_name] = msg.id
                    continue
                except NotFound:
                    pass

            msg = await channel.send(embed=embed)
            updated_cache[team_name] = msg.id

        # 🧹 Cleanup stale team messages
        for team_name, msg_id in message_cache.items():
            if team_name not in current_teams:
                try:
                    msg = await channel.fetch_message(msg_id)
                    await msg.delete()
                except NotFound:
                    pass

        # 💾 Save only the current team messages
        message_cache = updated_cache
        with open(cache_file, "w") as f:
            json.dump(message_cache, f, indent=2)

        await asyncio.sleep(300)

        #------------- Scoring Sumbit Modal ------------

        class SubmitScoreModal(discord.ui.Modal, title="Submit Match Scores"):
            # Score Inputs
            map1_a = discord.ui.TextInput(label="Map 1 - Team A Score", required=True)
            map1_b = discord.ui.TextInput(label="Map 1 - Team B Score", required=True)
            map2_a = discord.ui.TextInput(label="Map 2 - Team A Score", required=True)
            map2_b = discord.ui.TextInput(label="Map 2 - Team B Score", required=True)
            map3_a = discord.ui.TextInput(label="Map 3 - Team A Score", required=True)
            map3_b = discord.ui.TextInput(label="Map 3 - Team B Score", required=True)

            # Gamemode Inputs
            map1_mode = discord.ui.TextInput(label="Map 1 - Gamemode", required=True, placeholder="e.g. Payload")
            map2_mode = discord.ui.TextInput(label="Map 2 - Gamemode", required=True, placeholder="e.g. Control Point")
            map3_mode = discord.ui.TextInput(label="Map 3 - Gamemode", required=True, placeholder="e.g. Payload")

            def __init__(self, parent, match_id, team_a, team_b):
                super().__init__()
                self.parent = parent
                self.match_id = match_id
                self.team_a = team_a
                self.team_b = team_b

                # Register gamemode inputs in the modal
                self.add_item(self.map1_mode)
                self.add_item(self.map2_mode)
                self.add_item(self.map3_mode)

            async def on_submit(self, interaction: discord.Interaction):
                # Score data
                map_scores = [
                    (int(self.map1_a.value), int(self.map1_b.value)),
                    (int(self.map2_a.value), int(self.map2_b.value)),
                    (int(self.map3_a.value), int(self.map3_b.value)),
                ]

                # Total and map wins
                total_a = sum([s[0] for s in map_scores])
                total_b = sum([s[1] for s in map_scores])
                maps_won_a = sum(1 for s in map_scores if s[0] > s[1])
                maps_won_b = sum(1 for s in map_scores if s[1] > s[0])

                winner = (
                    self.team_a if total_a > total_b else
                    self.team_b if total_b > total_a else
                    self.team_a if maps_won_a > maps_won_b else
                    self.team_b if maps_won_b > maps_won_a else
                    "Tie"
                )

                # Compose row including gamemodes
                row = [
                    self.match_id,
                    self.team_a,
                    self.team_b,
                    self.map1_mode.value, map_scores[0][0], map_scores[0][1],
                    self.map2_mode.value, map_scores[1][0], map_scores[1][1],
                    self.map3_mode.value, map_scores[2][0], map_scores[2][1],
                    total_a,
                    total_b,
                    maps_won_a,
                    maps_won_b,
                    winner
                ]

                # Save to scoring sheet
                scoring_sheet = get_or_create_sheet(
                    self.parent.spreadsheet,
                    "Scoring",
                    [
                        "Match ID", "Team A", "Team B",
                        "Map 1 Mode", "Map 1 A", "Map 1 B",
                        "Map 2 Mode", "Map 2 A", "Map 2 B",
                        "Map 3 Mode", "Map 3 A", "Map 3 B",
                        "Total A", "Total B",
                        "Maps Won A", "Maps Won B",
                        "Winner"
                    ]
                )
                scoring_sheet.append_row(row)

                await interaction.response.send_message(f"✅ Score submitted! **Winner: {winner}**", ephemeral=True)

# -------------------- Bot Ready Event --------------------

bot.was_disconnected = False  # Define this once near the top after bot = commands.Bot(...)

def extract_id(text):
    match = re.search(r"\((\d{17,20})\)", text)
    return match.group(1) if match else None

async def cleanup_departed_members(bot, players_sheet, teams_sheet):
    print("[🧹] Running startup cleanup...")

    guild = bot.get_guild(config.get("guild_id"))
    if not guild:
        print("❌ Could not find guild.")
        return

    member_ids = {str(m.id) for m in guild.members}
    removed = 0
    disbanded = 0
    promoted = 0

    # Remove from Players sheet
    players_rows = players_sheet.get_all_values()
    for i in range(len(players_rows) - 1, 0, -1):
        row = players_rows[i]
        if not row: continue
        uid = row[0]
        if uid not in member_ids:
            players_sheet.delete_rows(i + 1)
            removed += 1
            await send_notification(f"🗑️ Removed `{row[1]}` from the league (no longer in server).")

    # Clean up Teams sheet
    team_rows = teams_sheet.get_all_values()
    for i in range(len(team_rows) - 1, 0, -1):
        row = team_rows[i]
        if not any(row): continue

        team_name = row[0]
        captain_id = extract_id(row[1])
        team_member_ids = [extract_id(cell) for cell in row[1:7] if cell]
        missing = [uid for uid in team_member_ids if uid not in member_ids]

        if captain_id and captain_id not in member_ids:
            replacement = extract_id(row[2]) if len(row) > 2 else None
            if replacement and replacement in member_ids:
                teams_sheet.update_cell(i + 1, 2, row[2])  # Promote Player 2
                captain_role_id = bot.config.get("universal_captain_role_id")
                if replacement and captain_role_id:
                    role = discord.utils.get(guild.roles, id=captain_role_id)
                    member = guild.get_member(int(replacement))
                    if role and member:
                        await member.add_roles(role, reason="Promoted to Captain")
                teams_sheet.update_cell(i + 1, 3, "")      # Clear old P2
                promoted += 1
                await send_notification(f"👑 Captain left — promoted Player 2 to captain of **{team_name}**.")
            else:
                teams_sheet.delete_rows(i + 1)
                disbanded += 1
                await send_notification(f"❌ Team **{team_name}** was disbanded (no captain or players left).")

                # 🧼 Delete associated team roles
                for suffix in ["", " Captain"]:
                    role_name = f"Team {team_name}{suffix}"
                    role = discord.utils.get(guild.roles, name=role_name)
                    if role:
                        try:
                            await role.delete()
                        except Exception as e:
                            print(f"[⚠️] Could not delete role {role_name}: {e}")

        else:
            for j in range(1, 7):
                cell = row[j]
                uid = extract_id(cell)
                if uid in missing:
                    teams_sheet.update_cell(i + 1, j + 1, "")
                    await send_notification(f"🚪 Removed `{cell}` from **{team_name}** (left the server)")

    print(f"[✅] Cleanup done — Removed: {removed}, Disbanded: {disbanded}, Promoted: {promoted}")

@bot.event
async def on_member_remove(member):
    user_id = str(member.id)
    username = member.name

    players = bot.players_sheet
    teams = bot.teams_sheet

    # Remove from Players sheet
    removed_from_players = False
    player_rows = players.get_all_values()
    for i, row in enumerate(player_rows[1:], start=2):
        if user_id == row[0] or username in row[1].lower():
            players.delete_rows(i)
            removed_from_players = True
            break

    # Search Teams sheet
    team_rows = teams.get_all_values()
    for i, row in enumerate(team_rows[1:], start=2):
        if not any(row):
            continue

        team_name = row[0]
        for j in range(1, 7):
            cell_id = extract_id(row[j])
            if cell_id == user_id:
                teams.update_cell(i, j + 1, "")  # Clear the player cell

                if j == 1:  # Captain left
                    replacement = row[2] if len(row) > 2 and row[2].strip() else ""
                    if replacement:
                        teams.update_cell(i, 2, replacement)  # Promote Player 2
                        teams.update_cell(i, 3, "")           # Optional: clear Player 2
                        await send_notification(f"👑 `{username}` left — promoted Player 2 to captain for **{team_name}**.")
                    else:
                        # 🧼 Delete associated team roles
                        guild = member.guild
                        for suffix in ["", " Captain"]:
                            role_name = f"Team {team_name}{suffix}"
                            role = discord.utils.get(guild.roles, name=role_name)
                            if role:
                                try:
                                    await role.delete()
                                except Exception as e:
                                    print(f"[⚠️] Could not delete role {role_name}: {e}")

                        teams.delete_rows(i)
                        await send_notification(f"❌ `{username}` left — disbanded team **{team_name}** (no other players).")

                else:
                    await send_notification(f"🚪 `{username}` left and was removed from **{team_name}**.")
                return

    if removed_from_players:
        await send_notification(f"🚪 `{username}` left the server and was removed from the league.")


@tasks.loop(seconds=30)
async def watchdog_check():
    try:
        if bot.is_closed():
            print("[🔌] Bot connection closed.")
            return

        if bot.was_disconnected:
            print("[🔁] Bot reconnected to Discord.")
            channel = bot.get_channel(config.get("dev_channel_id"))
            if channel:
                await channel.send("✅ Bot has reconnected to Discord.")
            bot.was_disconnected = False

        _ = len(bot.guilds)  # Health check

    except Exception as e:
        print(f"[⚠️] Discord unreachable: {e}")
        bot.was_disconnected = True
# Rehydrate join team request
PENDING_JOIN_FOLDER = "json"
PENDING_JOIN_FILE = os.path.join(PENDING_JOIN_FOLDER, "pending_join_requests.json")

async def rehydrate_join_requests(bot):
    if not os.path.exists(PENDING_JOIN_FILE):
        return

    try:
        with open(PENDING_JOIN_FILE, "r") as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ Failed to read join request file: {e}")
        return

    valid_entries = []

    for entry in data:
        try:
            guild = bot.get_guild(entry["guild_id"])
            if not guild:
                continue

            try:
                channel = await bot.fetch_channel(entry["channel_id"])
            except discord.NotFound:
                print(f"⚠️ Channel {entry['channel_id']} no longer exists. Skipping join request.")
                continue

            try:
                message = await channel.fetch_message(entry["message_id"])
            except discord.NotFound:
                print(f"⚠️ Message {entry['message_id']} no longer exists. Skipping join request.")
                continue

            captain = guild.get_member(entry["user_id"])
            invitee = guild.get_member(entry["user_id"])
            if not captain or not invitee:
                continue

            view = AcceptDenyJoinRequestView(
                parent_view=bot.league_panel,
                team_name=entry["team"],
                invitee=invitee,
                guild_id=guild.id,
                captain=captain
            )
            await message.edit(view=view)
#            if not bot.is_closed():
#                bot.loop.create_task(view.expire_notice_dm())

            valid_entries.append(entry)

        except Exception as e:
            print(f"⚠️ Failed to rehydrate join request message: {e}")

    # ✍️ Rewrite the file with only valid entries
    with open(PENDING_JOIN_FILE, "w") as f:
        json.dump(valid_entries, f, indent=2)

@bot.event
async def on_ready():
    await bot.tree.sync()
    await validate_roles(bot)
    for guild in bot.guilds:
        await guild.chunk()
    try:
        bot.add_view(SignupView(bot, parent=None))
    except Exception as e:
        print(f"❌ Failed to register SignupView: {e}")


    await cleanup_departed_members(bot, players_sheet, teams_sheet)

    if not watchdog_check.is_running():
        watchdog_check.start()

    print(f"Bot ready as {bot.user}")

    panel_channel = bot.get_channel(PANEL_CHANNEL_ID)
    if panel_channel:
        # --- DELETE old panel messages ---
        try:
            async for msg in panel_channel.history(limit=50):
                if msg.author == bot.user and msg.embeds:
                    if msg.embeds[0].title == "📋 League Command Panel":
                        await msg.delete()
        except Exception as e:
            print(f"Failed to delete old panel: {e}")

        # --- POST new panel ---
        view = command_buttons.LeaguePanel(
            bot,
            spreadsheet,
            players_sheet,
            teams_sheet,
            matches_sheet,
            scoring_sheet,
            leaderboard_sheet,
            proposed_sheet,
            proposed_scores_sheet,
            scheduled_sheet,
            weekly_matches_sheet,
            challenge_sheet,
            send_to_channel,
            send_notification,
            DEV_OVERRIDE_IDS
        )

        bot.add_view(view)
        bot.league_panel = view  # make LeaguePanel accessible for views like AcceptDenyMatchView
        await rehydrate_join_requests(bot)
        bot.scheduled_sheet = scheduled_sheet
        channel_id = config.get("panel_channel_id")
        if not channel_id:
            print("❌ No panel_channel_id configured.")
            return

        channel = bot.get_channel(channel_id)
        if not channel:
            print(f"❌ League panel channel not found: {channel_id}")
            return

        # Look for existing panel message
        existing = None
        async for msg in channel.history(limit=25):
            if msg.author == bot.user and len(msg.components) > 0:
                existing = msg
                break

        if existing:
            try:
                await existing.edit(view=bot.league_panel)
            except Exception as e:
                print(f"❌ Failed to reattach LeaguePanel: {e}")
        else:
            try:
                embed = discord.Embed(
                    title="📋 League Command Panel",
                    description="Use the buttons below to manage your league participation, teams, and matches!",
                    color=discord.Color.blue()
                )

                embed.add_field(
                    name="__**🧍 General Signup/Unsignup**__",
                    value=(
                        "`✅ Player Signup` – Join as a Player or League Sub\n"
                        "`👥 Join Team` – Request to join an existing team\n"
                        "`❌ Unsignup` – Leave the league (must leave team first)\n"
                        "`🚪 Leave Team` – Leave your current team"
                    ),
                    inline=False
                )

                embed.add_field(
                    name="__**👥 Team Management**__",
                    value=(
                        "`🏷️ Create Team` – Register a new team\n"
                        "`✏️ Change Team Name` – Rename your existing team (captain only)\n"
                        "`⭐ Promote Player` – Assign a new captain/co-captain\n"
                        "`❗ Disband Team` – Permanently disband your team(team will not have history)\n"
                        "`👢 Kick Player` - Kick a player from your team\n"
                        "`📡 Team Status` – Captains/co-captains can mark their team as **Active** or **Inactive**"
                    ),
                    inline=False
                )

                embed.add_field(
                    name="__**📅 Match Controls**__",
                    value=(
                        "`📅 Propose Match` – Schedule a match with another team\n"
                        "`🏆 Propose Score` – Log match scores map-by-map\n"
                        "`🔍 Find Eligible Subs` – Ping the top 24 League Subs for your team.\n"
                        "`🎲 Coin Flip` - Flip a coin to see who picks first map."
                    ),
                    inline=False
                )

                embed.add_field(
                    name="__**🎟️ Ticket Info**__",
                    value=(
                        "**🛠️ Match Dispute** – Open a ticket if there’s a scoring or rule violation issue\n"
                        "**🚫 Report Player/Sub** – Report misconduct, cheating, or eligibility violations\n\n"
                        "👉 Go to the https://discord.com/channels/779349159852769310/1175645368806035518 channel to open one"
                    ),
                    inline=False
                )

                embed.set_footer(text="⚡ Some actions require being a captain or co-captain.")

                await channel.send(embed=embed, view=bot.league_panel)

            except Exception as e:
                print(f"❌ Failed to post LeaguePanel: {e}")
    

        # ✅ Rehydrate match proposals
    proposed_rows = proposed_sheet.get_all_values()[1:]
    for idx, row in enumerate(proposed_rows, start=2):  # start=2 to skip header
        if len(row) < 7:
            continue

        match_id, team_a, team_b, proposer_id, proposed_date, channel_id, message_id = row
        if not channel_id or not message_id or not channel_id.isdigit() or not message_id.isdigit():
            print(f"⚠️ Skipping {match_id}: Invalid or missing channel/message ID")
            continue

        try:
            channel = await bot.fetch_channel(int(channel_id))
            message = await channel.fetch_message(int(message_id))

            from command_buttons import AcceptDenyMatchView
            from datetime import datetime

            # Parse date from <t:timestamp:f> format
            timestamp = int(proposed_date.split(":")[1])
            proposed_datetime = datetime.utcfromtimestamp(timestamp)

            view = AcceptDenyMatchView(
                parent=bot.league_panel,
                team_a=team_a,
                team_b=team_b,
                proposed_date_str=proposed_date,
                match_id=match_id,
                proposer_id=proposer_id,
                proposed_datetime=proposed_datetime,
                match_type="challenge" if "Challenge" in match_id else "assigned"
            )
            view.message = message
            view.channel_to_delete = channel
            await message.edit(view=view)

        except Exception as e:
            print(f"❌ Failed to rehydrate {match_id}: {e}")
            try:
                proposed_sheet.delete_rows(idx)
            except Exception as cleanup_error:
                print(f"⚠️ Failed to delete row {idx} for {match_id}: {cleanup_error}")
    
        # ✅ Rehydrate score confirmations
    scheduled_rows = spreadsheet.values_get("Match Scheduled!A:D").get("values", [])[1:]
    scheduled_ids = [row[0] for row in scheduled_rows if row]
    score_rows = proposed_scores_sheet.get_all_values()[1:]

    for idx, row in enumerate(score_rows, start=2):  # start=2 accounts for the header row
        if len(row) < 7:
            continue

        match_id, team1, team2, proposer_id, proposed_date, channel_id, message_id = row

        # Only rehydrate if it's still in the Proposed Scores sheet
        proposed_rows = spreadsheet.worksheet("Proposed Scores").get_all_values()[1:]
        proposed_ids = [row[0].strip() for row in proposed_rows]

        if match_id not in proposed_ids:
            print(f"⛔ Skipping rehydration: match {match_id} not in Proposed Scores sheet")
            continue

        if not channel_id or not message_id or not channel_id.isdigit() or not message_id.isdigit():
            print(f"⚠️ Skipping score {match_id}: Invalid or missing channel/message ID")
            continue

        try:
            channel = await bot.fetch_channel(int(channel_id))
            message = await channel.fetch_message(int(message_id))
            guild = channel.guild

            proposer = guild.get_member(int(proposer_id))
            if not proposer:
                continue

            from command_buttons import ConfirmScoreView
            from datetime import datetime

            proposed_datetime = datetime.utcnow()

            match_info = {
                "match_id": match_id,
                "team1": team1,
                "team2": team2,
                "date": proposed_date,
                "proposed_datetime": proposed_datetime,
                "is_challenge": "Challenge" in match_id,
            }

            view = ConfirmScoreView(
                bot.league_panel,
                match=match_info,
                map_scores=[],
                proposer=proposer,
                proposer_id=proposer_id,
                private_channel=channel
            )
            view.message = message
            view.channel_to_delete = channel
            await message.edit(view=view)

        except Exception as e:
            print(f"❌ Failed to rehydrate score for {match_id}: {e}")
            try:
                proposed_scores_sheet.delete_rows(idx)  # ✅ now using correct row index
            except Exception as cleanup_error:
                print(f"⚠️ Failed to clean up score row for {match_id}: {cleanup_error}")

    # ✅ POST DEV PANEL TOO
    await dev.post_dev_panel(bot, spreadsheet, DEV_OVERRIDE_IDS, send_notification)
    print("Posted Dev Panel!")

    # ✅ Start team embed updater
    bot.loop.create_task(auto_update_team_embeds(bot, teams_sheet))

bot.run(BOT_TOKEN)


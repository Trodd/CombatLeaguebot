import discord
from discord.ext import commands
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import match
import dev
import command_buttons  # <-- League Command Panel buttons
import asyncio, json
from discord import Embed, NotFound, HTTPException

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

players_sheet = get_or_create_sheet(spreadsheet, "Players", ["User ID", "Username"])
teams_sheet = get_or_create_sheet(spreadsheet, "Teams", ["Team Name", "Player 1", "Player 2", "Player 3", "Player 4", "Player 5", "Player 6"])
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
proposed_sheet = get_or_create_sheet(spreadsheet, "Match Proposed", ["Match ID", "Team A", "Team B", "Proposer ID", "Proposed Date", "Channel ID", "Message ID"])
proposed_scores_sheet = get_or_create_sheet(spreadsheet, "Proposed Scores", ["Match ID", "Team A", "Team B", "Proposer ID", "Proposed Date", "Channel ID", "Message ID"])
scheduled_sheet = get_or_create_sheet(spreadsheet, "Match Scheduled", ["match id","Team A", "Team B", "Proposer ID", "Scheduled Date", "Channel ID", "Message ID"])
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
# -------------------- Bot Setup --------------------

intents = discord.Intents.default()
intents.members = True  # ✅ FIXED: Required to use fetch_members and see all members

bot = commands.Bot(command_prefix="!", intents=intents)
bot.proposed_scores_sheet = proposed_scores_sheet
bot.config = config  # ✅ Very important → allows match.py and others to access config
bot.spreadsheet = spreadsheet

match.setup_match_module(bot, spreadsheet)
@bot.event
async def on_ready():
    print(f"Bot is ready as {bot.user}")

    # ✅ Delete old Dev Panels first
    await dev.cleanup_dev_panels(bot)

    # ✅ Post new Dev Panels
    await dev.post_dev_panel(bot, spreadsheet, DEV_OVERRIDE_IDS)

    # ✅ Post other panels (League etc) if needed


# -------------------- Helper Functions --------------------

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

        if not message_cache:
            async for msg in channel.history(limit=100):
                if msg.author == bot.user:
                    break
            else:
                print("[📤] No existing team messages found. Reposting all team embeds.")
                message_cache.clear()

        current_teams = {}
        updated_cache = {}

        for row in teams_sheet.get_all_values()[1:]:
            if not row or not row[0].strip():
                continue

            team_name = row[0]
            members = row[1:7]
            mentions = []

            for member in members:
                if "(" in member and ")" in member:
                    user_id = member.split("(")[-1].split(")")[0]
                    mentions.append(f"<@{user_id}>")
                elif member.strip():
                    mentions.append(member.strip())

            captain = mentions[0] if mentions else "N/A"

            embed = Embed(title=f"{team_name}", color=0x3498db)
            embed.add_field(name="👑 Captain", value=captain, inline=False)
            embed.add_field(name="👥 Players", value="\n".join(mentions) if mentions else "None", inline=False)

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
                      #  print(f"📝 Updated embed for {team_name}")
                   # else:
                      #  print(f"✅ No change for {team_name}")
                    updated_cache[team_name] = msg.id
                    continue
                except NotFound:
                    pass

            msg = await channel.send(embed=embed)
            updated_cache[team_name] = msg.id
            print(f"📤 Posted new embed for {team_name}")

        for team_name, msg_id in message_cache.items():
            if team_name not in current_teams:
                try:
                    msg = await channel.fetch_message(msg_id)
                    await msg.delete()
                    print(f"🗑️ Deleted old embed for {team_name}")
                except NotFound:
                    pass

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
                scoring_sheet = get_or_create_sheet("Scoring", [
                    "Match ID", "Team A", "Team B",
                    "Map 1 Mode", "Map 1 A", "Map 1 B",
                    "Map 2 Mode", "Map 2 A", "Map 2 B",
                    "Map 3 Mode", "Map 3 A", "Map 3 B",
                    "Total A", "Total B",
                    "Maps Won A", "Maps Won B",
                    "Winner"
                ])
                scoring_sheet.append_row(row)

                await interaction.response.send_message(f"✅ Score submitted! **Winner: {winner}**", ephemeral=True)

# -------------------- Bot Ready Event --------------------

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Bot ready as {bot.user}")

    panel_channel = bot.get_channel(PANEL_CHANNEL_ID)
    if panel_channel:
        # --- DELETE old panel messages ---
        try:
            async for msg in panel_channel.history(limit=50):
                if msg.author == bot.user and msg.embeds:
                    if msg.embeds[0].title == "📋 League Command Panel":
                        await msg.delete()
                        print("Deleted old League Command Panel.")
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

        embed = discord.Embed(
            title="📋 League Command Panel",
            description="Use the buttons below to manage your league registration, teams, and matches!",
            color=discord.Color.blue()
        )

        embed.add_field(name="✅ Player Signup", value="Sign up to participate in the league and become eligible to join or create teams.", inline=False)
        embed.add_field(name="🏷️ Create Team", value="Register a new team. Captains can form teams and receive matches once the minimum players requirement is met.", inline=False)
        embed.add_field(name="➕ Request to Join Team", value="Request to join an existing team. The team captain must approve your request.", inline=False)
        embed.add_field(name="⭐ Promote Player", value="Team captains can promote another player to become the new captain.", inline=False)
        embed.add_field(name="🚪 Leave Team", value="Leave your current team (only if you're not the captain).", inline=False)
        embed.add_field(name="❌ Unsignup", value="Remove yourself from the league. You must leave your team first to do this.", inline=False)
        embed.add_field(name="❗ Disband Team", value="Disband your team permanently (Captains and Developers only).", inline=False)
        embed.add_field(name="📅 Propose Match", value="Propose a match against another team. The opponent captain must accept. If they can't be DMed, a fallback private channel is used.", inline=False)
        embed.add_field(name="📊 Propose Score", value="Submit map-by-map scores after a match. Opponent captain must confirm. Uses fallback channel if needed.", inline=False)

        embed.set_footer(text="⚡ Some actions require being a captain or developer. Captains manage teams and approve join requests.")

        await panel_channel.send(embed=embed, view=view)
        print("Posted new League Command Panel!")
        bot.loop.create_task(auto_update_team_embeds(bot, teams_sheet))

    # ✅ POST DEV PANEL TOO
    await dev.post_dev_panel(bot, spreadsheet, DEV_OVERRIDE_IDS)
    print("Posted Dev Panel!")

bot.run(BOT_TOKEN)



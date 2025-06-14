import discord
from discord.ui import View, Button, Modal, TextInput
import json
import re
import pytz
from datetime import datetime, timedelta, timezone
from re import split
import traceback

# Helper function to extract user ID from "Name (ID)"
def extract_user_id(profile_string):
    """Extract user ID from profile string like Username#1234 | ID OR Username (ID) OR Username"""
    if "|" in profile_string:
        return profile_string.split("|")[-1].strip()
    elif "(" in profile_string and ")" in profile_string:
        return profile_string.split("(")[-1].split(")")[0].strip()
    else:
        return ""

# Helper to check roster lock timestamp
def is_roster_locked(config):
    timestamp = config.get("roster_lock_timestamp", "")
    if not timestamp:
        return False
    try:
        lock_time = datetime.fromisoformat(timestamp)
        return datetime.utcnow() >= lock_time
    except Exception:
        return False

    # Helper function to get or create a Google Sheet tab
def get_or_create_sheet(spreadsheet, name, headers):
    try:
        sheet = spreadsheet.worksheet(name)
    except Exception:
        spreadsheet.add_worksheet(title=name, rows="100", cols=str(len(headers)))
        sheet = spreadsheet.worksheet(name)
        sheet.append_row(headers)

    # 🔧 Attach the sheet title manually for raw append fallback
    sheet._sheet_title = name
    return sheet



async def safe_send(interaction, content, ephemeral=True):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)
    except discord.NotFound:
        print("❗ Tried to send to an expired interaction.")

class AcceptDenyMatchView(discord.ui.View):
            def __init__(self, parent, team_a, team_b, proposed_date_str, match_id, proposer_id, match_type="assigned", week_number=None, proposed_datetime=None):
                super().__init__(timeout=None)
                self.parent = parent
                self.team_a = team_a
                self.team_b = team_b
                self.proposed_date = proposed_date_str
                self.match_id = match_id
                self.match_type = match_type
                self.week_number = week_number
                self.proposed_datetime = proposed_datetime
                self.proposer_id = proposer_id
                self.message = None
                self.channel_to_delete = None

            @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success, custom_id="league:match_accept")
            async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
                async def safe_send(interaction, content, ephemeral=True):
                    try:
                        if interaction.response.is_done():
                            await interaction.followup.send(content, ephemeral=ephemeral)
                        else:
                            await interaction.response.send_message(content, ephemeral=ephemeral)
                    except discord.NotFound:
                        print("❗ Tried to respond to an expired interaction.")

                if interaction.user.id == int(self.proposer_id):
                    await safe_send(interaction, "❗ Only the opponent captain may respond...", ephemeral=True)
                    return
                await interaction.response.defer(ephemeral=True)

                if getattr(self, "already_responded", False):
                    return
                self.already_responded = True

                for item in self.children:
                    item.disabled = True
                await interaction.message.edit(view=self)

                discord_ts = int(self.proposed_datetime.timestamp())
                discord_time_fmt = f"<t:{discord_ts}:f>"
                discord_relative = f"<t:{discord_ts}:R>"

                # Add to scheduled sheet
                try:
                    body = {
                        "values": [[self.match_id, self.team_a, self.team_b, self.proposed_date]]
                    }
                    self.parent.spreadsheet.values_append(
                        "Match Scheduled!A:D",
                        params={"valueInputOption": "USER_ENTERED"},
                        body=body
                    )
                except Exception as e:
                    print(f"[❌] Low-level append to Match Scheduled failed: {e}")

                # ✅ Remove from Proposed Matches
                for idx, row in enumerate(self.parent.proposed_sheet.get_all_values()[1:], start=2):
                    if row and row[0].strip().lower() == self.match_id.strip().lower():
                        self.parent.proposed_sheet.delete_rows(idx)
                        break

                # Add to Matches if it's a challenge
                if self.match_type == "challenge":
                    self.parent.matches_sheet.append_row([
                        self.match_id, self.team_a, self.team_b,
                        self.proposed_date, self.proposed_date,
                        "Scheduled", "", "", ""
                    ])

                # Update existing Matches row
                match_sheet = get_or_create_sheet(
                    self.parent.spreadsheet, "Matches",
                    ["Match ID", "Team A", "Team B", "Proposed Date", "Scheduled Date", "Status", "Winner", "Loser", "Proposed By"]
                )

                for idx, row in enumerate(match_sheet.get_all_values()[1:], start=2):
                    row_id = row[0].strip().lower()
                    match_id = self.match_id.strip().lower()

                    # Strip prefix and split
                    row_parts = split(r'\d+-', row_id)[-1].split('-')  # e.g., Amo-Bis
                    match_parts = split(r'\d+-', match_id)[-1].split('-')  # e.g., Bis-Amo

                    if sorted(row_parts) == sorted(match_parts):
                        match_sheet.update_cell(idx, 4, self.proposed_date)
                        match_sheet.update_cell(idx, 5, self.proposed_date)
                        match_sheet.update_cell(idx, 6, "Scheduled")
                        break
                else:
                    print(f"[⚠️] Match ID {self.match_id} not found in Matches sheet")

                msg = (
                    f"✅ **Match Accepted:** `{self.team_a} vs {self.team_b}`\n"
                    f"🕓 Scheduled for {discord_time_fmt} ({discord_relative})"
                )
                await interaction.followup.send(msg, ephemeral=True)  # ✅ safe after defer()

                # Send to scheduled match channel
                match_channel = self.parent.bot.get_channel(self.parent.config.get("scheduled_channel_id"))
                if match_channel:
                    match_type_str = "Challenge Match" if self.match_type == "challenge" else f"Assigned Match (Week {self.week_number})"

                    embed = discord.Embed(
                        title="📅 Match Scheduled",
                        description=(
                            f"**{self.team_a}** vs **{self.team_b}**\n"
                            f"🕓 {discord_time_fmt} ({discord_relative})\n"
                            f"🏷️ {match_type_str}"
                        ),
                        color=discord.Color.green()
                    )

                    def get_mentions(team_name):
                        row = next((r for r in self.parent.teams_sheet.get_all_values() if r[0] == team_name), [])
                        mentions = []
                        guild = discord.utils.get(self.parent.bot.guilds)  # works in DMs

                        for cell in row[1:]:
                            if "(" in cell and ")" in cell:
                                user_id = cell.split("(")[-1].split(")")[0]
                                try:
                                    if guild:
                                        member = guild.get_member(int(user_id))
                                        if member:
                                            mentions.append(member.mention)
                                except Exception as e:
                                    print(f"[⚠️] Couldn't resolve user {user_id}: {e}")
                        return mentions

                    mentions_a = get_mentions(self.team_a)
                    mentions_b = get_mentions(self.team_b)

                    await match_channel.send(
                        content=f"{' '.join(mentions_a)} vs {' '.join(mentions_b)}",
                        embed=embed
                    )

                # Clean up message & channel
                try:
                    if interaction.message:
                        await interaction.message.delete()
                    if interaction.channel and isinstance(interaction.channel, discord.TextChannel):
                        if interaction.channel.name.startswith("proposed-match"):
                            await interaction.channel.delete()
                except Exception:
                    pass

            @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.danger, custom_id="league:propose_match_deny")
            async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
                async def safe_send(interaction, content, ephemeral=True):
                        try:
                            if interaction.response.is_done():
                                await interaction.followup.send(content, ephemeral=ephemeral)
                            else:
                                await interaction.response.send_message(content, ephemeral=ephemeral)
                        except discord.NotFound:
                            print("❗ Tried to send to a stale interaction or webhook.")

                if interaction.user.id == int(self.proposer_id):
                    await safe_send(interaction, "❗ Only the opponent captain may respond to this proposal.")
                    return
                await interaction.response.defer(ephemeral=True)

                if getattr(self, "already_responded", False):
                    return
                self.already_responded = True

                for item in self.children:
                    item.disabled = True
                await interaction.message.edit(view=self)


                # ✅ Remove from Proposed Matches
                proposed_rows = self.parent.proposed_sheet.get_all_values()
                for idx, row in enumerate(proposed_rows, start=1):
                    if row and row[0].strip().lower() == self.match_id.strip().lower():
                        self.parent.proposed_sheet.delete_rows(idx)
                        break

                # ✅ Remove from Challenge Matches if it was a challenge
                if self.match_type == "challenge":
                    challenge_rows = self.parent.challenge_sheet.get_all_values()
                    for idx, row in enumerate(challenge_rows[1:], start=2):  # skip header
                        if (
                            row[2] == self.team_a and
                            row[3] == self.team_b and
                            row[5] == self.proposed_date
                        ):
                            self.parent.challenge_sheet.delete_rows(idx)
                            break

                # Delete original message if in channel (safe check)
                if interaction.message:
                    try:
                        await interaction.message.delete()
                    except discord.Forbidden:
                        pass  # no perms, ignore

                # Delete channel if it was a private proposed match channel
                if interaction.channel and isinstance(interaction.channel, discord.TextChannel):
                    if interaction.channel.name.startswith("proposed-match"):
                        await interaction.channel.delete()
            
            async def on_timeout(self):
                # ✅ Remove from Proposed Match sheet by match ID
                proposed_rows = self.parent.proposed_sheet.get_all_values()[1:]
                for idx, row in enumerate(proposed_rows, start=2):
                    if row and row[0].strip().lower() == self.match_id.strip().lower():
                        self.parent.proposed_sheet.delete_rows(idx)
                        break

                # ✅ Remove from Challenge Match sheet if it was a challenge match
                if self.match_type == "challenge":
                    challenge_rows = self.parent.challenge_sheet.get_all_values()[1:]
                    for idx, row in enumerate(challenge_rows, start=2):
                        if row and row[1].strip().lower() == self.match_id.strip().lower():
                            self.parent.challenge_sheet.delete_rows(idx)
                            break

                # ✅ Delete original proposal message if still present
                try:
                    if self.message:
                        await self.message.delete()
                except Exception:
                    pass

                # ✅ Delete fallback private channel if one was created
                try:
                    if self.channel_to_delete:
                        await self.channel_to_delete.delete()
                except Exception:
                    pass

class ConfirmScoreView(discord.ui.View):
    def __init__(self, parent, match, map_scores, proposer, proposer_id, private_channel=None):
        super().__init__(timeout=None)
        self.parent = parent
        self.match = match
        self.map_scores = map_scores
        self.proposer = proposer
        self.proposer_id = proposer_id
        self.private_channel = private_channel
        self.channel_to_delete = private_channel
        self.message = None
        self.already_responded = False

    async def safe_send(self, interaction, content, ephemeral=True):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(content, ephemeral=ephemeral)
        except discord.NotFound:
            pass

    @discord.ui.button(label="✅ Accept Scores", style=discord.ButtonStyle.green, custom_id="propose_score_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id == self.proposer.id:
            await self.safe_send(interaction, "❗ Only the opposing captain may confirm or deny this score.")
            return

        if self.already_responded:
            return
        self.already_responded = True

        await interaction.response.defer(ephemeral=True)
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

        from match import update_team_rating, get_or_create_sheet

        match_id = self.match["match_id"].strip()
        existing = self.parent.proposed_scores_sheet.get_all_values()[1:]
        if not any(row[0].strip() == match_id for row in existing):
            await self.safe_send(interaction, "⚠️ This match has no active score proposal to accept. It may have expired or already been finalized.")
            return

        # === Score Parsing ===
        map_scores = []
        if isinstance(self.map_scores, dict):
            source = self.map_scores.values()
        elif isinstance(self.map_scores, list):
            source = self.map_scores
        else:
            source = []

        for m in source:
            if isinstance(m, dict):
                try:
                    mode = m["gamemode"]
                    score1 = int(m.get("team1_score", 0))
                    score2 = int(m.get("team2_score", 0))
                    map_scores.append((mode, score1, score2))
                except Exception as e:
                    print(f"[⚠️] Failed to parse map score: {m} — {e}")
            else:
                print(f"[❌] Invalid map score object (not dict): {m}")

        total_a = sum(s[1] for s in map_scores)
        total_b = sum(s[2] for s in map_scores)
        maps_won_a = sum(1 for s in map_scores if s[1] > s[2])
        maps_won_b = sum(1 for s in map_scores if s[2] > s[1])

        if len(map_scores) < 3 and maps_won_a == 1 and maps_won_b == 1:
            await self.safe_send(interaction, "❗ A third map is required to break the 1–1 tie. Please resubmit the score proposal including Map 3.")
            return

        if len(map_scores) < 2:
            await self.safe_send(interaction, "❗ Could not parse at least 2 valid map scores. Please re-submit the proposal.")
            return

        if total_a > total_b:
            winner = self.match["team1"]
            loser = self.match["team2"]
        elif total_b > total_a:
            winner = self.match["team2"]
            loser = self.match["team1"]
        else:
            winner = "Tie"
            loser = ""

        if winner != "Tie":
            update_team_rating(self.parent.leaderboard_sheet, winner, True, 25, -25)
            update_team_rating(self.parent.leaderboard_sheet, loser, False, 25, -25)

            # ✅ PLAYER STATS UPDATE
            def update_player_stats(sheet, user_id, username, won):
                values = sheet.get_all_values()
                existing = {row[1]: row for row in values[1:]}  # key = user_id

                if user_id in existing:
                    row = existing[user_id]
                    rating = int(row[2]) if row[2].isdigit() else self.parent.bot.config.get("default_player_rating", 800)
                    wins = int(row[3]) + (1 if won else 0)
                    losses = int(row[4]) + (0 if won else 1)
                    matches = int(row[5]) + 1
                    elo_change = self.parent.bot.config.get("elo_win_points", 25) if won else self.parent.bot.config.get("elo_loss_points", -25)
                    rating += elo_change
                    new_row = [row[0], user_id, str(rating), str(wins), str(losses), str(matches)]
                    sheet.update(f"A{values.index(row)+1}:F{values.index(row)+1}", [new_row])
                else:
                    rating = self.parent.bot.config.get("default_player_rating", 800)
                    elo_change = self.parent.bot.config.get("elo_win_points", 25) if won else self.parent.bot.config.get("elo_loss_points", -25)
                    sheet.append_row([username, user_id, str(rating + elo_change), "1" if won else "0", "0" if won else "1", "1"])


            def credit_team_players(team_name, won):
                row = next((r for r in self.parent.teams_sheet.get_all_values() if r[0] == team_name), [])
                for cell in row[1:]:
                    if "(" in cell and ")" in cell:
                        username = cell.split("(")[0].strip()
                        user_id = cell.split("(")[-1].split(")")[0].strip()
                        update_player_stats(self.parent.bot.player_leaderboard_sheet, user_id, username, won)

            credit_team_players(winner, True)
            credit_team_players(loser, False)

            for sub_key, is_winner in [("sub_a", self.match["team1"] == winner), ("sub_b", self.match["team2"] == winner)]:
                val = self.match.get(sub_key)
                if val and "|" in val:
                    name, uid = val.split("|")
                    update_player_stats(self.parent.bot.player_leaderboard_sheet, uid.strip(), name.strip(), won=is_winner)

        self.parent.scoring_sheet.append_row([
            self.match["match_id"],
            self.match["team1"],
            self.match["team2"],
            map_scores[0][0], map_scores[0][1], map_scores[0][2],
            map_scores[1][0], map_scores[1][1], map_scores[1][2],
            map_scores[2][0] if len(map_scores) > 2 else "",
            map_scores[2][1] if len(map_scores) > 2 else "",
            map_scores[2][2] if len(map_scores) > 2 else "",
            total_a, total_b, maps_won_a, maps_won_b, winner
        ])

        for sheet in [self.parent.proposed_sheet, self.parent.scheduled_sheet, self.parent.proposed_scores_sheet]:
            rows = sheet.get_all_values()[1:]
            for idx, row in enumerate(rows, start=2):
                if row and row[0].strip() == match_id:
                    sheet.delete_rows(idx)
                    break

        weekly_rows = self.parent.weekly_matches_sheet.get_all_values()[1:]
        for idx, row in enumerate(weekly_rows, start=2):
            if len(row) >= 4 and row[3].strip() == match_id:
                self.parent.weekly_matches_sheet.delete_rows(idx)
                break

        match_sheet = get_or_create_sheet(self.parent.spreadsheet, "Matches", [])
        for idx, row in enumerate(match_sheet.get_all_values()[1:], start=2):
            if row[0].strip() == match_id:
                match_sheet.update_cell(idx, 6, "Finished")
                match_sheet.update_cell(idx, 7, winner if winner != "Tie" else "")
                match_sheet.update_cell(idx, 8, loser if winner != "Tie" else "")
                break

        # 📢 Results Embed
        score_channel = self.parent.bot.get_channel(self.parent.bot.config.get("score_channel_id"))
        if score_channel:
            def get_mentions(team_name):
                row = next((r for r in self.parent.teams_sheet.get_all_values() if r[0] == team_name), [])
                mentions = []
                for cell in row[1:]:
                    if "(" in cell and ")" in cell:
                        user_id = cell.split("(")[-1].split(")")[0]
                        mentions.append(f"<@{user_id}>")
                return mentions

            mentions_a = get_mentions(self.match["team1"])
            mentions_b = get_mentions(self.match["team2"])

            try:
                week_sheet = get_or_create_sheet(self.parent.spreadsheet, "LeagueWeek", ["League Week"])
                week_number = week_sheet.get_all_values()[1][0]
            except Exception:
                week_number = "?"

            embed = discord.Embed(
                title="🏆 Final Match Result",
                description=f"**{self.match['team1']}** vs **{self.match['team2']}**",
                color=discord.Color.gold()
            )

            if self.match.get("sub_a"):
                try:
                    name, uid = self.match["sub_a"].split("|")
                    embed.add_field(name=f"🔁 Sub for {self.match['team1']}", value=f"<@{uid.strip()}>", inline=False)
                except:
                    pass
            if self.match.get("sub_b"):
                try:
                    name, uid = self.match["sub_b"].split("|")
                    embed.add_field(name=f"🔁 Sub for {self.match['team2']}", value=f"<@{uid.strip()}>", inline=False)
                except:
                    pass

            embed.add_field(name="📆 Week", value=f"Week {week_number}", inline=False)
            for i, s in self.map_scores.items():
                if isinstance(s, dict):
                    gamemode = s.get("gamemode", "Unknown")
                    t1_score = s.get("team1_score", "?")
                    t2_score = s.get("team2_score", "?")
                    embed.add_field(
                        name=f"Map {i} ({gamemode})",
                        value=f"{self.match['team1']} {t1_score} - {t2_score} {self.match['team2']}",
                        inline=False
                    )
            embed.add_field(name="Winner", value=winner, inline=False)
            await score_channel.send(content=" ".join(mentions_a + mentions_b), embed=embed)

        await self.safe_send(interaction, "✅ Score accepted and finalized.")
        try:
            await self.proposer.send("✅ Your proposed match scores have been accepted and finalized.")
        except:
            pass
        try:
            await interaction.message.delete()
        except:
            pass
        if self.private_channel:
            try:
                await self.private_channel.delete()
            except:
                pass

    @discord.ui.button(label="❌ Deny Scores", style=discord.ButtonStyle.red, custom_id="propose_score_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id == self.proposer.id:
            await self.safe_send(interaction, "❗ Only the opposing captain may confirm or deny this score.")
            return

        if self.already_responded:
            return
        self.already_responded = True

        await self.safe_send(interaction, "❌ Scores denied.")
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

        for idx, row in enumerate(self.parent.proposed_scores_sheet.get_all_values()[1:], start=2):
            if row and row[0].strip() == self.match["match_id"].strip():
                self.parent.proposed_scores_sheet.delete_rows(idx)
                break

        try:
            await self.proposer.send("❌ Your proposed match scores were denied.")
        except:
            pass
        try:
            await interaction.message.delete()
        except:
            pass
        if self.private_channel:
            try:
                await self.private_channel.delete()
            except:
                pass

class LeaguePanel(View):
    def __init__(self, bot, spreadsheet, players_sheet, teams_sheet, matches_sheet, scoring_sheet, leaderboard_sheet, proposed_sheet, proposed_scores_sheet, scheduled_sheet, weekly_matches_sheet, challenge_sheet, send_to_channel, send_notification, DEV_OVERRIDE_IDS):
        super().__init__(timeout=None)
        self.bot = bot
        self.spreadsheet = spreadsheet
        self.players_sheet = players_sheet
        self.teams_sheet = teams_sheet
        self.matches_sheet = matches_sheet
        self.scoring_sheet = scoring_sheet
        self.leaderboard_sheet = leaderboard_sheet
        self.proposed_sheet = proposed_sheet
        self.scheduled_sheet = scheduled_sheet
        self.weekly_matches_sheet = weekly_matches_sheet
        self.send_to_channel = send_to_channel
        self.challenge_sheet = challenge_sheet
        self.proposed_scores_sheet = proposed_scores_sheet
        self.send_notification = send_notification
        self.DEV_OVERRIDE_IDS = DEV_OVERRIDE_IDS

        with open("config.json") as f:
            self.config = json.load(f)

    def player_signed_up(self, user_id):
        user_id = str(user_id).strip()
        for row in self.players_sheet.get_all_values()[1:]:  # Skip header
            if len(row) > 0 and row[0].strip() == user_id:
                return True
        return False

    def team_exists(self, team_name):
        return any(team[0].lower() == team_name.lower() for team in self.teams_sheet.get_all_values())

# -------------------- PLAYER SIGNUP --------------------

    @discord.ui.button(label="✅ Player Signup", style=discord.ButtonStyle.blurple, custom_id="league:player_signup")
    async def player_signup(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        username = interaction.user.display_name

        banned_sheet = get_or_create_sheet(self.spreadsheet, "Banned", ["User ID", "Username", "Reason", "Banned By", "Date"])
        players_sheet = get_or_create_sheet(self.spreadsheet, "Players", ["User ID", "Username", "Role", "Timezone"])

        # ❌ Check if banned
        if any(row[0] == user_id for row in banned_sheet.get_all_values()[1:]):
            await interaction.response.send_message("❗ You are banned from signing up for the league.", ephemeral=True)
            return

        # ❌ Check if already signed up
        for row in players_sheet.get_all_values()[1:]:
            if row[0] == user_id:
                await interaction.response.send_message(
                    f"❗ You are already signed up as a **{row[2]}**. Unsign and resign to switch role.",
                    ephemeral=True
                )
                return

        class SignupView(discord.ui.View):
            def __init__(self, bot, parent):
                super().__init__(timeout=300)
                self.bot = bot
                self.parent = parent
                self.role = None
                self.timezone = None
                self.add_item(self.RoleSelect(self))
                self.add_item(self.TimezoneSelect(self))

            class RoleSelect(discord.ui.Select):
                def __init__(self, view):
                    self.parent_view = view
                    options = [
                        discord.SelectOption(label="Player", value="Player"),
                        discord.SelectOption(label="League Sub", value="League Sub")
                    ]
                    super().__init__(placeholder="Choose your role...", options=options, row=0)

                async def callback(self, interaction: discord.Interaction):
                    self.view.role = self.values[0]
                    await interaction.response.defer()

            class TimezoneSelect(discord.ui.Select):
                def __init__(self, view):
                    self.parent_view = view
                    options = [
                        discord.SelectOption(label="Pacific", value="US/Pacific"),
                        discord.SelectOption(label="Mountain", value="US/Mountain"),
                        discord.SelectOption(label="Central", value="US/Central"),
                        discord.SelectOption(label="Eastern", value="US/Eastern"),
                        discord.SelectOption(label="Atlantic", value="Canada/Atlantic"),
                        discord.SelectOption(label="UK / London", value="Europe/London"),
                        discord.SelectOption(label="Central Europe", value="Europe/Paris"),
                        discord.SelectOption(label="Eastern Europe", value="Europe/Athens")
                    ]
                    super().__init__(placeholder="Choose your timezone...", options=options, row=1)

                async def callback(self, interaction: discord.Interaction):
                    self.view.timezone = self.values[0]
                    await interaction.response.defer()

            @discord.ui.button(label="✅ Submit", style=discord.ButtonStyle.green, row=2)
            async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
                role = self.role
                tz = self.timezone

                if not role or not tz:
                    await interaction.response.send_message("❗ Please select both a role and a timezone.", ephemeral=True)
                    return

                try:
                    players_sheet.append_row([user_id, username, role, tz])
                    leaderboard = self.bot.player_leaderboard_sheet
                    if not any(row[1] == user_id for row in leaderboard.get_all_values()[1:]):
                        default_elo = self.bot.config.get("default_player_rating", 1025)
                        leaderboard.append_row([username, user_id, str(default_elo), "0", "0", "0"])
                except Exception as e:
                    print(f"[❌] Failed to store signup: {e}")
                    await interaction.response.send_message("❗ Signup failed. Try again later.", ephemeral=True)
                    return

                try:
                    guild = interaction.guild
                    if guild:
                        role_obj = guild.get_role(self.bot.config.get("player_role_id") if role == "Player" else self.bot.config.get("league_sub_role_id"))
                        if role_obj:
                            await interaction.user.add_roles(role_obj)
                except discord.Forbidden:
                    print(f"⚠️ Could not assign role to {interaction.user}")

                await self.parent.send_notification(f"📌 {interaction.user.mention} signed up as **{role}**")
                await interaction.response.edit_message(content=f"✅ Signed up as **{role}** in `{tz}` time!", view=None)

        await interaction.response.send_message(
            "Please choose your signup role and timezone:",
            view=SignupView(self.bot, self),
            ephemeral=True
        )

# -------------------- CREATE TEAM --------------------

    @discord.ui.button(label="🏷️ Create Team", style=discord.ButtonStyle.blurple, custom_id="league:create_team")
    async def create_team(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)

        # ✅ Check if user is signed up
        if not self.player_signed_up(user_id):
            await interaction.response.send_message("❗ You must sign up for the league before creating a team.", ephemeral=True)
            return

        # ❌ Check if user has league sub role
        sub_role = interaction.guild.get_role(self.bot.config.get("league_sub_role_id"))
        if sub_role and sub_role in interaction.user.roles:
            await interaction.response.send_message("❗ You cannot create a team as a league sub. Contact a dev if this is incorrect.", ephemeral=True)
            return

        user_display = f"{interaction.user.display_name} ({interaction.user.id})"

        # Check if rosters are locked
        if is_roster_locked(self.bot.config):
            if interaction.response.is_done():
                await interaction.followup.send("🔒 Rosters are locked, you cannot create team at this moment.", ephemeral=True)
            else:
                await interaction.response.send_message("🔒 Rosters are locked, you cannot create team at this moment.", ephemeral=True)
            return

        # Check if user is already a captain or team member
        for row in self.teams_sheet.get_all_values()[1:]:
            members = row[1:7]
            for cell in members:
                if extract_user_id(cell) == user_id:
                    if cell == row[1]:
                        await interaction.response.send_message("❗ You are already a captain. Disband or transfer captain role first.", ephemeral=True)
                    else:
                        await interaction.response.send_message("❗ You are already on a team. Leave your current team first.", ephemeral=True)
                    return

        class TeamNameModal(discord.ui.Modal, title="Create Team"):
            team_name = discord.ui.TextInput(label="Team Name", required=True)

            def __init__(self, bot, parent_view):
                super().__init__()
                self.bot = bot
                self.parent = parent_view

            async def on_submit(self, modal_interaction: discord.Interaction):
                team_name = self.team_name.value.strip()
                existing_teams = [row[0].lower() for row in self.parent.teams_sheet.get_all_values()]
                if team_name.lower() in existing_teams:
                    await modal_interaction.response.send_message("❗ Team already exists.", ephemeral=True)
                    return

                guild = modal_interaction.guild
                team_role = await guild.create_role(name=f"Team {team_name}")
                captain_role = guild.get_role(self.bot.config.get("universal_captain_role_id"))
                if not captain_role:
                    print(f"❗ Captain role ID not found from Config.")

                await modal_interaction.user.add_roles(team_role, captain_role)

                self.parent.teams_sheet.append_row([team_name, f"{modal_interaction.user.display_name} ({modal_interaction.user.id})"] + [""] * 5)

                if modal_interaction.response.is_done():
                    await interaction.followup.send(f"✅ Team **{team_name}** created! Invite players to join your team.", ephemeral=True)
                else:
                    await modal_interaction.response.send_message(
                        f"✅ Team **{team_name}** created! Invite players to join your team.",
                        ephemeral=True
                    )

                await self.parent.send_notification(f"🎉 **Team Created:** `{team_name}` by {modal_interaction.user.mention}")

        await interaction.response.send_modal(TeamNameModal(self.bot, self))

    # -------------------- PROPOSE MATCH --------------------

    @discord.ui.button(label="📅 Propose Match", style=discord.ButtonStyle.green, custom_id="league:propose_match")
    async def propose_match(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        is_captain = False

        for row in self.teams_sheet.get_all_values()[1:]:
            team_name = row[0]
            captain_cell = row[1]
            if f"({user_id})" in captain_cell:
                is_captain = True
                break

        if not is_captain:
            await interaction.response.send_message("❗ Only team captains can propose matches or scores.", ephemeral=True)
            return

        async def create_private_channel(guild, category_id, channel_name, members):
            # Ensure the ID is an int
            category = discord.utils.get(guild.categories, id=int(category_id))
            if not category:
                print(f"❗ Category with ID {category_id} not found.")
                return None

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True)
            }

            for member in members:
                overwrites[member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            return await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites
            )

        # -------------------- VIEWS --------------------

        class ProposeOpponentView(discord.ui.View):
            def __init__(self, parent, user_team, opponents, is_challenge):
                super().__init__(timeout=None)
                self.parent = parent
                self.user_team = user_team
                self.opponents = opponents
                self.is_challenge = is_challenge

                select = discord.ui.Select(
                    placeholder="Select Opponent",
                    options=[discord.SelectOption(label=op, value=op) for op in opponents]
                )
                select.callback = self.opponent_selected
                self.add_item(select)

            async def opponent_selected(self, interaction: discord.Interaction):
                try:
                    selected_opponent = interaction.data['values'][0]
                    view = CompactDateTimeView(self.parent, self.user_team, selected_opponent, self.is_challenge)

                    if interaction.response.is_done():
                        msg = await interaction.followup.edit_message(
                            message_id=interaction.message.id,
                            content=view.update_display_text(),
                            view=view
                        )
                    else:
                        await interaction.response.edit_message(
                            content=view.update_display_text(),
                            view=view
                        )
                        view.message = await interaction.original_response()

                except Exception as e:
                    print(f"[❌] opponent_selected failed: {e}")
                    if interaction.response.is_done():
                        await interaction.followup.send("❗ Failed to load date selector.", ephemeral=True)
                    else:
                        await interaction.response.send_message("❗ Failed to load date selector.", ephemeral=True)


        class CompactDateTimeView(discord.ui.View):
            def __init__(self, parent, team_a, team_b, is_challenge):
                super().__init__(timeout=None)
                self.parent = parent
                self.team_a = team_a
                self.team_b = team_b
                self.is_challenge = is_challenge
                self.date_time = {
                    "month": None,
                    "day": None,
                    "hour": None,
                    "minute": None,
                    "am_pm": None
                }
                self.message = None  # To store the message we're editing
                self.submit_button = self.SubmitButton(self)
                self.add_item(self.submit_button)

                self.add_item(self.MonthDropdown(self))
                self.add_item(self.DayDropdown1(self))
                self.add_item(self.DayDropdown2(self))
                self.add_item(self.TimeDropdown(self))
                self.add_item(self.AMButton(self))
                self.add_item(self.PMButton(self))

            def update_display_text(self):
                month = self.date_time.get("month")
                day = self.date_time.get("day")
                hour = self.date_time.get("hour")
                minute = self.date_time.get("minute")
                am_pm = self.date_time.get("am_pm")

                output = "**Match Proposal Builder**\n"
                output += "*You're selecting a proposed date and time for the match.*\n\n"
                output += "- Choose your local time.\n\n\n"
                output += "__**Selected Values:**__\n"

                if month:
                    output += f"📅 Month: `{month}`\n"
                else:
                    output += "📅 Month: ❌ *Not selected*\n"

                if day:
                    output += f"📆 Day: `{day}` (You must pick from one of the day dropdowns)\n"
                else:
                    output += "📆 Day: ❌ *Not selected*\n"

                if hour and minute:
                    output += f"⏰ Time: `{hour}:{minute}`\n"
                else:
                    output += "⏰ Time: ❌ *Not selected*\n"

                if am_pm:
                    output += f"🌓 AM/PM: `{am_pm}`\n"
                else:
                    output += "🌓 AM/PM: ❌ *Not selected*\n"

                if all([month, day, hour, minute, am_pm]):
                    output += "\n✅ All fields selected. Ready to submit!"
                else:
                    output += "\n⛔ Incomplete — select all required fields."

                return output

            async def refresh_message(self):
                # Toggle submit button
                if self.submit_button:
                    is_ready = all(self.date_time.values())
                    self.submit_button.disabled = not is_ready

                # Update the message
                if self.message:
                    await self.message.edit(content=self.update_display_text(), view=self)

            class MonthDropdown(discord.ui.Select):
                def __init__(self, parent_view):
                    options = [discord.SelectOption(label=str(m), value=str(m)) for m in range(1, 13)]
                    super().__init__(placeholder="Select Month", options=options, row=0)
                    self.parent_view = parent_view

                async def callback(self, interaction: discord.Interaction):
                    self.parent_view.date_time["month"] = self.values[0]
                    await interaction.response.defer()
                    await self.parent_view.refresh_message()

            class DayDropdown1(discord.ui.Select):
                def __init__(self, parent_view):
                    options = [discord.SelectOption(label=str(d), value=str(d)) for d in range(1, 16)]
                    super().__init__(placeholder="Day 1–15", options=options, row=1)
                    self.parent_view = parent_view

                async def callback(self, interaction: discord.Interaction):
                    self.parent_view.date_time["day"] = self.values[0]
                    await interaction.response.defer()
                    await self.parent_view.refresh_message()

            class DayDropdown2(discord.ui.Select):
                def __init__(self, parent_view):
                    options = [discord.SelectOption(label=str(d), value=str(d)) for d in range(16, 32)]
                    super().__init__(placeholder="Day 16–31", options=options, row=2)
                    self.parent_view = parent_view

                async def callback(self, interaction: discord.Interaction):
                    self.parent_view.date_time["day"] = self.values[0]
                    await interaction.response.defer()
                    await self.parent_view.refresh_message()

            class TimeDropdown(discord.ui.Select):
                def __init__(self, parent_view):
                    self.parent_view = parent_view
                    options = []
                    for h in range(1, 13):
                        options.append(discord.SelectOption(label=f"{h}:00", value=f"{h}:00"))
                        options.append(discord.SelectOption(label=f"{h}:30", value=f"{h}:30"))
                    super().__init__(placeholder="Select Time (30-min blocks)", options=options, row=3)

                async def callback(self, interaction: discord.Interaction):
                    selected = self.values[0]
                    hour, minute = selected.split(":")
                    self.parent_view.date_time["hour"] = hour
                    self.parent_view.date_time["minute"] = minute
                    await interaction.response.defer()
                    await self.parent_view.refresh_message()

            class AMButton(discord.ui.Button):
                def __init__(self, parent_view):
                    super().__init__(label="☀️ AM", style=discord.ButtonStyle.primary, row=4)
                    self.parent_view = parent_view

                async def callback(self, interaction: discord.Interaction):
                    self.parent_view.date_time["am_pm"] = "AM"
                    await interaction.response.defer()
                    await self.parent_view.refresh_message()

            class PMButton(discord.ui.Button):
                def __init__(self, parent_view):
                    super().__init__(label="🌙 PM", style=discord.ButtonStyle.primary, row=4)
                    self.parent_view = parent_view

                async def callback(self, interaction: discord.Interaction):
                    self.parent_view.date_time["am_pm"] = "PM"
                    await interaction.response.defer()
                    await self.parent_view.refresh_message()

            class SubmitButton(discord.ui.Button):
                def __init__(self, parent_view):
                    super().__init__(label="✅ Submit Proposal", style=discord.ButtonStyle.green, row=4)
                    self.parent_view = parent_view

                async def callback(self, interaction: discord.Interaction):
                    await interaction.response.defer(ephemeral=True)
                    if not all(self.parent_view.date_time.values()):
                        await safe_send(interaction, "❗ Please complete all selections first.", ephemeral=True)
                        return

                    from datetime import datetime

                    try:
                        # Build datetime
                        month = int(self.parent_view.date_time["month"])
                        day = int(self.parent_view.date_time["day"])
                        hour = int(self.parent_view.date_time["hour"])
                        minute = int(self.parent_view.date_time["minute"])
                        am_pm = self.parent_view.date_time["am_pm"]

                        if am_pm.upper() == "PM" and hour != 12:
                            hour += 12
                        elif am_pm.upper() == "AM" and hour == 12:
                            hour = 0

                        year = datetime.utcnow().year
                        naive_dt = datetime(year, month, day, hour, minute)

                        # 🌍 Resolve user's timezone
                        user_tz_name = None
                        for row in self.parent_view.parent.players_sheet.get_all_values()[1:]:
                            if row[0].strip() == str(interaction.user.id) and len(row) > 3:
                                user_tz_name = row[3]
                                break

                        if user_tz_name in pytz.all_timezones:
                            tz = pytz.timezone(user_tz_name)
                            localized_dt = tz.localize(naive_dt)
                            proposed_datetime = localized_dt.astimezone(pytz.utc)
                        else:
                            print(f"[⚠️] Missing or invalid timezone for user {interaction.user.id}")
                            proposed_datetime = pytz.utc.localize(naive_dt)

                        # 🕓 Format display and enforce validation
                        discord_ts = int(proposed_datetime.timestamp())
                        proposed_date = f"<t:{discord_ts}:f>"

                        season_start = datetime.fromisoformat(self.parent_view.parent.config.get("season_start")).replace(tzinfo=timezone.utc)
                        season_end = datetime.fromisoformat(self.parent_view.parent.config.get("season_end")).replace(tzinfo=timezone.utc)
                        now = datetime.now(tz=timezone.utc)

                        if proposed_datetime < now:
                            await interaction.followup.send("❗ You can't propose a match time in the past.", ephemeral=True)
                            return

                        if proposed_datetime < season_start:
                            await interaction.followup.send(
                                f"❗ The season hasn't started yet. First match must be on or after {season_start.strftime('%B %d, %Y')}.",
                                ephemeral=True
                            )
                            return

                        if proposed_datetime > season_end:
                            await interaction.followup.send(
                                f"❗ That date is beyond the season end. Last match must be on or before {season_end.strftime('%B %d, %Y')}.",
                                ephemeral=True
                            )
                            return

                    except Exception as e:
                        print(f"[❌] Failed to build or validate datetime: {e}")
                        await interaction.followup.send("❗ Invalid time selection or season configuration.", ephemeral=True)
                        return

                    # Check duplicates
                    try:
                        existing = self.parent_view.parent.proposed_sheet.get_all_values()[1:]
                        for row in existing:
                            if (row[0] == self.parent_view.team_a and row[1] == self.parent_view.team_b) or \
                            (row[0] == self.parent_view.team_b and row[1] == self.parent_view.team_a):
                                await interaction.followup.send("❗ A match proposal between these teams already exists.", ephemeral=True)
                                return
                    except Exception as e:
                        print(f"[❌] Failed checking for duplicates: {e}")

                    # Match ID
                    try:
                        league_week_sheet = get_or_create_sheet(self.parent_view.parent.spreadsheet, "LeagueWeek", ["League Week"])
                        week_number = int(league_week_sheet.get_all_values()[1][0])
                        if self.parent_view.is_challenge:
                            match_id = f"Challenge{week_number}-{self.parent_view.team_a[:3]}-{self.parent_view.team_b[:3]}"
                        else:
                            match_id = f"Week{week_number}-{self.parent_view.team_a[:3]}-{self.parent_view.team_b[:3]}"
                    except Exception as e:
                        print(f"[❗] Match ID fallback: {e}")
                        match_id = f"Match-{self.parent_view.team_a[:3]}-{self.parent_view.team_b[:3]}"

                    # Find captain
                    guild = interaction.guild
                    captain = None
                    for row in self.parent_view.parent.teams_sheet.get_all_values()[1:]:
                        if row[0].lower() == self.parent_view.team_b.lower():
                            try:
                                id_str = row[1].split("(")[-1].replace(")", "").strip()
                                captain = await guild.fetch_member(int(id_str))
                            except Exception as e:
                                print(f"[❗] Failed fetching captain: {e}")
                            break

                    if not captain:
                        await interaction.followup.send(f"❗ Could not find the captain of **{self.parent_view.team_b}**.", ephemeral=True)
                        return

                    # Create fallback channel
                    try:
                        fallback_id = int(self.parent_view.parent.config.get("fallback_category_id"))
                        private_channel = await create_private_channel(
                            guild,
                            fallback_id,
                            f"proposed-match-{self.parent_view.team_a}-vs-{self.parent_view.team_b}",
                            [interaction.user, captain]
                        )
                    except Exception as e:
                        print(f"[❌] Failed to create fallback channel: {e}")
                        private_channel = None

                    if not private_channel:
                        await interaction.followup.send("❗ Failed to create fallback channel.", ephemeral=True)
                        return

                    try:
                        view = AcceptDenyMatchView(
                            self.parent_view.parent,
                            self.parent_view.team_a,
                            self.parent_view.team_b,
                            proposed_date,
                            match_id=match_id,
                            match_type="challenge" if self.parent_view.is_challenge else "assigned",
                            week_number=week_number if not self.parent_view.is_challenge else None,
                            proposed_datetime=proposed_datetime,
                            proposer_id=interaction.user.id
                        )
                        msg = await private_channel.send(
                            f"{captain.mention} 📨 Proposed Match from **{self.parent_view.team_a}** on {proposed_date}. Accept?",
                            view=view
                        )
                        view.message = msg
                        view.channel_to_delete = private_channel
                        self.parent_view.parent.bot.add_view(view, message_id=msg.id)

                        # Log
                        self.parent_view.parent.proposed_sheet.append_row([
                            match_id,
                            self.parent_view.team_a,
                            self.parent_view.team_b,
                            str(interaction.user.id),
                            proposed_date,
                            str(private_channel.id),
                            str(msg.id)
                        ])
                        if self.parent_view.is_challenge:
                            self.parent_view.parent.challenge_sheet.append_row([
                                week_number,
                                match_id,
                                self.parent_view.team_a,
                                self.parent_view.team_b,
                                str(interaction.user.id),
                                proposed_date,
                                "",
                                "Pending"
                            ])

                        confirm = (
                            f"✅ Proposed match submitted:\n"
                            f"**{self.parent_view.team_a}** vs **{self.parent_view.team_b}**\n"
                            f"🕓 Scheduled for {proposed_date}"
                        )
                        try:
                            if not interaction.response.is_done():
                                await interaction.response.send_message(confirm, ephemeral=True)
                            else:
                                await interaction.followup.send(confirm, ephemeral=True)
                        except discord.NotFound:
                            print("❗ Interaction expired — could not send final confirmation.")

                    except Exception as e:
                        print(f"[❌] Failed to finalize proposal: {e}")
                        await interaction.followup.send("❗ Failed to deliver proposal.", ephemeral=True)

        class ChallengeSearchModal(discord.ui.Modal, title="Search Challenge Opponent"):
            query = discord.ui.TextInput(label="Enter Team Name", required=True)

            def __init__(self, parent, user_team):
                super().__init__()
                self.parent = parent
                self.user_team = user_team

            async def on_submit(self, interaction: discord.Interaction):
                await interaction.response.defer(ephemeral=True)
                search = self.query.value.lower()
                valid_teams = []

                # ✅ Challenge match weekly limit check
                from datetime import datetime
                challenge_sheet = get_or_create_sheet(
                    self.parent.spreadsheet,
                    "Challenge Matches",
                    ["Week", "Team A", "Team B", "Proposer ID", "Proposed Date", "Completion Date"]
                )

                league_week_sheet = get_or_create_sheet(self.parent.spreadsheet, "LeagueWeek", ["League Week"])
                current_week = int(league_week_sheet.get_all_values()[1][0])
                weekly_limit = self.parent.config.get("weekly_challenge_limit", 2)

                team_challenges = [
                    row for row in challenge_sheet.get_all_values()[1:]
                    if str(row[0]) == str(current_week) and self.user_team in (row[1], row[2])
                ]

                if len(team_challenges) >= weekly_limit:
                    await interaction.followup.send(
                        f"❗ Your team already has {weekly_limit} challenge match(es) this week.",
                        ephemeral=True
                    )
                    return

                for row in self.parent.teams_sheet.get_all_values()[1:]:
                    team_name = row[0]
                    players = [p for p in row[1:] if p.strip()]
                    if team_name.lower() != self.user_team.lower() and len(players) >= self.parent.config.get("team_min_players", 3):
                        if search in team_name.lower():
                            valid_teams.append(team_name)

                if not valid_teams:
                    await interaction.followup.send("❗ No valid teams found.", ephemeral=True)
                    return

                await interaction.followup.send(
                    "Select opponent:",
                    view=ProposeOpponentView(self.parent, self.user_team, valid_teams, is_challenge=True),
                    ephemeral=True
                )

        class SelectTypeView(discord.ui.View):
            def __init__(self, parent, user_team, assigned_opponents):
                super().__init__(timeout=None)
                self.parent = parent
                self.user_team = user_team
                self.assigned_opponents = assigned_opponents

                select = discord.ui.Select(placeholder="Select Match Type", options=[
                    discord.SelectOption(label="Assigned Opponent", value="assigned"),
                    discord.SelectOption(label="Challenge Match", value="challenge")
                ])
                select.callback = self.selected_type
                self.add_item(select)

            async def selected_type(self, interaction: discord.Interaction):
                selected = interaction.data['values'][0]

                if selected == "assigned":
                    if not self.assigned_opponents:
                        await interaction.response.send_message("❗ You have no assigned opponents.", ephemeral=True)
                        return
                    await interaction.response.send_message("Select opponent:", view=ProposeOpponentView(self.parent, self.user_team, self.assigned_opponents, is_challenge=False), ephemeral=True)
                else:
                    await interaction.response.send_modal(ChallengeSearchModal(self.parent, self.user_team))

    # -------------------- MAIN propose_match logic --------------------

        user_id = str(interaction.user.id)
        user_team = None

        for row in self.teams_sheet.get_all_values()[1:]:
            player_ids = [str(extract_user_id(p)).strip() for p in row[1:] if p]
            if user_id in player_ids:
                user_team = row[0]
                break

        if not user_team:
            await interaction.response.send_message("❗ You are not on a team.", ephemeral=True)
            return


        weekly_matches = get_or_create_sheet(self.bot.spreadsheet, "Weekly Matches", ["Week", "Team A", "Team B", "Match ID", "Scheduled Date"])
        assigned_opponents = []
        for row in weekly_matches.get_all_values()[1:]:
            if row[1] == user_team:
                assigned_opponents.append(row[2])
            elif row[2] == user_team:
                assigned_opponents.append(row[1])

        view = SelectTypeView(self, user_team, assigned_opponents)
        await interaction.response.send_message("Select match type:", view=view, ephemeral=True)

    # -------------------- PROPOSE SCORE--------------------

    @discord.ui.button(label="🏆 Propose Score", style=discord.ButtonStyle.blurple, custom_id="propose_score")
    async def propose_score(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        is_captain = False

        for row in self.teams_sheet.get_all_values()[1:]:
            team_name = row[0]
            captain_cell = row[1]
            if f"({user_id})" in captain_cell:
                is_captain = True
                break

        if not is_captain:
            await interaction.response.send_message("❗ Only team captains can propose matches or scores.", ephemeral=True)
            return

        async def create_private_channel(guild, category_id, channel_name, members):
            category = guild.get_channel(int(category_id))
            if not isinstance(category, discord.CategoryChannel):
                print("❌ Invalid category object passed.")
                return None

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True)
            }

            for member in members:
                if isinstance(member, (discord.Member, discord.User)):
                    overwrites[member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
                else:
                    print(f"⚠️ Skipping invalid member object in channel creation: {member} (type: {type(member)})")

            return await guild.create_text_channel(name=channel_name, category=category, overwrites=overwrites)

        
        class MapScoreModal(discord.ui.Modal, title="Enter Map Score"):
            def __init__(self, parent_view, match, map_scores, map_number, gamemode):
                super().__init__()
                self.parent_view = parent_view  
                self.match = match
                self.map_scores = map_scores
                self.map_number = map_number
                self.gamemode = gamemode

                self.team1_score = discord.ui.TextInput(
                    label=f"{match['team1']} Rounds Won", required=True)
                self.team2_score = discord.ui.TextInput(
                    label=f"{match['team2']} Rounds Won", required=True)
                self.add_item(self.team1_score)
                self.add_item(self.team2_score)

            async def on_submit(self, interaction: discord.Interaction):
                self.map_scores[self.map_number] = {
                    "gamemode": self.gamemode,
                    "team1_score": self.team1_score.value,
                    "team2_score": self.team2_score.value
                }

                await interaction.response.defer()
                await self.parent_view.refresh()  # refresh original message

        class MapGamemodeSelectView(discord.ui.View):
            def __init__(self, parent_view, match, map_scores, map_number):
                super().__init__(timeout=None)
                self.parent_view = parent_view  # the original MapScoreView
                self.match = match
                self.map_scores = map_scores
                self.map_number = map_number

                select = discord.ui.Select(placeholder="Select Gamemode", options=[
                    discord.SelectOption(label="Payload", value="Payload"),
                    discord.SelectOption(label="Capture Point", value="Capture Point")
                ])
                select.callback = self.gamemode_selected
                self.add_item(select)

            async def gamemode_selected(self, interaction: discord.Interaction):
                gamemode = interaction.data['values'][0]
                await interaction.response.send_modal(
                    MapScoreModal(self.parent_view, self.match, self.map_scores, self.map_number, gamemode)
                )

        class MapScoreView(discord.ui.View):
            def __init__(self, bot, parent, match):
                super().__init__(timeout=None)
                self.bot = bot
                self.parent = parent
                self.match = match
                self.map_scores = {}
                self.message = None
                self.current_map = 1
                self.show_map(self.current_map)

            def show_map(self, map_num):
                self.clear_items()
                self.current_map = map_num

                self.add_item(self.GamemodeDropdown(self, map_num))

                gamemode = self.map_scores.get(map_num, {}).get("gamemode")
                if gamemode:
                    self.add_item(self.ScoreDropdown(self, map_num, "team1"))
                    self.add_item(self.ScoreDropdown(self, map_num, "team2"))

                self.add_item(self.BackButton(self))
                self.add_item(self.NextButton(self))

            def status_text(self):
                lines = [f"**🏆 Proposed Score for {self.match['team1']} vs {self.match['team2']}**"]
                for i in range(1, 4):
                    s = self.map_scores.get(i)
                    if not s:
                        break
                    gamemode = s.get("gamemode", "❓")
                    t1 = s.get("team1_score")
                    t2 = s.get("team2_score")
                    if t1 is not None and t2 is not None:
                        lines.append(f"✅ Map {i}: **{gamemode}** — {self.match['team1']} {t1} - {t2} {self.match['team2']}")
                    else:
                        lines.append(f"❌ Map {i}: **{gamemode}** — Score incomplete")

                # ➕ Add subs info if available
                if self.match.get("sub_a"):
                    name, uid = self.match["sub_a"].split("|")
                    lines.append(f"🔁 **Sub for {self.match['team1']}**: <@{uid.strip()}>")
                if self.match.get("sub_b"):
                    name, uid = self.match["sub_b"].split("|")
                    lines.append(f"🔁 **Sub for {self.match['team2']}**: <@{uid.strip()}>")

                return "\n".join(lines)

            async def refresh(self):
                if self.message:
                    await self.message.edit(content=self.status_text(), view=self)
            
            class GamemodeDropdown(discord.ui.Select):
                def __init__(self, view, map_num):
                    self.view_obj = view
                    self.map_num = map_num
                    super().__init__(
                        placeholder=f"Map {map_num} Gamemode",
                        options=[
                            discord.SelectOption(label="Payload", value="Payload"),
                            discord.SelectOption(label="Capture Point", value="Capture Point")
                        ],
                        custom_id=f"gm_{map_num}"
                    )

                async def callback(self, interaction: discord.Interaction):
                    gamemode = self.values[0]
                    self.view_obj.map_scores.setdefault(self.map_num, {})["gamemode"] = gamemode

                    # ❌ Remove existing ScoreDropdowns for this map
                    to_remove = [
                        item for item in self.view_obj.children
                        if isinstance(item, MapScoreView.ScoreDropdown) and item.map_num == self.map_num
                    ]
                    for item in to_remove:
                        self.view_obj.remove_item(item)

                    # ✅ Add updated ScoreDropdowns with correct limits
                    self.view_obj.add_item(MapScoreView.ScoreDropdown(self.view_obj, self.map_num, "team1"))
                    self.view_obj.add_item(MapScoreView.ScoreDropdown(self.view_obj, self.map_num, "team2"))

                    # ✅ Update the message *after* changing children
                    try:
                        if interaction.response.is_done():
                            await interaction.followup.edit_message(
                                message_id=interaction.message.id,
                                content=self.view_obj.status_text(),
                                view=self.view_obj
                            )
                        else:
                            await interaction.response.edit_message(
                                content=self.view_obj.status_text(),
                                view=self.view_obj
                            )
                    except discord.NotFound:
                        print("❗ GamemodeDropdown failed to update message — probably expired.")

            class ScoreDropdown(discord.ui.Select):
                def __init__(self, view_obj, map_num, team, row=None):
                    self.view_obj = view_obj
                    self.map_num = map_num
                    self.team = team
                    team_name = view_obj.match["team1"] if team == "team1" else view_obj.match["team2"]

                    gamemode = view_obj.map_scores.get(map_num, {}).get("gamemode")
                    if not gamemode:
                        raise ValueError(f"Gamemode not set for Map {map_num}")

                    limit = 2 if gamemode == "Capture Point" else 10

                    super().__init__(
                        placeholder=f"{team_name} Rounds Won (Map {map_num}) — Max {limit}",
                        options=[discord.SelectOption(label=str(i), value=str(i)) for i in range(0, limit + 1)],
                        custom_id=f"score_{map_num}_{team}",
                        row=row
                    )

                async def callback(self, interaction: discord.Interaction):
                    gamemode = self.view_obj.map_scores.get(self.map_num, {}).get("gamemode")
                    if not gamemode:
                        # Edit existing message with an error inline
                        if self.view_obj.message:
                            await self.view_obj.message.edit(
                                content=self.view_obj.status_text() + "\n❗ **Please select the gamemode before entering scores.**",
                                view=self.view_obj
                            )
                        else:
                            if not interaction.response.is_done():
                                await interaction.response.send_message("❗ Please select the gamemode before entering scores.", ephemeral=True)
                            else:
                                await interaction.followup.send("❗ Please select the gamemode before entering scores.", ephemeral=True)
                        return

                    await interaction.response.defer()
                    try:
                        self.view_obj.map_scores.setdefault(self.map_num, {})[f"{self.team}_score"] = int(self.values[0])
                    except Exception as e:
                        print(f"[❌] Failed to store score dropdown value: {self.values[0]} — {e}")

                    await self.view_obj.refresh()

            class NextButton(discord.ui.Button):
                def __init__(self, view):
                    super().__init__(label="➡️ Next Map", style=discord.ButtonStyle.primary, row=4)
                    self.view_obj = view

                async def callback(self, interaction: discord.Interaction):
                    current = self.view_obj.current_map

                    # 🛑 If current map is incomplete, don’t proceed
                    if not all(k in self.view_obj.map_scores.get(current, {}) for k in ["gamemode", "team1_score", "team2_score"]):
                        await interaction.response.edit_message(
                            content="❗ Please complete all fields for this map before continuing.",
                            view=self.view_obj
                        )
                        return

                    # ✅ If Map 1 or Map 2: check if we need a 3rd map or finalize
                    if current == 2:
                        a_wins = sum(int(s.get("team1_score", 0)) > int(s.get("team2_score", 0)) for s in self.view_obj.map_scores.values())
                        b_wins = sum(int(s.get("team2_score", 0)) > int(s.get("team1_score", 0)) for s in self.view_obj.map_scores.values())

                        if a_wins == 1 and b_wins == 1:
                            # Go to Map 3
                            self.view_obj.current_map = 3
                            self.view_obj.show_map(3)
                            await interaction.response.edit_message(
                                content=self.view_obj.status_text(),
                                view=self.view_obj
                            )
                            return
                        else:
                            # 2–0 situation → final confirmation
                            await interaction.response.edit_message(
                                content=self.view_obj.status_text() + "\n\n__**Note:**__ Use the dropdowns below to add league subs if any were used. "
                            "Leave as None if no subs played.\n\nPlease confirm and submit your score proposal:",
                                view=ConfirmProposalView(self.view_obj)
                            )
                            return

                    if current == 3:
                        # All 3 maps completed → final confirmation
                        await interaction.response.edit_message(
                            content=self.view_obj.status_text() + "\n\n__**Note:**__ Use the dropdowns below to add league subs if any were used. "
                            "Leave as None if no subs played.\n\nPlease confirm and submit your score proposal:",
                            view=ConfirmProposalView(self.view_obj)
                        )
                        return

                    # Default: proceed to next map
                    self.view_obj.current_map += 1
                    self.view_obj.show_map(self.view_obj.current_map)
                    await interaction.response.edit_message(
                        content=self.view_obj.status_text(),
                        view=self.view_obj
                    )

            class BackButton(discord.ui.Button):
                def __init__(self, view):
                    super().__init__(label="⬅️ Back", style=discord.ButtonStyle.secondary, row=4)
                    self.view_obj = view

                async def callback(self, interaction: discord.Interaction):
                    if self.view_obj.current_map > 1:
                        self.view_obj.current_map -= 1
                        self.view_obj.show_map(self.view_obj.current_map)
                        await interaction.response.edit_message(content=self.view_obj.status_text(), view=self.view_obj)

            async def submit(self, interaction: discord.Interaction):
                if self.message:
                    await self.message.edit(content="✅ Scores submitted. Processing...", view=None)

                team1 = self.match["team1"]
                team2 = self.match["team2"]
                guild = interaction.guild

                team_user_is_on = None
                opponent_team = None
                for row in self.parent.teams_sheet.get_all_values()[1:]:
                    if f"({interaction.user.id})" in row[1]:
                        team_user_is_on = row[0]
                        break

                if not team_user_is_on:
                    await interaction.followup.send("❗ You must be a team captain to submit scores.", ephemeral=True)
                    return

                opponent_team = team2 if team_user_is_on == team1 else team1
                opponent_captain = None
                for row in self.parent.teams_sheet.get_all_values()[1:]:
                    if row[0] == opponent_team:
                        try:
                            user_id = int(row[1].split("(")[-1].replace(")", "").strip())
                            opponent_captain = await guild.fetch_member(user_id)
                        except:
                            opponent_captain = None
                        break

                if not opponent_captain:
                    await interaction.followup.send(f"❗ Could not find the captain of **{opponent_team}**.", ephemeral=True)
                    return

                embed = discord.Embed(
                    title="Proposed Match Scores",
                    description=f"**{team1}** vs **{team2}**"
                )
                for i, s in self.map_scores.items():
                    if isinstance(s, dict):
                        gamemode = s.get("gamemode", "Unknown")
                        t1_score = s.get("team1_score", "?")
                        t2_score = s.get("team2_score", "?")

                        embed.add_field(
                            name=f"Map {i} ({gamemode})",
                            value=f"{self.match['team1']} {t1_score} - {t2_score} {self.match['team2']}",
                            inline=False
                        )
                    else:
                        print(f"[❌] map_scores[{i}] is not a dict: {s}")

                if self.match.get("sub_a"):
                    name, uid = self.match["sub_a"].split("|")
                    embed.add_field(name=f"🔁 Sub for {team1}", value=f"<@{uid.strip()}>", inline=False)
                if self.match.get("sub_b"):
                    name, uid = self.match["sub_b"].split("|")
                    embed.add_field(name=f"🔁 Sub for {team2}", value=f"<@{uid.strip()}>", inline=False)

                category_id = self.parent.config.get("fallback_category_id")
                private_channel = await create_private_channel(
                    guild,
                    int(category_id),
                    f"proposed-score-{team1}-vs-{team2}",
                    [interaction.user, opponent_captain]
                )
                if not private_channel:
                    await interaction.followup.send("❗ Failed to create confirmation channel.", ephemeral=True)
                    return

                view = ConfirmScoreView(
                    self.parent,
                    self.match,
                    dict(self.map_scores),
                    interaction.user,
                    str(interaction.user.id),
                    private_channel
                )

                msg = await private_channel.send(
                    f"{opponent_captain.mention} 📨 Proposed Match Scores from **{team1}**.",
                    embed=embed,
                    view=view
                )

                try:
                    sheet = self.parent.proposed_scores_sheet
                    match_id = self.match["match_id"]
                    new_row = [
                        match_id,
                        team1,
                        team2,
                        str(interaction.user.id),
                        self.match.get("date", f"<t:{int(datetime.utcnow().timestamp())}:f>"),
                        str(private_channel.id),
                        str(msg.id)
                    ]
                    rows = sheet.get_all_values()
                    row_index = next((i + 2 for i, row in enumerate(rows[1:]) if row[0] == match_id), None)
                    if row_index:
                        sheet.update(f"A{row_index}:G{row_index}", [new_row])
                    else:
                        sheet.append_row(new_row)
                except Exception as e:
                    print(f"❌ Failed to write to Proposed Scores: {e}")

                view.message = msg
                self.parent.bot.add_view(view, message_id=msg.id)

                try:
                    await interaction.message.edit(content="✅ Score submitted for confirmation in a private channel.", view=None)
                except discord.NotFound:
                    pass  
        
        class ConfirmProposalView(discord.ui.View):
            def __init__(self, map_view):
                super().__init__(timeout=300)
                self.map_view = map_view

                team1 = self.map_view.match["team1"]
                team2 = self.map_view.match["team2"]
                self.add_item(self.SubSelectDropdown(self, team1, "sub_a"))
                self.add_item(self.SubSelectDropdown(self, team2, "sub_b"))
                self.add_item(self.SubmitButton(self))
                self.add_item(self.BackButton(self))

            class BackButton(discord.ui.Button):
                def __init__(self, parent):
                    super().__init__(label="↩️ Go Back & Edit", style=discord.ButtonStyle.secondary)
                    self.parent_view = parent

                async def callback(self, interaction: discord.Interaction):
                    await interaction.response.edit_message(
                        content=self.parent_view.map_view.status_text(),
                        view=self.parent_view.map_view
                    )

            class SubSelectDropdown(discord.ui.Select):
                def __init__(self, parent_view, team_name, match_key):
                    self.parent_view = parent_view
                    self.team_name = team_name
                    self.match_key = match_key

                    # Load eligible subs
                    subs = []
                    try:
                        team_rating = 0
                        for row in parent_view.map_view.parent.leaderboard_sheet.get_all_values()[1:]:
                            if row[0].strip().lower() == team_name.strip().lower():
                                team_rating = float(row[1])
                                break

                        players = parent_view.map_view.parent.players_sheet.get_all_values()[1:]
                        for row in players:
                            if len(row) >= 3 and row[2].strip().lower() == "league sub":
                                try:
                                    uid = row[0]
                                    for prow in parent_view.map_view.parent.bot.player_leaderboard_sheet.get_all_values()[1:]:
                                        if len(prow) >= 3 and prow[1] == uid:
                                            try:
                                                rating = float(prow[2])
                                                if rating <= team_rating:
                                                    subs.append((row[1], uid, rating))  # name from Players sheet, id, rating
                                            except Exception as e:
                                                print(f"⚠️ Failed to parse rating for UID {uid}: {e}")
                                except Exception as e:
                                    print(f"⚠️ Failed to process player row {row}: {e}")
                    except Exception as e:
                        print(f"[SubSelectDropdown] Failed to get subs: {e}")

                    options = [
                        discord.SelectOption(label=f"{name} - {int(rating)}", value=f"{name}|{uid}")
                        for name, uid, rating in sorted(subs, key=lambda x: x[2], reverse=True)[:24]
                    ]
                    options.insert(0, discord.SelectOption(label="None", value="None"))

                    super().__init__(
                        placeholder=f"Sub for {team_name} (optional)",
                        min_values=1,
                        max_values=1,
                        options=options
                    )

                async def callback(self, interaction: discord.Interaction):
                    val = self.values[0]
                    self.parent_view.map_view.match[self.match_key] = val if val != "None" else None
                    await interaction.response.edit_message(
                        content=self.view.map_view.status_text() +
                                 "\n\n__**Note:**__ Use the dropdowns below to add league subs if any were used. "
                            "Leave as None if no subs played.\n\nPlease confirm and submit your score proposal:",
                        view=self.view
                    )
            
            class SubmitButton(discord.ui.Button):
                def __init__(self, parent):
                    super().__init__(label="✅ Submit to Opponent", style=discord.ButtonStyle.green)
                    self.parent_view = parent

                async def callback(self, interaction: discord.Interaction):
                    await self.parent_view.map_view.submit(interaction)

        class MatchSelectView(discord.ui.View):
            def __init__(self, parent, matches):
                super().__init__(timeout=None)
                self.parent = parent
                self.matches = matches

                options = []
                for i, m in enumerate(matches):
                    raw_date = m.get("date", "")
                    readable = raw_date  # default fallback

                    # Try to parse <t:TIMESTAMP:f>
                    if raw_date.startswith("<t:") and raw_date.endswith(":f>"):
                        try:
                            timestamp = int(raw_date[3:-3])
                            dt = datetime.utcfromtimestamp(timestamp)
                            readable = dt.strftime("%b %d, %Y @ %I:%M %p UTC")
                        except Exception:
                            pass  # fallback to raw_date if parsing fails

                    label = f"{m['team1']} vs {m['team2']} on {readable}"
                    options.append(discord.SelectOption(label=label, value=str(i)))
                select = discord.ui.Select(placeholder="Select Match", options=options)
                select.callback = self.match_selected
                self.add_item(select)

            async def match_selected(self, interaction: discord.Interaction):
                match = self.matches[int(interaction.data['values'][0])]
                view = MapScoreView(self.parent.bot, self.parent, match)
                await interaction.response.edit_message(content=view.status_text(), view=view)
                view.message = await interaction.original_response()


        # Main logic
        scheduled_sheet = self.spreadsheet.worksheet("Match Scheduled")
        scheduled_matches = scheduled_sheet.get_all_values()[1:]
        user_id = str(interaction.user.id)
        matches = []

        for match in scheduled_matches:
            if not any(match) or len(match) < 4:
                print(f"⚠️ Skipping invalid row (too short): {match}")
                continue

            match_id, team1, team2, date = match[:4]

            team1_role = discord.utils.get(interaction.guild.roles, name=f"Team {team1} Captain")
            team2_role = discord.utils.get(interaction.guild.roles, name=f"Team {team2} Captain")

            is_captain = False
            proposer_team, opponent_team = None, None

            # Primary: check Discord roles
            if team1_role and interaction.user in team1_role.members:
                proposer_team, opponent_team = team1, team2
                is_captain = True
            elif team2_role and interaction.user in team2_role.members:
                proposer_team, opponent_team = team2, team1
                is_captain = True
            else:
                # Fallback: match by ID in captain cell of Teams sheet
                for row in self.teams_sheet.get_all_values()[1:]:
                    if row[0].strip() == team1 and f"({user_id})" in row[1]:
                        proposer_team, opponent_team = team1, team2
                        is_captain = True
                        break
                    elif row[0].strip() == team2 and f"({user_id})" in row[1]:
                        proposer_team, opponent_team = team2, team1
                        is_captain = True
                        break

            if not is_captain:
                continue

            matches.append({
                "match_id": match_id,
                "match_type": "weekly",
                "team1": proposer_team,
                "team2": opponent_team,
                "date": date,
                "proposed_datetime": datetime.utcnow().isoformat()
            })


        if not matches:
            await interaction.response.send_message("❗ No scheduled matches found or you are not a captain.", ephemeral=True)
            return

        view = MatchSelectView(self, matches)
        await interaction.response.send_message("Select match to propose score:", view=view, ephemeral=True)

    # ------------------ Find Sub ----------------------
    
    @discord.ui.button(label="🔍 Find Eligible Subs", style=discord.ButtonStyle.green, custom_id="league:find_subs")
    async def find_subs(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        team_row = None

        # Step 1: Find team user is on
        for row in self.teams_sheet.get_all_values()[1:]:
            if any(user_id in cell for cell in row[1:]):
                team_row = row
                break

        if not team_row:
            await interaction.response.send_message("❗ You are not currently on a team.", ephemeral=True)
            return

        team_name = team_row[0]
        team_elo = None
        for row in self.leaderboard_sheet.get_all_values()[1:]:
            if row[0].strip() == team_name:
                try:
                    team_elo = int(row[1])
                except:
                    pass
                break

        if team_elo is None:
            await interaction.response.send_message("❗ Could not find your team's ELO in the leaderboard.", ephemeral=True)
            return

        avg_elo = team_elo
        elo_sheet = self.bot.player_leaderboard_sheet.get_all_values()[1:]
        lower, upper = avg_elo - 100, avg_elo + 100

        # Step 2: Get all team player IDs
        team_players = set()
        for row in self.teams_sheet.get_all_values()[1:]:
            for cell in row[1:]:
                if "(" in cell and ")" in cell:
                    team_players.add(cell.split("(")[-1].split(")")[0])
        
        players_sheet = self.players_sheet.get_all_values()[1:]

        # Step 3: Filter eligible subs
        eligible = []
        for row in elo_sheet:
            try:
                name, uid, rating = row[0], row[1].strip(), int(row[2])

                # ✅ Skip if on a team
                if uid in team_players:
                    continue

                # ✅ Check Players sheet for role = "League Sub"
                player_row = next((r for r in players_sheet if r[0].strip() == uid and r[2].strip().lower() == "league sub"), None)
                if not player_row:
                    continue

                # ✅ Check ELO condition
                if rating <= avg_elo:
                    eligible.append((name, uid, rating, row[3], row[4]))  # Win/loss if applicable

            except Exception:
                continue


        # Sort and limit to top 24
        eligible = sorted(eligible, key=lambda r: r[2], reverse=True)[:24]

        if not eligible:
            await interaction.response.send_message(f"No eligible subs found in ELO range.", ephemeral=True)
            return

        eligible.sort(key=lambda r: int(r[2]), reverse=True)
        msg = f"🔍 Top 25 eligible subs for **{team_name}** (ELO ≤ {avg_elo}):\n"
        for e in eligible:
            msg += f"- <@{e[1]}> • ELO: {e[2]} • ✅ {e[3]} ❌ {e[4]}\n"

        ping_channel_id = self.config.get("sub_ping_channel_id")  # Set this in your config.json
        ping_channel = interaction.guild.get_channel(int(ping_channel_id)) if ping_channel_id else None

        if not ping_channel:
            await interaction.response.send_message("❗ Sub ping channel not configured or found.", ephemeral=True)
            return

        # Build ping list
        mentions = [f"<@{row[1].strip()}>" for row in eligible]
        content = (f"📢 **{team_name}** is looking for a league sub.\n"f"Eligible players:\n" + " ".join(mentions))

        await ping_channel.send(content)
        await interaction.response.send_message("✅ Pings sent to eligible subs.", ephemeral=True)

    # -------------------- JOIN TEAM --------------------
    
    @discord.ui.button(label="👥 Join Team", style=discord.ButtonStyle.blurple, custom_id="league:join_team")
    async def join_team(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)

        # Check if user is signed up
        if not self.player_signed_up(user_id):
            await interaction.response.send_message("❗ You must sign up for the league before joining a team.", ephemeral=True)
            return
        
        # ❌ Block league subs from joining teams
        sub_role = interaction.guild.get_role(self.bot.config.get("league_sub_role_id"))
        if sub_role and sub_role in interaction.user.roles:
            await interaction.response.send_message("❗ League subs are not eligible to join teams. Contact a dev if this is incorrect.", ephemeral=True)
            return
        
        # Roster Lock Check
        if is_roster_locked(self.bot.config):
            await self.safe_send(interaction, "🔒 Rosters are locked, Cannot Join at this time.")
            return

        class TeamSearchModal(discord.ui.Modal, title="Search Team Name"):
            query = discord.ui.TextInput(label="Enter Team Name", required=True)

            def __init__(self, parent_view):
                super().__init__()
                self.parent_view = parent_view

            async def on_submit(self, interaction: discord.Interaction):
                search = self.query.value.lower()
                all_teams = [row[0] for row in self.parent_view.teams_sheet.get_all_values() if row[0]]

                matches = [team for team in all_teams if search in team.lower()]
                if not matches:
                    matches = all_teams[:25]

                view = TeamSelectView(self.parent_view, matches, interaction.user)
                await interaction.response.send_message("Select the team you want to join:", view=view, ephemeral=True)

        class TeamSelectView(discord.ui.View):
            def __init__(self, parent_view, teams, user):
                super().__init__(timeout=None)
                self.parent_view = parent_view
                self.teams = teams
                self.user = user

                options = [discord.SelectOption(label=team, value=team) for team in self.teams]
                select = discord.ui.Select(placeholder="Select Team", options=options)
                select.callback = self.select_team
                self.add_item(select)

            async def select_team(self, interaction: discord.Interaction):
                selected_team = self.children[0].values[0]

                headers = self.parent_view.teams_sheet.row_values(1)

                for row in self.parent_view.teams_sheet.get_all_values()[1:]:
                    members = row[1:7]
                    for cell in members:
                        if extract_user_id(cell) == str(self.user.id):
                            await interaction.response.send_message("❗ You are already on a team.", ephemeral=True)
                            return

                guild = interaction.guild
                team_role = discord.utils.get(guild.roles, name=f"Team {selected_team}")

                if not team_role:
                    await interaction.response.send_message("❗ Team role does not exist.", ephemeral=True)
                    return

                captain = None
                for row in self.parent_view.teams_sheet.get_all_values()[1:]:
                    if row[0].lower() == selected_team.lower():
                        try:
                            user_id_str = row[1].split("(")[-1].replace(")", "").strip()
                            captain = await guild.fetch_member(int(user_id_str))
                        except Exception:
                            pass
                        break

                if not captain:
                    await interaction.response.send_message("❗ Could not find team captain.", ephemeral=True)
                    return

                try:
                    await captain.send(
                        f"📥 **{self.user.display_name}** wants to join **{selected_team}**. Approve?",
                        view=AcceptDenyJoinRequestView(self.parent_view, selected_team, self.user, guild.id)
                    )
                    await interaction.response.send_message("✅ Request sent to team captain via DM.", ephemeral=True)

                except discord.Forbidden:
                    fallback_category_id = self.parent_view.config.get("fallback_category_id")
                    fallback_category = guild.get_channel(int(fallback_category_id)) if fallback_category_id else None

                    # Try to find existing fallback channel in the category
                    fallback_channel = None
                    if fallback_category:
                        for ch in fallback_category.text_channels:
                            if ch.name == "team-requests":
                                fallback_channel = ch
                                break

                    # If not found, create one in the correct category
                    if fallback_channel is None:
                        overwrites = {
                            guild.default_role: discord.PermissionOverwrite(read_messages=False),
                            captain: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
                        }
                        fallback_channel = await guild.create_text_channel(
                            "team-requests",
                            overwrites=overwrites,
                            category=fallback_category
                        )
                    else:
                        await fallback_channel.set_permissions(captain, read_messages=True, send_messages=True)

                    # After fallback_channel.send(...)
                    await fallback_channel.send(
                        f"📥 {captain.mention} **{self.user.display_name}** wants to join **{selected_team}**. Approve?",
                        view=AcceptDenyJoinRequestView(self.parent_view, selected_team, self.user, guild.id)
                    )

                    # Use safe send response to user
                    await safe_send(interaction, "✅ Captain's DMs closed, sent request to private channel.")

                    # Define auto_delete
                    async def auto_delete(channel):
                        await discord.utils.sleep_until(datetime.utcnow() + timedelta(minutes=5))
                        try:
                            if len([m async for m in channel.history(limit=1)]) > 0:
                                await channel.delete()
                        except discord.NotFound:
                            print("❗ Tried to auto-delete, but channel no longer exists.")

                    # Schedule it
                    self.parent_view.bot.loop.create_task(auto_delete(fallback_channel))


        class AcceptDenyJoinRequestView(discord.ui.View):
            def __init__(self, parent_view, team_name, invitee, guild_id):
                super().__init__(timeout=None)
                self.parent_view = parent_view
                self.team_name = team_name
                self.invitee = invitee
                self.guild_id = guild_id

            @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success, custom_id="team_join_accept")
            async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
                guild = self.parent_view.bot.get_guild(self.guild_id)
                team_role = discord.utils.get(guild.roles, name=f"Team {self.team_name}")

                if not team_role:
                    await interaction.response.send_message("❗ Team role no longer exists.", ephemeral=True)
                    return

                already_on_team = False
                for row in self.parent_view.teams_sheet.get_all_values():
                    for cell in row[1:7]:
                        if cell.strip() == f"{self.invitee.display_name} ({self.invitee.id})":
                            already_on_team = True
                            break
                    if already_on_team:
                        break

                if already_on_team:
                    await interaction.response.send_message("❗ Player is already on another team.", ephemeral=True)
                    return

                await self.invitee.add_roles(team_role)

                for idx, row in enumerate(self.parent_view.teams_sheet.get_all_values(), 1):
                    if row[0].lower() == self.team_name.lower():
                        max_players = self.parent_view.config.get("team_max_players", 6)
                        current_players = [p for p in row[1:7] if p.strip()]
                        if len(current_players) >= max_players:
                            await interaction.response.send_message(
                                f"❗ This team already has the maximum number of players ({max_players}).",
                                ephemeral=True
                            )
                            return
                        for i in range(1, 7):
                            if row[i] == "":
                                self.parent_view.teams_sheet.update_cell(idx, i + 1, f"{self.invitee.display_name} ({self.invitee.id})")
                                break
                        # ✅ Check minimum player count
                        team_row = self.parent_view.teams_sheet.row_values(idx)
                        player_count = sum(1 for cell in team_row[1:7] if cell.strip())
                        min_required = self.parent_view.config.get("team_min_players", 3)

                        if player_count == min_required:
                            try:
                                await self.parent_view.send_notification(
                                    f"✅ **{self.team_name}** has reached the minimum required players ({min_required}) and is now eligible for matches!"
                                )
                            except Exception as e:
                                print(f"❗ Failed to send team eligibility notification: {e}")    
                        break

                await interaction.response.send_message("✅ Player added to team.", ephemeral=True)

                # ✅ Send notification to league announcement channel
                try:
                    await self.parent_view.send_notification(
                        f"👥 {self.invitee.mention} has joined **{self.team_name}**!"
                    )
                except Exception as e:
                    print(f"❗ Failed to send join team notification: {e}")

                await interaction.message.delete()

                if isinstance(interaction.channel, discord.TextChannel) and interaction.channel.name == "team-requests":
                    await interaction.channel.delete()

            @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger, custom_id="team_join_deny")
            async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.response.send_message("❌ Request denied.", ephemeral=True)

                await interaction.message.delete()

                if isinstance(interaction.channel, discord.TextChannel) and interaction.channel.name == "team-requests":
                    await interaction.channel.delete()

        await interaction.response.send_modal(TeamSearchModal(self))

    # -------------------- LEAVE TEAM --------------------

    @discord.ui.button(label="🚪 Leave Team", style=discord.ButtonStyle.red, custom_id="league:leave_team")
    async def leave_team(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)

        for idx, row in enumerate(self.teams_sheet.get_all_values(), 1):
            if idx == 1 or not row or not row[0].strip():
                continue

            team_name = row[0]
            members = row[1:7]

            for col, cell in enumerate(members, start=2):  # B-G = cols 2–7
                if extract_user_id(cell) == user_id:
                    if col == 2:  # Column B = captain
                        await interaction.response.send_message("❗ You are the captain. Promote or disband first.", ephemeral=True)
                        return

                    # Remove from sheet
                    self.teams_sheet.update_cell(idx, col, "")

                    # Remove Discord roles
                    guild = interaction.guild
                    team_role = discord.utils.get(interaction.guild.roles, name=f"Team {team_name}")
                    captain_role = guild.get_role(self.bot.config.get("universal_captain_role_id"))
                    member = interaction.guild.get_member(int(user_id))

                    roles_to_remove = []
                    if team_role and team_role in member.roles:
                        roles_to_remove.append(team_role)
                    if captain_role and captain_role in member.roles:
                        roles_to_remove.append(captain_role)

                    if roles_to_remove:
                        try:
                            await member.remove_roles(*roles_to_remove)
                        except discord.Forbidden:
                            print(f"❗ Could not remove roles from {member.display_name}")

                    await interaction.response.send_message(f"✅ You left **{team_name}**.", ephemeral=True)

                    try:
                        await self.send_notification(f"🚪 {member.mention} has left **{team_name}**.")
                    except Exception as e:
                        print(f"❗ Failed to send notification: {e}")

                    return

        await interaction.response.send_message("You are not on a team.", ephemeral=True)

    # -------------------- UNSIGNUP --------------------

    @discord.ui.button(label="❌ Unsignup", style=discord.ButtonStyle.red, custom_id="league:unsignup")
    async def unsignup(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)

        # Check if on a team first
        for team in self.teams_sheet.get_all_values():
            for cell in team[1:]:
                if extract_user_id(cell) == user_id:
                    await interaction.response.send_message("❗ You are currently on a team. Leave your team before unsigning.", ephemeral=True)
                    return

        # Remove from player sheet
        for idx, row in enumerate(self.players_sheet.get_all_values()[1:], start=2):
            if len(row) > 0 and row[0].strip() == user_id:
                self.players_sheet.delete_rows(idx)

                # 🧼 Remove roles
                guild = interaction.guild
                if guild:
                    member = interaction.user
                    player_role = interaction.guild.get_role(self.bot.config.get("player_role_id"))
                    sub_role = interaction.guild.get_role(self.bot.config.get("league_sub_role_id"))

                    try:
                        if player_role in member.roles:
                            await member.remove_roles(player_role)
                        if sub_role in member.roles:
                            await member.remove_roles(sub_role)
                    except discord.Forbidden:
                        print(f"⚠️ Missing permissions to remove roles from {member}")

                await interaction.response.send_message("✅ You have been removed from the league.", ephemeral=True)

                try:
                    await self.send_notification(f"❌ {interaction.user.mention} has left the league.")
                except Exception as e:
                    print(f"❗ Failed to send unsignup notification: {e}")
                return

        await interaction.response.send_message("❗ You are not signed up.", ephemeral=True)

    # -------------------- PROMOTE PLAYER ------------------

    @discord.ui.button(label="⭐ Promote Player", style=discord.ButtonStyle.green, custom_id="league:promote_player")
    async def promote_player(self, interaction: discord.Interaction, button: discord.ui.Button):
        username_id = f"{interaction.user.display_name} ({interaction.user.id})"

        # Find team and check if user is captain
        for idx, team in enumerate(self.teams_sheet.get_all_values(), 1):
            if team[1] == username_id:
                team_name = team[0]
                members = [player for player in team[1:] if player]

                # Build dropdown options (skip self / captain)
                options = [
                    discord.SelectOption(label=p.split(" (")[0], value=p)
                    for p in members if p != username_id
                ]

                if not options:
                    await interaction.response.send_message("❗ No players available to promote.", ephemeral=True)
                    return

                class PromoteSelect(discord.ui.View):
                    def __init__(self, parent, team_name, old_captain, team_idx):
                        super().__init__(timeout=None)
                        self.parent = parent
                        self.team_name = team_name
                        self.old_captain = old_captain
                        self.team_idx = team_idx

                        select = discord.ui.Select(placeholder="Select player to promote", options=options)
                        select.callback = self.promote
                        self.add_item(select)

                    async def promote(self, select_interaction):
                        new_captain_user_id = extract_user_id(select_interaction.data['values'][0])
                        guild = select_interaction.guild

                        old_captain_member = guild.get_member(int(extract_user_id(self.old_captain)))
                        new_captain_member = guild.get_member(int(new_captain_user_id))

                        captain_role = guild.get_role(self.bot.config.get("universal_captain_role_id"))
                        if captain_role:
                            if old_captain_member and captain_role in old_captain_member.roles:
                                await old_captain_member.remove_roles(captain_role)
                            if new_captain_member:
                                await new_captain_member.add_roles(captain_role)

                        # Update sheet safely
                        row = self.parent.teams_sheet.row_values(self.team_idx)
                        old_captain_str = self.old_captain
                        new_captain_str = f"{new_captain_member.display_name} ({new_captain_member.id})"

                        # Find and swap the new captain's old cell
                        for i in range(1, 7):
                            if extract_user_id(row[i]) == new_captain_user_id:
                                row[i] = old_captain_str
                                break

                        row[1] = new_captain_str  # set new captain in col B

                        self.parent.teams_sheet.update(f"A{self.team_idx}:G{self.team_idx}", [row])

                        await select_interaction.response.send_message(
                            f"✅ {new_captain_member.mention} is now the captain of **{self.team_name}**!",
                            ephemeral=True
                        )
                        await self.parent.send_notification(
                            f"⭐ {new_captain_member.mention} has been promoted to **Captain of {self.team_name}**."
                        )

                await interaction.response.send_message("Select player to promote to captain:", view=PromoteSelect(self, team_name, username_id, idx), ephemeral=True)
                return

        await interaction.response.send_message("❗ You are not a captain or on a team.", ephemeral=True)

    # -------------------- DISBAND TEAM --------------------

    @discord.ui.button(label="❗ Disband Team", style=discord.ButtonStyle.red, custom_id="league:disband_team")
    async def disband_team(self, interaction: discord.Interaction, button: discord.ui.Button):

        class DisbandModal(Modal, title="Disband Team"):
            team_name = TextInput(label="Team Name")

            def __init__(self, parent_view, bot):
                super().__init__()
                self.parent_view = parent_view
                self.bot = bot

            async def on_submit(self, modal_interaction: discord.Interaction):
                team_name = self.team_name.value.strip()

                for idx, team in enumerate(self.parent_view.teams_sheet.get_all_values(), 1):
                    if team[0].lower() == team_name.lower():
                        team_captain_raw = team[1]
                        captain_id = extract_user_id(team_captain_raw)

                        # 🔒 Verify authority to disband
                        if captain_id:
                            if str(modal_interaction.user.id) != str(captain_id):
                                if str(modal_interaction.user.id) not in self.parent_view.DEV_OVERRIDE_IDS:
                                    await modal_interaction.response.send_message("❗ Only the captain or a developer can disband this team.", ephemeral=True)
                                    return
                        else:
                            if str(modal_interaction.user.display_name) not in team_captain_raw:
                                if str(modal_interaction.user.id) not in self.parent_view.DEV_OVERRIDE_IDS:
                                    await modal_interaction.response.send_message("❗ Only the captain or a developer can disband this team.", ephemeral=True)
                                    return

                        guild = modal_interaction.guild
                        team_role = discord.utils.get(guild.roles, name=f"Team {team_name}")
                        captain_role = guild.get_role(self.bot.config.get("universal_captain_role_id"))

                        # 🧹 Delete team role
                        if team_role:
                            try:
                                await team_role.delete()
                            except Exception as e:
                                print(f"⚠️ Failed to delete team role: {e}")

                        # 🧹 Remove captain role from user
                        if captain_role and captain_id:
                            member = guild.get_member(int(captain_id))
                            if member and captain_role in member.roles:
                                try:
                                    await member.remove_roles(captain_role)
                                except discord.Forbidden:
                                    print(f"⚠️ Could not remove Captain role from {member.display_name}")

                        # 🧾 Remove from sheet
                        self.parent_view.teams_sheet.delete_rows(idx)

                        await modal_interaction.response.send_message("✅ Team disbanded successfully.", ephemeral=True)
                        await self.parent_view.send_notification(f"💥 **{team_name}** has been disbanded.")
                        return

                await modal_interaction.response.send_message("❗ Team not found.", ephemeral=True)

        modal = DisbandModal(self, self.bot)
        await interaction.response.send_modal(modal)













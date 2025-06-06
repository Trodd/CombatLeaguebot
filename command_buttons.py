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
                        print(f"[🗑️] Removing accepted proposal {self.match_id} from Match Proposed")
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
            print("❗ Tried to respond to a stale interaction.")

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

        # Final safety: ensure proposed score row exists before continuing
        match_id = self.match["match_id"].strip()
        existing = self.parent.proposed_scores_sheet.get_all_values()[1:]
        if not any(row[0].strip() == match_id for row in existing):
            await self.safe_send(interaction, "⚠️ This match has no active score proposal to accept. It may have expired or already been finalized.")
            return

        from match import update_team_rating
        map_scores = [(m["gamemode"], int(m["team1_score"]), int(m["team2_score"])) for m in self.map_scores]
        total_a = sum(s[1] for s in map_scores)
        total_b = sum(s[2] for s in map_scores)
        maps_won_a = sum(1 for s in map_scores if s[1] > s[2])
        maps_won_b = sum(1 for s in map_scores if s[2] > s[1])

        if total_a > total_b:
            winner = self.match["team1"]
            loser = self.match["team2"]
        elif total_b > total_a:
            winner = self.match["team2"]
            loser = self.match["team1"]
        else:
            winner = "Tie"
            loser = ""

        # Append to scoring + leaderboard
        if winner != "Tie":
            update_team_rating(self.parent.leaderboard_sheet, winner, True, 25, -25)
            update_team_rating(self.parent.leaderboard_sheet, loser, False, 25, -25)

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

        # Remove from Proposed, Scheduled, Proposed Scores (by match ID in column A)
        for sheet in [self.parent.proposed_sheet, self.parent.scheduled_sheet, self.parent.proposed_scores_sheet]:
            rows = sheet.get_all_values()[1:]
            for idx, row in enumerate(rows, start=2):
                if row and row[0].strip() == match_id:
                    sheet.delete_rows(idx)
                    print(f"🗑️ Removed {match_id} from {sheet.title}")
                    break

        # ✅ Remove from Weekly Matches where match ID is in column D (index 3)
        weekly_rows = self.parent.weekly_matches_sheet.get_all_values()[1:]
        for idx, row in enumerate(weekly_rows, start=2):
            if len(row) >= 4 and row[3].strip() == match_id:
                self.parent.weekly_matches_sheet.delete_rows(idx)
                print(f"🗑️ Removed {match_id} from Weekly Matches (col D)")
                break

        # Match sheet update
        match_sheet = get_or_create_sheet(self.parent.spreadsheet, "Matches", [])
        for idx, row in enumerate(match_sheet.get_all_values()[1:], start=2):
            if row[0].strip() == match_id:
                match_sheet.update_cell(idx, 6, "Finished")
                match_sheet.update_cell(idx, 7, winner if winner != "Tie" else "")
                match_sheet.update_cell(idx, 8, loser if winner != "Tie" else "")
                break

        # Post final score embed with mentions and week number
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

            # 🗓️ Get week number from LeagueWeek sheet
            try:
                league_week_sheet = get_or_create_sheet(self.parent.spreadsheet, "LeagueWeek", ["League Week"])
                week_number = league_week_sheet.get_all_values()[1][0]
            except Exception:
                week_number = "?"

            embed = discord.Embed(
                title="🏆 Final Match Result",
                description=f"**{self.match['team1']}** vs **{self.match['team2']}**",
                color=discord.Color.gold()
            )
            embed.add_field(name="📆 Week", value=f"Week {week_number}", inline=False)

            for i, s in enumerate(self.map_scores, 1):
                embed.add_field(
                    name=f"Map {i} ({s['gamemode']})",
                    value=f"{self.match['team1']} {s['team1_score']} - {s['team2_score']} {self.match['team2']}",
                    inline=False
                )
            embed.add_field(name="Winner", value=winner, inline=False)

            await score_channel.send(
                content=" ".join(mentions_a + mentions_b),
                embed=embed
            )

        await self.safe_send(interaction, "✅ Score accepted and finalized.")
        try:
            await self.proposer.send("✅ Your proposed match scores have been accepted and finalized.")
        except discord.Forbidden:
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

        # ✅ Remove row from Proposed Scores
        for idx, row in enumerate(self.parent.proposed_scores_sheet.get_all_values()[1:], start=2):
            if row and row[0].strip() == self.match["match_id"].strip():
                self.parent.proposed_scores_sheet.delete_rows(idx)
                print(f"🗑️ Removed denied proposed score for {self.match['match_id']}")
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

    async def on_timeout(self):
        try:
            await self.proposer.send("⏳ Your proposed scores expired due to no response.")
        except:
            pass
        try:
            if self.message:
                await self.message.delete()
        except:
            pass
        if self.channel_to_delete:
            try:
                await self.channel_to_delete.delete()
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
        banned_players = banned_sheet.get_all_values()[1:]

# Check if user is banned
        if any(row[0] == user_id for row in banned_players):
            await interaction.response.send_message("❗ You are banned from signing up for the league.", ephemeral=True)
            return

        # Check if already signed up
        existing_ids = self.players_sheet.col_values(1)
        if user_id in existing_ids:
            await interaction.response.send_message("❗ You are already signed up.", ephemeral=True)
            return

        # Signup
        self.players_sheet.append_row([user_id, username])
        await interaction.response.send_message("✅ You have been signed up!", ephemeral=True)
        await self.send_notification(f"📌 {interaction.user.mention} has signed up for the league!")


# -------------------- CREATE TEAM --------------------

    @discord.ui.button(label="🏷️ Create Team", style=discord.ButtonStyle.blurple, custom_id="league:create_team")
    async def create_team(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)

        # ✅ Check if user is signed up
        if not self.player_signed_up(user_id):
            await interaction.response.send_message("❗ You must sign up for the league before creating a team.", ephemeral=True)
            return

        user_display = f"{interaction.user.display_name} ({interaction.user.id})"

        # Check if rosters are locked
        headers = self.teams_sheet.row_values(1)
        if "Locked" in headers:
            locked_col = headers.index("Locked") + 1
            is_locked = any(row[locked_col - 1].strip().lower() == "yes" for row in self.teams_sheet.get_all_values()[1:])
            if is_locked:
                await interaction.response.send_message("❗ Rosters are locked. You cannot create a new team right now.", ephemeral=True)
                return

        # Check if user is already a captain or team member
        for row in self.teams_sheet.get_all_values()[1:]:  # Skip header
            members = row[1:7]  # Columns B–G: captain + members
            for cell in members:
                if extract_user_id(cell) == user_id:
                    if cell == row[1]:
                        await interaction.response.send_message("❗ You are already a captain. Disband or transfer captain role first.", ephemeral=True)
                    else:
                        await interaction.response.send_message("❗ You are already on a team. Leave your current team first.", ephemeral=True)
                    return

        class TeamNameModal(discord.ui.Modal, title="Create Team"):
            team_name = discord.ui.TextInput(label="Team Name", required=True)

            def __init__(self, parent_view):
                super().__init__()
                self.parent = parent_view

            async def on_submit(self, modal_interaction: discord.Interaction):
                team_name = self.team_name.value.strip()

                # Check for duplicate team names
                existing_teams = [row[0].lower() for row in self.parent.teams_sheet.get_all_values()]
                if team_name.lower() in existing_teams:
                    await modal_interaction.response.send_message("❗ Team already exists.", ephemeral=True)
                    return

                guild = modal_interaction.guild

                # Create team roles
                team_role = await guild.create_role(name=f"Team {team_name}")
                captain_role = await guild.create_role(name=f"Team {team_name} Captain")

                # Assign roles to captain
                await modal_interaction.user.add_roles(team_role, captain_role)

                # Add team to sheet with captain only
                self.parent.teams_sheet.append_row([team_name, f"{modal_interaction.user.display_name} ({modal_interaction.user.id})"] + [""] * 5)

                # ✅ Check if interaction still active
                if modal_interaction.response.is_done():
                    await interaction.followup.send(f"✅ Team **{team_name}** created! Invite players to join your team.", ephemeral=True)
                else:
                    await modal_interaction.response.send_message(
                        f"✅ Team **{team_name}** created! Invite players to join your team.",
                        ephemeral=True
                    )

                await self.parent.send_notification(f"🎉 **Team Created:** `{team_name}` by {modal_interaction.user.mention}")

        await interaction.response.send_modal(TeamNameModal(self))

    # -------------------- PROPOSE MATCH --------------------

    @discord.ui.button(label="📅 Propose Match", style=discord.ButtonStyle.green, custom_id="league:propose_match")
    async def propose_match(self, interaction: discord.Interaction, button: discord.ui.Button):

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
            def __init__(self, parent, user_team,  opponents, is_challenge,):
                super().__init__(timeout=None)
                self.parent = parent
                self.user_team = user_team
                self.opponents = opponents
                self.is_challenge = is_challenge

                select = discord.ui.Select(placeholder="Select Opponent", options=[discord.SelectOption(label=op, value=op) for op in opponents])
                select.callback = self.opponent_selected
                self.add_item(select)

            async def opponent_selected(self, interaction: discord.Interaction):
                selected_opponent = interaction.data['values'][0]
                await interaction.response.send_message("Select date and time:", view=DateTimeView(self.parent, self.user_team, selected_opponent, self.is_challenge), ephemeral=True)

        class DateTimeView(discord.ui.View):
            def __init__(self, parent, team_a, team_b, is_challenge):
                super().__init__(timeout=None)
                self.parent = parent
                self.team_a = team_a
                self.team_b = team_b
                self.is_challenge = is_challenge
                self.date_time = {}

            @discord.ui.button(label="📅 Select Date (Month & Day)", style=discord.ButtonStyle.primary, custom_id="league:propose_match_month")
            async def select_date(self, interaction: discord.Interaction, button: discord.ui.Button):
                months = [discord.SelectOption(label=str(m), value=str(m)) for m in range(1, 13)]
                select_month = discord.ui.Select(placeholder="Select Month", options=months)

                days_1_15 = [discord.SelectOption(label=str(d), value=str(d)) for d in range(1, 16)]
                select_day1 = discord.ui.Select(placeholder="Select Day 1-15", options=days_1_15)

                days_16_31 = [discord.SelectOption(label=str(d), value=str(d)) for d in range(16, 32)]
                select_day2 = discord.ui.Select(placeholder="Select Day 16-31", options=days_16_31)

                async def selected(interaction):
                    selected_month = select_month.values[0] if select_month.values else None
                    selected_day = select_day1.values[0] if select_day1.values else (select_day2.values[0] if select_day2.values else None)

                    if not selected_month or not selected_day:
                        await interaction.response.send_message("❗ Please select both month and day.", ephemeral=True)
                        return

                    self.date_time["month"] = selected_month
                    self.date_time["day"] = selected_day

                    await interaction.response.send_message(f"✅ Date selected: {selected_month}/{selected_day}", ephemeral=True)

                select_month.callback = selected
                select_day1.callback = selected
                select_day2.callback = selected

                view = discord.ui.View(timeout=None)
                view.add_item(select_month)
                view.add_item(select_day1)
                view.add_item(select_day2)

                await interaction.response.send_message("Select month and day:", view=view, ephemeral=True)

            @discord.ui.button(label="⏰ Select Time (Hour, Minute, AM/PM)", style=discord.ButtonStyle.primary, custom_id="league:propose_time")
            async def select_time(self, interaction: discord.Interaction, button: discord.ui.Button):
                hours = [discord.SelectOption(label=str(h), value=str(h)) for h in range(1, 13)]
                select_hour = discord.ui.Select(placeholder="Hour", options=hours)

                minutes = [discord.SelectOption(label=str(m).zfill(2), value=str(m).zfill(2)) for m in range(0, 60, 5)]
                select_minute = discord.ui.Select(placeholder="Minute", options=minutes)

                am_pm = [discord.SelectOption(label=period, value=period) for period in ["AM", "PM"]]
                select_am_pm = discord.ui.Select(placeholder="AM/PM", options=am_pm)

                async def selected(inner: discord.Interaction):
                    await inner.response.defer(ephemeral=True)

                    if not select_hour.values or not select_minute.values or not select_am_pm.values:
                        msg = "❗ Please select hour, minute, and AM/PM."
                        await inner.followup.send(msg, ephemeral=True)
                        return

                    self.date_time["hour"] = select_hour.values[0]
                    self.date_time["minute"] = select_minute.values[0]
                    self.date_time["am_pm"] = select_am_pm.values[0]

                    required_fields = ["month", "day", "hour", "minute", "am_pm"]
                    if all(k in self.date_time and self.date_time[k] for k in required_fields):
                        league_week_sheet = get_or_create_sheet(self.parent.spreadsheet, "LeagueWeek", ["League Week"])
                        week_number = int(league_week_sheet.get_all_values()[1][0])
                        msg = "✅ All fields selected. Ready to submit your match proposal:"
                        view = SubmitProposalView(self.parent, self.date_time, self.team_a, self.team_b, self.is_challenge, interaction.user.id, week_number=week_number)
                    else:
                        msg = f"✅ Time selected: {self.date_time['hour']}:{self.date_time['minute']} {self.date_time['am_pm']}\nPlease finish selecting date."
                        view = None

                    if view:
                        await inner.followup.send(msg, view=view, ephemeral=True)
                    else:
                        await inner.followup.send(msg, ephemeral=True)

                # Bind the callback
                select_hour.callback = selected
                select_minute.callback = selected
                select_am_pm.callback = selected

                # Display dropdowns
                view = discord.ui.View(timeout=None)
                view.add_item(select_hour)
                view.add_item(select_minute)
                view.add_item(select_am_pm)

                await interaction.response.send_message("Select hour, minute, and AM/PM:", view=view, ephemeral=True)

        class SubmitProposalView(discord.ui.View):
            def __init__(self, parent, date_time, team_a, team_b, is_challenge, proposer_id, week_number=None):
                super().__init__(timeout=None)
                self.parent = parent
                self.date_time = date_time
                self.team_a = team_a
                self.team_b = team_b
                self.is_challenge = is_challenge
                self.week_number = week_number
                self.proposer_id = proposer_id

            @discord.ui.button(label="✅ Submit Match Proposal", style=discord.ButtonStyle.green, custom_id="league:submit_match_propose")
            async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
                from datetime import datetime

                try:
                    month = int(self.date_time["month"])
                    day = int(self.date_time["day"])
                    hour = int(self.date_time["hour"])
                    minute = int(self.date_time["minute"])
                    am_pm = self.date_time["am_pm"]

                    if am_pm.upper() == "PM" and hour != 12:
                        hour += 12
                    elif am_pm.upper() == "AM" and hour == 12:
                        hour = 0

                    year = datetime.utcnow().year
                    naive_dt = datetime(year, month, day, hour, minute)
                    discord_ts = int(naive_dt.timestamp())
                    proposed_date = f"<t:{discord_ts}:f>"
                    proposed_datetime = naive_dt
                except Exception as e:
                    await interaction.response.send_message(f"❗ Failed to build match time: {e}", ephemeral=True)
                    return

                # Check duplicates
                existing = self.parent.proposed_sheet.get_all_values()[1:]
                for row in existing:
                    if (row[0] == self.team_a and row[1] == self.team_b) or (row[0] == self.team_b and row[1] == self.team_a):
                        await interaction.response.send_message("❗ A match proposal between these teams already exists.", ephemeral=True)
                        return

                # Generate match ID
                if self.is_challenge:
                    match_id = f"Challenge{self.week_number}-{self.team_a[:3]}-{self.team_b[:3]}"
                else:
                    match_id = f"Week{self.week_number}-{self.team_a[:3]}-{self.team_b[:3]}"

                # Resolve captain first
                guild = interaction.guild
                team_role = discord.utils.get(guild.roles, name=f"Team {self.team_b}")
                captain_role = discord.utils.get(guild.roles, name=f"Team {self.team_b} Captain")
                captain = None
                if team_role and captain_role:
                    for member in captain_role.members:
                        if team_role in member.roles:
                            captain = member
                            break

                # Build view
                view = AcceptDenyMatchView(
                    self.parent,
                    self.team_a,
                    self.team_b,
                    proposed_date,
                    match_id=match_id,
                    match_type="challenge" if self.is_challenge else "assigned",
                    week_number=self.week_number if not self.is_challenge else None,
                    proposed_datetime=proposed_datetime,
                    proposer_id=interaction.user.id
                )

                # ✅ Deliver proposal
                if captain:
                    fallback_id = int(self.parent.config.get("fallback_category_id"))
                    private_channel = await create_private_channel(
                        guild, fallback_id,
                        f"proposed-match-{self.team_a}-vs-{self.team_b}",
                        [interaction.user, captain]
                    )

                    if private_channel:
                        msg = await private_channel.send(
                            f"{captain.mention} 📨 Proposed Match from **{self.team_a}** on {proposed_date}. Accept?",
                            view=view
                        )
                        view.message = msg
                        view.channel_to_delete = private_channel
                        self.parent.bot.add_view(view, message_id=msg.id)

                        # ✅ Now safe to log
                        self.parent.proposed_sheet.append_row([
                            match_id,
                            self.team_a,
                            self.team_b,
                            str(interaction.user.id),
                            proposed_date,
                            str(private_channel.id),
                            str(msg.id)
                        ])

                        if self.is_challenge:
                            self.parent.challenge_sheet.append_row([
                                self.week_number,
                                match_id,
                                self.team_a,
                                self.team_b,
                                str(interaction.user.id),
                                proposed_date,
                                "",
                                "Pending"
                            ])
                    else:
                        await interaction.followup.send("❗ Failed to create fallback channel.", ephemeral=True)
                        return
                else:
                    await interaction.followup.send("❗ No captain found to deliver the proposal.", ephemeral=True)
                    return

                # Final ack to proposer
                msg = (
                    f"✅ Proposed match submitted:\n"
                    f"**{self.team_a}** vs **{self.team_b}**\n"
                    f"🕓 Scheduled for {proposed_date}"
                )
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(msg, ephemeral=True)
                    else:
                        await interaction.followup.send(msg, ephemeral=True)
                except discord.NotFound:
                    print("❗ Interaction expired before response could be sent.")

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
                overwrites[member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            return await guild.create_text_channel(name=channel_name, category=category, overwrites=overwrites)

        

        class MapScoreModal(discord.ui.Modal, title="Enter Map Score"):
            def __init__(self, parent, match, map_scores, map_number, gamemode):
                super().__init__()
                self.parent = parent
                self.match = match
                self.map_scores = map_scores
                self.map_number = map_number
                self.gamemode = gamemode
                

                if gamemode == "Payload":
                    self.team1_score = discord.ui.TextInput(label=f"{self.match['team1']} Total Rounds Won ", required=True)
                    self.team2_score = discord.ui.TextInput(label=f"{self.match['team2']} Total Rounds Won ", required=True)
                else:
                    self.team1_score = discord.ui.TextInput(label=f"{self.match['team1']} Rounds Won (Best of 3)", required=True)
                    self.team2_score = discord.ui.TextInput(label=f"{self.match['team2']} Rounds Won (Best of 3)", required=True)

                self.add_item(self.team1_score)
                self.add_item(self.team2_score)

            async def on_submit(self, interaction: discord.Interaction):
                await interaction.response.defer(ephemeral=True)
                self.map_scores.append({
                    "gamemode": self.gamemode,
                    "team1_score": self.team1_score.value,
                    "team2_score": self.team2_score.value
                })

                view = MapScoreView(self.parent, self.match, self.map_scores)
                await interaction.followup.send("✅ Map score saved!", view=view, ephemeral=True)

        class MapGamemodeSelectView(discord.ui.View):
            def __init__(self, parent, match, map_scores, map_number):
                super().__init__(timeout=None)
                self.parent = parent
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
                await interaction.response.send_modal(MapScoreModal(self.parent, self.match, self.map_scores, self.map_number, gamemode))

        class MapScoreView(discord.ui.View):
            def __init__(self, parent, match, map_scores):
                super().__init__(timeout=None)
                self.parent = parent
                self.match = match
                self.map_scores = map_scores

            @discord.ui.button(label="Map 1: Enter Score", style=discord.ButtonStyle.green, custom_id="propose_score_map1")
            async def map1(self, interaction: discord.Interaction, button: discord.ui.Button):
                view = MapGamemodeSelectView(self.parent, self.match, self.map_scores, 1)
                await interaction.response.send_message("Select gamemode for Map 1:", view=view, ephemeral=True)

            @discord.ui.button(label="Map 2: Enter Score", style=discord.ButtonStyle.green, custom_id="propose_score_map2")
            async def map2(self, interaction: discord.Interaction, button: discord.ui.Button):
                view = MapGamemodeSelectView(self.parent, self.match, self.map_scores, 2)
                await interaction.response.send_message("Select gamemode for Map 2:", view=view, ephemeral=True)

            @discord.ui.button(label="Map 3 (Optional): Enter Score", style=discord.ButtonStyle.blurple, custom_id="propose_score_map3")
            async def map3(self, interaction: discord.Interaction, button: discord.ui.Button):
                view = MapGamemodeSelectView(self.parent, self.match, self.map_scores, 3)
                await interaction.response.send_message("Select gamemode for Map 3:", view=view, ephemeral=True)
            
            @discord.ui.button(label="✅ Submit All Maps", style=discord.ButtonStyle.success, custom_id="propose_score_submaps")
            async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
                if len(self.map_scores) < 2:
                    await interaction.response.send_message("❗ Please enter Map 1 and Map 2 first.", ephemeral=True)
                    return

                await interaction.response.send_message("✅ Proposed scores submitted. Waiting for opponent confirmation...", ephemeral=True)

                opponent_team = self.match["team2"] if interaction.user in [
                    m for m in discord.utils.get(interaction.guild.roles, name=f"Team {self.match['team1']} Captain").members
                ] else self.match["team1"]

                opponent_role = discord.utils.get(interaction.guild.roles, name=f"Team {opponent_team} Captain")
                opponent_captain = opponent_role.members[0] if opponent_role and opponent_role.members else None

                embed = discord.Embed(title="Proposed Match Scores", description=f"**{self.match['team1']}** vs **{self.match['team2']}**")
                for i, s in enumerate(self.map_scores, 1):
                    embed.add_field(name=f"Map {i} ({s['gamemode']})", value=f"{self.match['team1']} {s['team1_score']} - {s['team2_score']} {self.match['team2']}", inline=False)

                if opponent_captain:
                    category_id = self.parent.config.get("fallback_category_id")
                    private_channel = await create_private_channel(
                        interaction.guild,
                        int(category_id),
                        f"proposed-score-{self.match['team1']}-vs-{self.match['team2']}",
                        [interaction.user, opponent_captain]
                    )

                    if private_channel:
                        view = ConfirmScoreView(self.parent, self.match, self.map_scores, interaction.user, interaction.user.id, private_channel)
                        msg = await private_channel.send(
                            f"{opponent_captain.mention} 📨 Proposed Match Scores from **{self.match['team1']}**.",
                            embed=embed,
                            view=view
                        )
                        # ✅ Always log to Proposed Scores, overwrite if match ID already exists
                        try:
                            sheet = self.parent.proposed_scores_sheet
                            match_id = self.match["match_id"]

                            new_row = [
                                match_id,
                                self.match["team1"],
                                self.match["team2"],
                                str(interaction.user.id),
                                self.match.get("date", f"<t:{int(datetime.utcnow().timestamp())}:f>"),
                                str(private_channel.id),
                                str(msg.id)
                            ]

                            # Look for existing row to overwrite
                            existing_rows = sheet.get_all_values()
                            row_index = None

                            for i, row in enumerate(existing_rows[1:], start=2):  # skip header, start=2
                                if row and row[0] == match_id:
                                    row_index = i
                                    break

                            if row_index:
                                sheet.update(f"A{row_index}:G{row_index}", [new_row])
                                print(f"[✏️] Overwrote Proposed Scores row for match {match_id}")
                            else:
                                sheet.append_row(new_row)
                                print(f"[📊] Appended Proposed Scores row for match {match_id}")

                        except Exception as e:
                            print(f"❌ Failed to write to Proposed Scores: {e}")

                        view.message = msg
                        self.parent.bot.add_view(view, message_id=msg.id)

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
                view = MapScoreView(self.parent, match, [])
                await interaction.response.send_message("Enter scores for Map 1, Map 2 (required) and Map 3 (optional):", view=view, ephemeral=True)

        # Main logic
        scheduled_sheet = self.spreadsheet.worksheet("Match Scheduled")
        scheduled_matches = scheduled_sheet.get_all_values()[1:]
        user_id = str(interaction.user.id)
        matches = []

        print(f"[DEBUG] Checking scheduled matches for user: {user_id}")

        for match in scheduled_matches:
            print(f"[DEBUG] Raw match row: {match}")
            if not any(match) or len(match) < 4:
                print(f"⚠️ Skipping invalid row (too short): {match}")
                continue

            match_id, team1, team2, date = match[:4]
            print(f"[DEBUG] Match {match_id}: {team1} vs {team2} on {date}")

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
                print(f"[❌] Skipping match {match_id} — user {user_id} not captain of {team1}/{team2}")
                continue

            matches.append({
                "match_id": match_id,
                "match_type": "weekly",
                "team1": proposer_team,
                "team2": opponent_team,
                "date": date,
                "proposed_datetime": datetime.utcnow().isoformat()
            })

        print(f"[FINAL DEBUG] Total matches found for user: {len(matches)}")

        if not matches:
            await interaction.response.send_message("❗ No scheduled matches found or you are not a captain.", ephemeral=True)
            return

        view = MatchSelectView(self, matches)
        await interaction.response.send_message("Select match to propose score:", view=view, ephemeral=True)

    # -------------------- JOIN TEAM --------------------
    
    @discord.ui.button(label="👥 Join Team", style=discord.ButtonStyle.blurple, custom_id="league:join_team")
    async def join_team(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)

        # Check if user is signed up
        if not self.player_signed_up(user_id):
            await interaction.response.send_message("❗ You must sign up for the league before joining a team.", ephemeral=True)
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
                if "Locked" in headers:
                    locked_col = headers.index("Locked") + 1
                    for row in self.parent_view.teams_sheet.get_all_values()[1:]:
                        if row[0].lower() == selected_team.lower():
                            if len(row) >= locked_col and row[locked_col - 1].strip().lower() == "yes":
                                await interaction.response.send_message("❗ Rosters are locked. You cannot join this team right now.", ephemeral=True)
                                return

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
                for member in team_role.members:
                    cap_role = discord.utils.get(guild.roles, name=f"Team {selected_team} Captain")
                    if cap_role and cap_role in member.roles:
                        captain = member
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

                if interaction.channel and interaction.channel.name == "team-requests":
                    await interaction.channel.delete()

            @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger, custom_id="team_join_deny")
            async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.response.send_message("❌ Request denied.", ephemeral=True)

                await interaction.message.delete()

                if interaction.channel and interaction.channel.name == "team-requests":
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
                    team_role = discord.utils.get(interaction.guild.roles, name=f"Team {team_name}")
                    captain_role = discord.utils.get(interaction.guild.roles, name=f"Team {team_name} Captain")
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

        # Check if signed up
        for idx, row in enumerate(self.players_sheet.get_all_values(), 1):
            if len(row) > 0 and row[0].strip() == user_id:
                self.players_sheet.delete_rows(idx)
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

                        captain_role = discord.utils.get(guild.roles, name=f"Team {self.team_name} Captain")
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

            def __init__(self, parent_view):
                super().__init__()
                self.parent_view = parent_view

            async def on_submit(self, modal_interaction: discord.Interaction):
                team_name = self.team_name.value.strip()

                for idx, team in enumerate(self.parent_view.teams_sheet.get_all_values(), 1):
                    if team[0].lower() == team_name.lower():

                        team_captain_raw = team[1]
                        captain_id = extract_user_id(team_captain_raw)

                        if captain_id:
                            if str(modal_interaction.user.id) != str(captain_id):
                                if str(modal_interaction.user.id) not in self.parent_view.DEV_OVERRIDE_IDS:
                                    await modal_interaction.response.send_message("❗ Only the captain or a developer can disband this team.", ephemeral=True)
                                    return

                        else:
                            # Fallback → compare display name
                            if str(modal_interaction.user.display_name) not in team_captain_raw:
                                if str(modal_interaction.user.id) not in self.parent_view.DEV_OVERRIDE_IDS:
                                    await modal_interaction.response.send_message("❗ Only the captain or a developer can disband this team.", ephemeral=True)
                                    return

                        team_role = discord.utils.get(modal_interaction.guild.roles, name=f"Team {team_name}")
                        captain_role = discord.utils.get(modal_interaction.guild.roles, name=f"Team {team_name} Captain")

                        if team_role:
                            await team_role.delete()
                        if captain_role:
                            await captain_role.delete()

                        self.parent_view.teams_sheet.delete_rows(idx)

                        await modal_interaction.response.send_message("✅ Team disbanded successfully.", ephemeral=True)
                        await self.parent_view.send_notification(f"💥 **{team_name}** has been disbanded.")
                        return

                await modal_interaction.response.send_message("❗ Team not found.", ephemeral=True)

        modal = DisbandModal(self)
        await interaction.response.send_modal(modal)











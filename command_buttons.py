import discord
from discord.ui import View, Button, Modal, TextInput
import json
import re
import pytz
from datetime import datetime, timedelta, timezone

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
        sheet = spreadsheet.add_worksheet(title=name, rows="100", cols=str(len(headers)))
        sheet.append_row(headers)
    return sheet

async def safe_send(interaction, content, ephemeral=True):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)
    except discord.NotFound:
        print("❗ Tried to send to an expired interaction.")

class LeaguePanel(View):
    def __init__(self, bot, spreadsheet, players_sheet, teams_sheet, matches_sheet, scoring_sheet, leaderboard_sheet, proposed_sheet, scheduled_sheet, weekly_matches_sheet, challenge_sheet, send_to_channel, send_notification, DEV_OVERRIDE_IDS):
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

    @discord.ui.button(label="✅ Player Signup", style=discord.ButtonStyle.blurple)
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

    @discord.ui.button(label="🏷️ Create Team", style=discord.ButtonStyle.blurple)
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

        # Check if user is already on a team
        for team in self.teams_sheet.get_all_values():
            if user_id in team:
                await interaction.response.send_message("❗ You are already on a team. Leave your team first.", ephemeral=True)
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
                    await modal_interaction.followup.send(f"✅ Team **{team_name}** created! Invite players to join your team.", ephemeral=True)
                else:
                    await modal_interaction.response.send_message(f"✅ Team **{team_name}** created! Invite players to join your team.", ephemeral=True)

                await self.parent.send_notification(f"🎉 **Team Created:** `{team_name}` by {modal_interaction.user.mention}")

        await interaction.response.send_modal(TeamNameModal(self))

    # -------------------- PROPOSE MATCH --------------------

    @discord.ui.button(label="📅 Propose Match", style=discord.ButtonStyle.green)
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

        class AcceptDenyMatchView(discord.ui.View):
            def __init__(self, parent, team_a, team_b, proposed_date_str, match_id, match_type="assigned", week_number=None, proposed_datetime=None):
                super().__init__(timeout=300)
                self.parent = parent
                self.team_a = team_a
                self.team_b = team_b
                self.proposed_date = proposed_date_str
                self.match_id = match_id
                self.match_type = match_type
                self.week_number = week_number
                self.proposed_datetime = proposed_datetime
                self.message = None
                self.channel_to_delete = None

            @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success)
            async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
                discord_ts = int(self.proposed_datetime.timestamp())
                discord_time_fmt = f"<t:{discord_ts}:f>"
                discord_relative = f"<t:{discord_ts}:R>"
                # Add to scheduled sheet
                self.parent.scheduled_sheet.append_row([self.match_id, self.team_a, self.team_b, self.proposed_date])
                # ✅ Update Proposed Date, Scheduled Date, and Status in Matches sheet
                match_sheet = get_or_create_sheet(
                    self.parent.spreadsheet,
                    "Matches",
                    ["Match ID", "Team A", "Team B", "Proposed Date", "Scheduled Date", "Status", "Winner", "Loser", "Proposed By"]
                )

                for idx, row in enumerate(match_sheet.get_all_values()[1:], start=2):
                    if row[0].strip().lower() == self.match_id.strip().lower():
                        print(f"[✅] Updating dates and status for match {self.match_id}")
                        match_sheet.update_cell(idx, 4, self.proposed_date)  # Column 4: Proposed Date
                        match_sheet.update_cell(idx, 5, self.proposed_date)  # Column 5: Scheduled Date
                        match_sheet.update_cell(idx, 6, "Scheduled")         # Column 6: Status
                        break
                else:
                    print(f"[⚠️] Match ID {self.match_id} not found in Matches sheet")

                msg = (
                    f"✅ **Match Accepted:** `{self.team_a} vs {self.team_b}`\n"
                    f"🕓 Scheduled for {discord_time_fmt} ({discord_relative})"
                )

                if isinstance(interaction.channel, discord.DMChannel):
                    await interaction.response.send_message(msg)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)

                # Notify match channel
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
                        for cell in row[1:]:
                            if "(" in cell and ")" in cell:
                                user_id = cell.split("(")[-1].split(")")[0]
                                member = interaction.guild.get_member(int(user_id))
                                if member:
                                    mentions.append(member.mention)
                        return mentions

                    mentions_a = get_mentions(self.team_a)
                    mentions_b = get_mentions(self.team_b)

                    await match_channel.send(
                        content=f"{' '.join(mentions_a)} vs {' '.join(mentions_b)}",
                        embed=embed
                    )

                # Delete original message if in channel (safe check)
                if interaction.message:
                    try:
                        await interaction.message.delete()
                    except discord.Forbidden:
                        pass  # if bot has no perms, ignore

                # Delete channel if it was a private proposed match channel
                if interaction.channel and isinstance(interaction.channel, discord.TextChannel):
                    if interaction.channel.name.startswith("proposed-match"):
                        await interaction.channel.delete()

            @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.danger)
            async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.response.send_message("❌ Match proposal declined.", ephemeral=True)

                # ✅ Remove from Proposed Matches
                proposed_rows = self.parent.proposed_sheet.get_all_values()
                for idx, row in enumerate(proposed_rows, start=1):
                    if (
                        row[0] == self.team_a and
                        row[1] == self.team_b and
                        row[3] == self.proposed_date
                    ):
                        self.parent.proposed_sheet.delete_rows(idx)
                        break

                # ✅ Remove from Challenge Matches if it was a challenge
                if self.match_type == "challenge":
                    challenge_rows = self.parent.challenge_sheet.get_all_values()
                    for idx, row in enumerate(challenge_rows, start=1):
                        if (
                            row[1] == self.team_a and
                            row[2] == self.team_b and
                            row[4] == self.proposed_date
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
                proposed_rows = self.parent.proposed_sheet.get_all_values()
                for idx, row in enumerate(proposed_rows, start=1):
                    if (
                        row[0] == self.team_a and
                        row[1] == self.team_b and
                        row[3] == self.proposed_date
                    ):
                        self.parent.proposed_sheet.delete_rows(idx)
                        break

                # remove from challenge sheet if needed
                if self.match_type == "challenge":
                    challenge_rows = self.parent.challenge_sheet.get_all_values()
                    for idx, row in enumerate(challenge_rows, start=1):
                        if (
                            row[1] == self.team_a and
                            row[2] == self.team_b and
                            row[4] == self.proposed_date
                        ):
                            self.parent.challenge_sheet.delete_rows(idx)
                            break

                # delete the message if still available
                try:
                    if self.message:
                        await self.message.delete()
                except Exception:
                    pass

                # delete the fallback channel if one was created
                try:
                    if self.channel_to_delete:
                        await self.channel_to_delete.delete()
                except Exception:
                    pass

        class ProposeOpponentView(discord.ui.View):
            def __init__(self, parent, user_team,  opponents, is_challenge,):
                super().__init__(timeout=300)
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
                super().__init__(timeout=300)
                self.parent = parent
                self.team_a = team_a
                self.team_b = team_b
                self.is_challenge = is_challenge
                self.date_time = {}

            @discord.ui.button(label="📅 Select Date (Month & Day)", style=discord.ButtonStyle.primary)
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

                view = discord.ui.View(timeout=300)
                view.add_item(select_month)
                view.add_item(select_day1)
                view.add_item(select_day2)

                await interaction.response.send_message("Select month and day:", view=view, ephemeral=True)

            @discord.ui.button(label="⏰ Select Time (Hour, Minute, AM/PM)", style=discord.ButtonStyle.primary)
            async def select_time(self, interaction: discord.Interaction, button: discord.ui.Button):
                hours = [discord.SelectOption(label=str(h), value=str(h)) for h in range(1, 13)]
                select_hour = discord.ui.Select(placeholder="Hour", options=hours)

                minutes = [discord.SelectOption(label=str(m).zfill(2), value=str(m).zfill(2)) for m in range(0, 60, 5)]
                select_minute = discord.ui.Select(placeholder="Minute", options=minutes)

                am_pm = [discord.SelectOption(label=period, value=period) for period in ["AM", "PM"]]
                select_am_pm = discord.ui.Select(placeholder="AM/PM", options=am_pm)

                async def selected(inner: discord.Interaction):
                    if not select_hour.values or not select_minute.values or not select_am_pm.values:
                        msg = "❗ Please select hour, minute, and AM/PM."
                        if not inner.response.is_done():
                            await inner.response.send_message(msg, ephemeral=True)
                        else:
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
                        view = SubmitProposalView(self.parent, self.date_time, self.team_a, self.team_b, self.is_challenge, week_number=week_number)
                    else:
                        msg = f"✅ Time selected: {self.date_time['hour']}:{self.date_time['minute']} {self.date_time['am_pm']}\nPlease finish selecting date."
                        view = None

                    if view:
                        if not inner.response.is_done():
                            await inner.response.send_message(msg, view=view, ephemeral=True)
                        else:
                            await inner.followup.send(msg, view=view, ephemeral=True)
                    else:
                        if not inner.response.is_done():
                            await inner.response.send_message(msg, ephemeral=True)
                        else:
                            await inner.followup.send(msg, ephemeral=True)

                # Bind the callback
                select_hour.callback = selected
                select_minute.callback = selected
                select_am_pm.callback = selected

                # Display dropdowns
                view = discord.ui.View(timeout=300)
                view.add_item(select_hour)
                view.add_item(select_minute)
                view.add_item(select_am_pm)

                await interaction.response.send_message("Select hour, minute, and AM/PM:", view=view, ephemeral=True)

        class SubmitProposalView(discord.ui.View):
            def __init__(self, parent, date_time, team_a, team_b, is_challenge,week_number=None):
                super().__init__(timeout=300)
                self.parent = parent
                self.date_time = date_time
                self.team_a = team_a
                self.team_b = team_b
                self.is_challenge = is_challenge
                self.week_number = week_number

            @discord.ui.button(label="✅ Submit Match Proposal", style=discord.ButtonStyle.green)
            async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
                from datetime import datetime

                try:
                    # Parse from user input
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

                    # Final timestamp for Discord (no UTC conversion)
                    discord_ts = int(naive_dt.timestamp())
                    proposed_date = f"<t:{discord_ts}:f>"
                    proposed_datetime = naive_dt

                except Exception as e:
                    await interaction.response.send_message(f"❗ Failed to build match time: {e}", ephemeral=True)
                    return

                
                # Check duplicate proposal
                existing = self.parent.proposed_sheet.get_all_values()[1:]
                for row in existing:
                    if (row[0] == self.team_a and row[1] == self.team_b) or (row[0] == self.team_b and row[1] == self.team_a):
                        await interaction.response.send_message("❗ A match proposal between these teams already exists.", ephemeral=True)
                        return

                # Save to proposed sheet
                self.parent.proposed_sheet.append_row([self.team_a, self.team_b, str(interaction.user.id), proposed_date])

                # Log to Challenge Matches if needed
                if self.is_challenge:
                    league_week_sheet = get_or_create_sheet(self.parent.spreadsheet, "LeagueWeek", ["League Week"])
                    current_week = int(league_week_sheet.get_all_values()[1][0])
                    self.parent.challenge_sheet.append_row([
                        current_week,
                        self.team_a,
                        self.team_b,
                        str(interaction.user.id),
                        proposed_date,
                        ""  # placeholder for completion date
                    ])

                # Notify captain (DM or fallback channel)
                guild = interaction.guild
                team_role = discord.utils.get(guild.roles, name=f"Team {self.team_b}")
                captain_role = discord.utils.get(guild.roles, name=f"Team {self.team_b} Captain")

                captain = None
                if team_role and captain_role:
                    for member in captain_role.members:
                        if team_role in member.roles:
                            captain = member
                            break
                
                # ✅ Generate match_id
                if self.is_challenge:
                    match_id = f"Challenge-{self.team_a[:3]}-{self.team_b[:3]}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
                else:
                    match_id = f"Week{self.week_number}-{self.team_a[:3]}-{self.team_b[:3]}"


                view = AcceptDenyMatchView(
                    self.parent,
                    self.team_a,
                    self.team_b,
                    proposed_date,
                    match_id=match_id,
                    match_type="challenge" if self.is_challenge else "assigned",
                    week_number=self.week_number if not self.is_challenge else None,
                    proposed_datetime=proposed_datetime
                )

                if captain:
                    try:
                        await captain.send(f"📨 Proposed Match from **{self.team_a}** on {proposed_date}. Accept?", view=view)
                        view.message = msg
                        view.channel_to_delete = None
                    except discord.Forbidden:
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
                        else:
                            # fallback failed, notify proposer
                            await interaction.followup.send("❗ Failed to create fallback channel. Could not deliver proposal.", ephemeral=True)
                            return  # avoid running `view.message = msg` if msg is not defined

                # Final ack to proposer
                msg = (
                    f"✅ Proposed match submitted:\n"
                    f"**{self.team_a}** vs **{self.team_b}**\n"
                    f"🕓 Scheduled for {proposed_date}"
                )
                if not interaction.response.is_done():
                    await interaction.response.send_message(msg, ephemeral=True)
                else:
                    await interaction.followup.send(msg, ephemeral=True)

        class ChallengeSearchModal(discord.ui.Modal, title="Search Challenge Opponent"):
            query = discord.ui.TextInput(label="Enter Team Name", required=True)

            def __init__(self, parent, user_team):
                super().__init__()
                self.parent = parent
                self.user_team = user_team

            async def on_submit(self, interaction: discord.Interaction):
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
                    await interaction.response.send_message(
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
                    await interaction.response.send_message("❗ No valid teams found.", ephemeral=True)
                    return

                await interaction.response.send_message("Select opponent:", view=ProposeOpponentView(self.parent, self.user_team, valid_teams, is_challenge=True), ephemeral=True)

        class SelectTypeView(discord.ui.View):
            def __init__(self, parent, user_team, assigned_opponents):
                super().__init__(timeout=300)
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

    @discord.ui.button(label="🏆 Propose Score", style=discord.ButtonStyle.blurple)
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

        class ConfirmScoreView(discord.ui.View):
            def __init__(self, parent, match, map_scores, proposer, private_channel=None):
                super().__init__(timeout=300)
                self.parent = parent
                self.match = match
                self.map_scores = map_scores
                self.proposer = proposer
                self.private_channel = private_channel  # ← allows private channel deletion
                self.message = None
                self.channel_to_delete = private_channel

            @discord.ui.button(label="✅ Accept Scores", style=discord.ButtonStyle.green)
            async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.response.defer(ephemeral=True)
                from match import update_team_rating 
                # Calculate totals and maps won
                map_scores = [(m["gamemode"], int(m["team1_score"]), int(m["team2_score"])) for m in self.map_scores]
                total_a = sum([score[1] for score in map_scores])  # Team A scores
                total_b = sum([score[2] for score in map_scores])  # Team B scores
                maps_won_a = sum(1 for score in map_scores if score[1] > score[2])
                maps_won_b = sum(1 for score in map_scores if score[2] > score[1])


                if total_a > total_b:
                    winner = self.match["team1"]
                elif total_b > total_a:
                    winner = self.match["team2"]
                else:
                    if maps_won_a > maps_won_b:
                        winner = self.match["team1"]
                    elif maps_won_b > maps_won_a:
                        winner = self.match["team2"]
                    else:
                        winner = "Tie"

                # Update leaderboard
                elo_win = self.parent.bot.config.get("elo_win_points", 25)
                elo_loss = self.parent.bot.config.get("elo_loss_points", -25)

                if winner != "Tie":
                    update_team_rating(self.parent.leaderboard_sheet, winner, True, elo_win, elo_loss)
                    loser = self.match["team2"] if winner == self.match["team1"] else self.match["team1"]
                    update_team_rating(self.parent.leaderboard_sheet, loser, False, elo_win, elo_loss)

                # Append to scoring sheet (NEW WAY)
                self.parent.scoring_sheet.append_row([
                    self.match.get("match_id", "challenge"),
                    self.match["team1"],
                    self.match["team2"],
                    map_scores[0][0], map_scores[0][1], map_scores[0][2],  # Map 1: Mode, A, B
                    map_scores[1][0], map_scores[1][1], map_scores[1][2],  # Map 2: Mode, A, B
                    map_scores[2][0] if len(map_scores) > 2 else "",
                    map_scores[2][1] if len(map_scores) > 2 else "",
                    map_scores[2][2] if len(map_scores) > 2 else "",
                    total_a,
                    total_b,
                    maps_won_a,
                    maps_won_b,
                    winner
                ])

                # ✅ REMOVE MATCH FROM WEEKLY OR PROPOSED SHEET
                from datetime import datetime

                # Data
                league_week_sheet = get_or_create_sheet(self.parent.spreadsheet, "LeagueWeek", ["League Week"])
                week_number = int(league_week_sheet.get_all_values()[1][0])
                match_id = self.match.get("match_id")
                team_a = self.match["team1"]
                team_b = self.match["team2"]
                winner = winner
                total_a = total_a
                total_b = total_b
                maps_won_a = maps_won_a
                maps_won_b = maps_won_b

                proposed_date = ""
                scheduled_date = ""

                # ✅ Update Match Sheet status to "Finished"
                if match_id:
                    match_sheet = get_or_create_sheet(
                        self.parent.spreadsheet,
                        "Matches",
                        ["Match ID", "Team A", "Team B", "Proposed Date", "Scheduled Date", "Status", "Winner", "Loser", "Proposed By"]
                    )

                    for idx, row in enumerate(match_sheet.get_all_values()[1:], start=2):
                        if row[0].strip().lower() == str(match_id).strip().lower():
                            print(f"[✅] Updating match {match_id} status to Finished")
                            match_sheet.update_cell(idx, 6, "Finished")  # Column 6 = "Status"
                            match_sheet.update_cell(idx, 7, winner if winner != "Tie" else "")  # Winner column
                            match_sheet.update_cell(idx, 8, loser if winner != "Tie" else "")   # Loser column
                            break
                        else:
                            print(f"[⚠️] match_id {match_id} not found in Matches sheet")

                # --- Find proposed match
                for idx, row in enumerate(self.parent.proposed_sheet.get_all_values()[1:], start=2):
                    if (row[0] == team_a and row[1] == team_b) or (row[0] == team_b and row[1] == team_a):
                        proposed_date = row[3]
                        self.parent.proposed_sheet.delete_rows(idx)
                        break

                # --- Find scheduled match
                for idx, row in enumerate(self.parent.scheduled_sheet.get_all_values()[1:], start=2):
                    if (row[0] == team_a and row[1] == team_b) or (row[0] == team_b and row[1] == team_a):
                        scheduled_date = row[2]
                        self.parent.scheduled_sheet.delete_rows(idx)
                        break

                # --- Clean up Weekly Matches
                for idx, row in enumerate(self.parent.weekly_matches_sheet.get_all_values()[1:], start=2):
                    if (row[1] == team_a and row[2] == team_b) or (row[1] == team_b and row[2] == team_a):
                        self.parent.weekly_matches_sheet.delete_rows(idx)
                        break

                # --- Prepare map scores
                map1 = self.map_scores[0] if len(self.map_scores) > 0 else {"gamemode": "", "team1_score": "", "team2_score": ""}
                map2 = self.map_scores[1] if len(self.map_scores) > 1 else {"gamemode": "", "team1_score": "", "team2_score": ""}
                map3 = self.map_scores[2] if len(self.map_scores) > 2 else {"gamemode": "", "team1_score": "", "team2_score": ""}

                # --- Append to Match History
                match_history_sheet = get_or_create_sheet(
                    self.parent.spreadsheet,
                    "Match History",
                    [
                        "Week", "Match ID", "Team A", "Team B", "Proposed Date", "Scheduled Date",
                        "Map 1 Mode", "Map 1 A", "Map 1 B",
                        "Map 2 Mode", "Map 2 A", "Map 2 B",
                        "Map 3 Mode", "Map 3 A", "Map 3 B",
                        "Total A", "Total B", "Maps Won A", "Maps Won B", "Winner"
                    ]
                )

                match_history_sheet.append_row([
                    week_number,
                    match_id,
                    team_a,
                    team_b,
                    proposed_date,
                    scheduled_date,
                    map1["gamemode"], map1["team1_score"], map1["team2_score"],
                    map2["gamemode"], map2["team1_score"], map2["team2_score"],
                    map3["gamemode"], map3["team1_score"], map3["team2_score"],
                    total_a,
                    total_b,
                    maps_won_a,
                    maps_won_b,
                    winner
                ])

                # ✅ Log completed challenge match (for enforcement)
                if self.match.get("is_challenge"):
                    from datetime import datetime
                    league_week_sheet = get_or_create_sheet(self.parent.spreadsheet, "LeagueWeek", ["League Week"])
                    week_number = int(league_week_sheet.get_all_values()[1][0])

                    challenge_sheet = get_or_create_sheet(
                        self.parent.spreadsheet,
                        "Challenge Matches",
                        ["Week", "Team A", "Team B", "Proposer ID", "Completion Date"]
                    )

                    challenge_sheet.append_row([
                        week_number,
                        self.match["team1"],
                        self.match["team2"],
                        str(self.proposer.id),
                        datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                    ])

                
                # Announce final result to score/results channel
                score_channel_id = self.parent.bot.config.get("score_channel_id")
                score_channel = self.parent.bot.get_channel(score_channel_id)

                if score_channel:
                    result_embed = discord.Embed(
                        title="🏆 Final Match Result",
                        description=f"**{self.match['team1']}** vs **{self.match['team2']}**",
                        color=discord.Color.gold()
                    )
                    for i, s in enumerate(self.map_scores, 1):
                        result_embed.add_field(
                            name=f"Map {i} ({s['gamemode']})",
                            value=f"{self.match['team1']} {s['team1_score']} - {s['team2_score']} {self.match['team2']}",
                            inline=False
                        )
                    result_embed.add_field(name="Winner", value=winner, inline=False)
                    await score_channel.send(embed=result_embed)

                # 🛎️ Ping both teams in the same message
                def get_mentions(team_name):
                    row = next((r for r in self.parent.teams_sheet.get_all_values() if r[0] == team_name), [])
                    mentions = []
                    for cell in row[1:]:
                        if "(" in cell and ")" in cell:
                            user_id = cell.split("(")[-1].split(")")[0]
                            member = interaction.guild.get_member(int(user_id))
                            if member:
                                mentions.append(member.mention)
                    return mentions

                mentions_a = get_mentions(self.match["team1"])
                mentions_b = get_mentions(self.match["team2"])

                # Safely format match time (UTC fallback)
                match_time = self.match.get("proposed_datetime")
                if isinstance(match_time, str):
                    try:
                        match_time = datetime.fromisoformat(match_time)
                    except:
                        match_time = None

                if not match_time:
                    match_time = datetime.utcnow()

                formatted_dt = match_time.strftime("%Y-%m-%d %I:%M %p %Z").strip()
                discord_ts = int(match_time.replace(tzinfo=timezone.utc).timestamp())

                await score_channel.send(
                    f"🏁 Final score submitted for **{self.match['team1']}** vs **{self.match['team2']}**!\n"
                    f"{' '.join(mentions_a)} vs {' '.join(mentions_b)}\n"
                    f"🕒 **Match Time:** <t:{discord_ts}:F> (<t:{discord_ts}:R>)"
                )

                # Notify and cleanup
                await safe_send(interaction, "✅ Score accepted and finalized!\n✅ Scores accepted and saved!", ephemeral=True)

                # DM proposer (safe)
                try:
                    await self.proposer.send("✅ Your proposed match scores have been accepted and finalized.")
                except discord.Forbidden:
                    pass

                # Delete the message (safe)
                try:
                    await interaction.message.delete()
                except (discord.NotFound, AttributeError):
                    pass

                # Delete private channel or proposed-score channel
                if self.private_channel:
                    await self.private_channel.delete()
                elif interaction.channel and interaction.channel.name.startswith("proposed-score"):
                    await interaction.channel.delete()

            @discord.ui.button(label="❌ Deny Scores", style=discord.ButtonStyle.red)
            async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.response.send_message("❌ Scores denied.", ephemeral=True)

                # DM proposer (safe)
                try:
                    await self.proposer.send("❌ Your proposed match scores were denied.")
                except discord.Forbidden:
                    pass

                await interaction.message.delete()

                # Delete private channel or proposed-score channel
                if self.private_channel:
                    await self.private_channel.delete()
                elif interaction.channel and interaction.channel.name.startswith("proposed-score"):
                    await interaction.channel.delete()

            async def on_timeout(self):
                try:
                    await self.proposer.send("⏳ Your proposed scores expired due to no response.")
                except:
                    pass

                # Delete private channel or message
                try:
                    if self.private_channel:
                        await self.private_channel.delete()
                except:
                    pass

                try:
                    if self.message:
                        await self.message.delete()
                except:
                    pass

                # Remove from proposed sheet
                for idx, row in enumerate(self.parent.proposed_sheet.get_all_values()[1:], start=2):
                    if (
                        row[0] == self.match["team1"] and row[1] == self.match["team2"]
                    ) or (
                        row[0] == self.match["team2"] and row[1] == self.match["team1"]
                    ):
                        self.parent.proposed_sheet.delete_rows(idx)
                        break

        class MapScoreModal(discord.ui.Modal, title="Enter Map Score"):
            def __init__(self, parent, match, map_scores, map_number, gamemode):
                super().__init__()
                self.parent = parent
                self.match = match
                self.map_scores = map_scores
                self.map_number = map_number
                self.gamemode = gamemode

                if gamemode == "Payload":
                    self.team1_score = discord.ui.TextInput(label=f"{self.match['team1']} Rounds Won (Best of 3)", required=True)
                    self.team2_score = discord.ui.TextInput(label=f"{self.match['team2']} Rounds Won (Best of 3)", required=True)
                else:
                    self.team1_score = discord.ui.TextInput(label=f"{self.match['team1']} Rounds Won (Best of 3)", required=True)
                    self.team2_score = discord.ui.TextInput(label=f"{self.match['team2']} Rounds Won (Best of 3)", required=True)

                self.add_item(self.team1_score)
                self.add_item(self.team2_score)

            async def on_submit(self, interaction: discord.Interaction):
                self.map_scores.append({
                    "gamemode": self.gamemode,
                    "team1_score": self.team1_score.value,
                    "team2_score": self.team2_score.value
                })

                view = MapScoreView(self.parent, self.match, self.map_scores)
                await interaction.response.send_message("✅ Map score saved!", view=view, ephemeral=True)

        class MapGamemodeSelectView(discord.ui.View):
            def __init__(self, parent, match, map_scores, map_number):
                super().__init__(timeout=300)
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
                super().__init__(timeout=600)
                self.parent = parent
                self.match = match
                self.map_scores = map_scores

            @discord.ui.button(label="Map 1: Enter Score", style=discord.ButtonStyle.green)
            async def map1(self, interaction: discord.Interaction, button: discord.ui.Button):
                view = MapGamemodeSelectView(self.parent, self.match, self.map_scores, 1)
                await interaction.response.send_message("Select gamemode for Map 1:", view=view, ephemeral=True)

            @discord.ui.button(label="Map 2: Enter Score", style=discord.ButtonStyle.green)
            async def map2(self, interaction: discord.Interaction, button: discord.ui.Button):
                view = MapGamemodeSelectView(self.parent, self.match, self.map_scores, 2)
                await interaction.response.send_message("Select gamemode for Map 2:", view=view, ephemeral=True)

            @discord.ui.button(label="Map 3 (Optional): Enter Score", style=discord.ButtonStyle.blurple)
            async def map3(self, interaction: discord.Interaction, button: discord.ui.Button):
                view = MapGamemodeSelectView(self.parent, self.match, self.map_scores, 3)
                await interaction.response.send_message("Select gamemode for Map 3:", view=view, ephemeral=True)

            @discord.ui.button(label="✅ Submit All Maps", style=discord.ButtonStyle.success)
            async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
                if len(self.map_scores) < 2:
                    await interaction.response.send_message("❗ Please enter Map 1 and Map 2 first.", ephemeral=True)
                    return

                await interaction.response.send_message("✅ Proposed scores submitted. Waiting for opponent confirmation...", ephemeral=True)

                opponent_team = self.match["team2"] if interaction.user in [m for m in discord.utils.get(interaction.guild.roles, name=f"Team {self.match['team1']} Captain").members] else self.match["team1"]
                opponent_role = discord.utils.get(interaction.guild.roles, name=f"Team {opponent_team} Captain")
                opponent_captain = opponent_role.members[0] if opponent_role and opponent_role.members else None

                embed = discord.Embed(title="Proposed Match Scores", description=f"**{self.match['team1']}** vs **{self.match['team2']}**")
                for i, s in enumerate(self.map_scores, 1):
                    embed.add_field(name=f"Map {i} ({s['gamemode']})", value=f"{self.match['team1']} {s['team1_score']} - {s['team2_score']} {self.match['team2']}", inline=False)

                if opponent_captain:
                    try:
                        await opponent_captain.send(embed=embed, view=ConfirmScoreView(self.parent, self.match, self.map_scores, interaction.user))
                    except discord.Forbidden:
                        try:
                            category_id = self.parent.config.get("fallback_category_id")

                            private_channel = await create_private_channel(
                                interaction.guild,
                                int(category_id),
                                f"proposed-score-{self.match['team1']}-vs-{self.match['team2']}",
                                [interaction.user, opponent_captain]
                            )

                            if private_channel:
                                await private_channel.send(
                                    f"{opponent_captain.mention} 📨 Proposed Match Scores from **{self.match['team1']}**.",
                                    embed=embed,
                                    view=ConfirmScoreView(self.parent, self.match, self.map_scores, interaction.user, private_channel)
                                )
                                view.private_channel = private_channel
                            else:
                                await interaction.followup.send("❌ Failed to create fallback channel.", ephemeral=True)

                        except Exception as e:
                            await interaction.followup.send(f"❌ Error creating fallback channel: {e}", ephemeral=True)

        class MatchSelectView(discord.ui.View):
            def __init__(self, parent, matches):
                super().__init__(timeout=600)
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
        scheduled_matches = self.scheduled_sheet.get_all_values()[1:]
        user_id = str(interaction.user.id)

        matches = []
        for match in scheduled_matches:
            if not any(match) or len(match) < 4:
                print(f"⚠️ Skipping invalid row (too short): {match}")
                continue

            match_id = match[0]
            team1 = match[1]
            team2 = match[2]
            date = match[3]

            team1_role = discord.utils.get(interaction.guild.roles, name=f"Team {team1} Captain")
            team2_role = discord.utils.get(interaction.guild.roles, name=f"Team {team2} Captain")

            if (team1_role and user_id in [str(m.id) for m in team1_role.members]) or (team2_role and user_id in [str(m.id) for m in team2_role.members]):
                matches.append({
                    "match_id": match_id,
                    "match_type": "weekly",
                    "team1": team1,
                    "team2": team2,
                    "date": date,
                    "proposed_datetime": datetime.utcnow().isoformat()
                })

        if not matches:
            await interaction.response.send_message("❗ No scheduled matches found or you are not a captain.", ephemeral=True)
            return

        view = MatchSelectView(self, matches)
        await interaction.response.send_message("Select match to propose score:", view=view, ephemeral=True)

    # -------------------- JOIN TEAM --------------------
    
    @discord.ui.button(label="👥 Join Team", style=discord.ButtonStyle.blurple)
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
                super().__init__(timeout=300)
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

                for row in self.parent_view.teams_sheet.get_all_values():
                    for cell in row[1:7]:
                        if cell.strip() == f"{self.user.display_name} ({self.user.id})":
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
                    fallback_channel = discord.utils.get(guild.text_channels, name="team-requests")
                    if fallback_channel is None:
                        overwrites = {
                            guild.default_role: discord.PermissionOverwrite(read_messages=False),
                            captain: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
                        }
                        fallback_channel = await guild.create_text_channel("team-requests", overwrites=overwrites)
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
                super().__init__(timeout=300)
                self.parent_view = parent_view
                self.team_name = team_name
                self.invitee = invitee
                self.guild_id = guild_id

            @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success)
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

            @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger)
            async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.response.send_message("❌ Request denied.", ephemeral=True)

                await interaction.message.delete()

                if interaction.channel and interaction.channel.name == "team-requests":
                    await interaction.channel.delete()

        await interaction.response.send_modal(TeamSearchModal(self))

    # -------------------- LEAVE TEAM --------------------

    @discord.ui.button(label="🚪 Leave Team", style=discord.ButtonStyle.red)
    async def leave_team(self, interaction: discord.Interaction, button: discord.ui.Button):
        username_id = f"{interaction.user.display_name} ({interaction.user.id})"

        for idx, team in enumerate(self.teams_sheet.get_all_values(), 1):
            if username_id in team:
                team_name = team[0]

                if username_id == team[1]:
                    await interaction.response.send_message("❗ You are the captain. Promote or disband first.", ephemeral=True)
                    return

                # Remove from sheet
                self.teams_sheet.update_cell(idx, team.index(username_id) + 1, "")

                # Remove team role
                team_role = discord.utils.get(interaction.guild.roles, name=f"Team {team_name}")
                if team_role:
                    try:
                        await interaction.user.remove_roles(team_role)
                    except discord.Forbidden:
                        print(f"❗ Failed to remove role from {interaction.user.display_name}")

                await interaction.response.send_message(f"✅ You left **{team_name}**.", ephemeral=True)

                # Send league-wide notification
                try:
                    await self.send_notification(f"🚪 {interaction.user.mention} has left **{team_name}**.")
                except Exception as e:
                    print(f"❗ Failed to send notification: {e}")

                return

        await interaction.response.send_message("You are not on a team.", ephemeral=True)

    # -------------------- UNSIGNUP --------------------

    @discord.ui.button(label="❌ Unsignup", style=discord.ButtonStyle.red)
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

    @discord.ui.button(label="⭐ Promote Player", style=discord.ButtonStyle.green)
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
                        super().__init__(timeout=300)
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
                            await old_captain_member.remove_roles(captain_role)
                            await new_captain_member.add_roles(captain_role)

                        # Update sheet: move old captain to player spot and new captain to spot 2
                        row = self.parent.teams_sheet.row_values(self.team_idx)
                        new_row = [self.team_name, f"{new_captain_member.display_name} ({new_captain_member.id})"]
                        added = False

                        new_captain_str = f"{new_captain_member.display_name} ({new_captain_member.id})"
                        for val in row[1:]:
                            if val in [self.old_captain, new_captain_str]:
                                continue  # Skip old and new captain from old positions
                            if not added and len(new_row) < 7:
                                new_row.append(self.old_captain)  # Reinsert old captain as a player
                                added = True
                            if val:
                                new_row.append(val)

                        while len(new_row) < 7:
                            new_row.append("")

                        self.parent.teams_sheet.update(f"A{self.team_idx}:G{self.team_idx}", [new_row])

                        await select_interaction.response.send_message(f"✅ {new_captain_member.mention} is now the captain of **{self.team_name}**!", ephemeral=True)
                        await self.parent.send_notification(f"⭐ {new_captain_member.mention} has been promoted to **Captain of {self.team_name}**.")

                await interaction.response.send_message("Select player to promote to captain:", view=PromoteSelect(self, team_name, username_id, idx), ephemeral=True)
                return

        await interaction.response.send_message("❗ You are not a captain or on a team.", ephemeral=True)

    # -------------------- DISBAND TEAM --------------------

    @discord.ui.button(label="❗ Disband Team", style=discord.ButtonStyle.red)
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
                            # Compare by ID
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











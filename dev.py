import discord
from discord.ui import View, Modal, TextInput
from discord import app_commands, Interaction, PermissionOverwrite
from discord.utils import get
import json

with open("config.json") as f:
    config = json.load(f)

def get_or_create_sheet(spreadsheet, name, headers):
    try:
        return spreadsheet.worksheet(name)
    except:
        sheet = spreadsheet.add_worksheet(title=name, rows="100", cols=str(len(headers)))
        sheet.append_row(headers)
        return sheet

async def check_dev(interaction, dev_ids):
    if interaction.user.id in dev_ids or any(role.id in dev_ids for role in interaction.user.roles):
        return True
    await interaction.response.send_message("❗ No permission.", ephemeral=True)
    return False

# ✅✅✅ UNIVERSAL SAFE VIEW BASE (TRUE SAFE SEND)
class SafeView(View):
    async def safe_send(self, interaction, content):
        if interaction.is_expired() or interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)

class CloseChannelView(discord.ui.View):
            def __init__(self, author_id):
                super().__init__(timeout=None)
                self.author_id = author_id

            @discord.ui.button(label="🎬 Close Channel", style=discord.ButtonStyle.red, custom_id="cast:close_channel")
            async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != self.author_id and not interaction.user.guild_permissions.manage_channels:
                    await interaction.response.send_message("❗ Only the caster or a mod can close this channel.", ephemeral=True)
                    return
                await interaction.response.send_message("🧹 Channel closing...", ephemeral=True)
                await interaction.channel.delete()

@app_commands.command(name="cast", description="Create a private caster channel for a match")
@app_commands.describe(match_id="Match ID (e.g. Week1-ABC-XYZ)")
async def cast(interaction: Interaction, match_id: str):
    await interaction.response.defer(ephemeral=True)

    dev_ids = config.get("dev_override_ids", [])
    caster_roles = config.get("caster_role_ids", [])
    fallback_category_id = config.get("fallback_category_id")

    if (
        interaction.user.id not in dev_ids and
        not any(role.id in caster_roles for role in interaction.user.roles)
    ):
        await interaction.followup.send("❗ You do not have permission to use this command.", ephemeral=True)
        return

    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open(config["sheet_name"])
        matches_sheet = spreadsheet.worksheet("Matches")
        teams_sheet = spreadsheet.worksheet("Teams")
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to access spreadsheet: {e}", ephemeral=True)
        return

    match_row = next((row for row in matches_sheet.get_all_values() if row[0].strip() == match_id.strip()), None)
    if not match_row:
        await interaction.followup.send(f"❌ Match ID `{match_id}` not found.", ephemeral=True)
        return

    team_a, team_b = match_row[1], match_row[2]

    def get_team_members(team_name):
        row = next((r for r in teams_sheet.get_all_values() if r[0].strip() == team_name.strip()), None)
        return [
            int(cell.split("(")[-1].split(")")[0])
            for cell in row[1:] if "(" in cell and ")" in cell
        ] if row else []

    guild = interaction.guild
    overwrites = {
        guild.default_role: PermissionOverwrite(view_channel=False),
        guild.me: PermissionOverwrite(view_channel=True, manage_channels=True)
    }

    for uid in get_team_members(team_a) + get_team_members(team_b) + dev_ids:
        member = guild.get_member(uid)
        if member:
            overwrites[member] = PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    for role_id in caster_roles:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    category = guild.get_channel(int(fallback_category_id))
    channel_name = f"cast-{team_a.lower()}-vs-{team_b.lower()}".replace(" ", "-")

    try:
        channel = await guild.create_text_channel(channel_name, overwrites=overwrites, category=category)
        await channel.send(
            f"🎥 Caster channel for **{team_a}** vs **{team_b}** created.",
            view=CloseChannelView(interaction.user.id)
        )
        # 🔔 Notify captains/co-captains with info message
        def get_mention_list(team_name):
            row = next((r for r in teams_sheet.get_all_values() if r[0].strip() == team_name.strip()), None)
            if not row:
                return ""

            mentions = set()

            # Player slots (columns B to G → indices 1–6)
            for cell in row[1:7]:
                if "(" in cell and ")" in cell:
                    try:
                        user_id = cell.split("(")[-1].split(")")[0]
                        mentions.add(f"<@{user_id}>")
                    except Exception:
                        continue

            # Co-captain column (I → index 8)
            if len(row) > 8 and "(" in row[8] and ")" in row[8]:
                try:
                    user_id = row[8].split("(")[-1].split(")")[0]
                    mentions.add(f"<@{user_id}>")
                except Exception:
                    pass

            return " ".join(mentions)

        team_a_mentions = get_mention_list(team_a)
        team_b_mentions = get_mention_list(team_b)

        info_note = (
            f"{team_a_mentions} {team_b_mentions}\n\n"
            f"🎥 This match is being **casted live**.\n\n"
            f"📌 Please post your **Spark/Taxi links** here before the match starts.\n"
            f"🛑 **Do not start the match immediately.** Wait for the caster(s) to queue in and give the go-ahead.\n"
            f"⏱️ There may be a short delay if stream setup or scene testing is needed.\n\n"
            f"👍 Thanks for your patience and for helping us make the stream great!"
        )

        await channel.send(info_note)

        await interaction.followup.send(f"✅ Caster channel created: {channel.mention}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to create channel: {e}", ephemeral=True)

# -------------------- MATCH TOOLS --------------------

class DevPanel_Match(SafeView):
    def __init__(self, bot, spreadsheet, dev_ids, send_notification):
        super().__init__(timeout=None)
        self.bot = bot
        self.spreadsheet = spreadsheet
        self.dev_ids = dev_ids
        self.send_notification = send_notification

    async def interaction_check(self, interaction):
        return await check_dev(interaction, self.dev_ids)

    @discord.ui.button(label="📥 Force Weekly Matchups", style=discord.ButtonStyle.red, custom_id="dev:force_weekly_matchups")
    async def force_weekly(self, interaction, button):
        class ForceWeeklyMatchups(Modal, title="Force Weekly Matchups"):
            week = TextInput(label="League Week", required=True)

            def __init__(self, parent):
                super().__init__()
                self.parent = parent

            async def on_submit(self, i):
                import match
                from datetime import datetime

                try:
                    league_week = int(self.week.value)
                except ValueError:
                    await self.parent.safe_send(i, "❗ Please enter a valid League Week number (e.g. 1, 2, 3).", ephemeral=True)
                    return

                # ✅ Save to LeagueWeek sheet
                league_week_sheet = get_or_create_sheet(
                    self.parent.spreadsheet,
                    "LeagueWeek",
                    ["League Week"]
                )

                try:
                    league_week_sheet.update_cell(2, 1, league_week)
                except Exception as e:
                    await self.parent.safe_send(i, f"❗ Failed to update LeagueWeek sheet: {e}", ephemeral=True)
                    return

                await self.parent.safe_send(i, f"✅ League Week set to {league_week}. Generating matchups...")
                await match.generate_weekly_matches(i, self.parent.spreadsheet, league_week, force=True)

        await interaction.response.send_modal(ForceWeeklyMatchups(self))

    @discord.ui.button(label="📢 Announce Unscheduled Matches", style=discord.ButtonStyle.green, custom_id="dev:announce_unscheduled_matches")
    async def announce_unscheduled(self, interaction, button):
        await interaction.response.defer(ephemeral=True)

        with open("config.json") as f:
            config = json.load(f)

        match_channel = interaction.guild.get_channel(int(config.get("match_channel_id")))
        match_sheet = get_or_create_sheet(self.spreadsheet, "Matches", ["Match ID", "Team A", "Team B", "Proposed Date", "Scheduled Date", "Status", "Winner", "Loser", "Proposed By"])
        team_sheet = get_or_create_sheet(self.spreadsheet, "Teams", ["Team Name", "Captain", "Player 2", "Player 3", "Player 4", "Player 5", "Player 6"])

        # Helper to get mentions for a team
        def get_mentions(team_name):
            row = next((r for r in team_sheet.get_all_values() if r[0] == team_name), None)
            if not row:
                return ""
            mentions = []
            for cell in row[1:]:
                if "(" in cell and ")" in cell:
                    user_id = cell.split("(")[-1].split(")")[0]
                    member = interaction.guild.get_member(int(user_id))
                    if member:
                        mentions.append(member.mention)
            return " ".join(mentions)

        seen_matches = set()

        for row in match_sheet.get_all_values()[1:]:
            team_a, team_b = row[1], row[2]
            match_key = tuple(sorted([team_a, team_b]))  # ensures A vs B == B vs A

            if match_key in seen_matches:
                continue  # skip duplicates
            seen_matches.add(match_key)

            scheduled_date = row[4]
            status = row[5]
            if scheduled_date in ["", "TBD"] and status not in ["Finished", "Cancelled", "Forfeited"]:
                mentions_a = get_mentions(team_a)
                mentions_b = get_mentions(team_b)
                await match_channel.send(
                    f"📢 **Unscheduled Match:** {team_a} vs {team_b}\n"
                    f"{mentions_a} vs {mentions_b}"
                )

        await interaction.followup.send("✅ Announced unscheduled matches with pings.", ephemeral=True)

    @discord.ui.button(label="📅 Force Schedule Match", style=discord.ButtonStyle.blurple, custom_id="dev:force_schedule_match")
    async def force_schedule(self, interaction, button):
        class ForceScheduleModal(Modal, title="Force Schedule Match"):
            match_id = TextInput(label="Match ID (existing or new)", placeholder="e.g. Week3-M002 or 42")
            date = TextInput(label="Date and Time", placeholder="e.g. July 20 @ 7PM EST or TBD")

            def __init__(self, parent):
                super().__init__()
                self.parent = parent

            async def on_submit(self, i):
                match_id = self.match_id.value.strip()
                scheduled_date = self.date.value.strip()

                matches_sheet = get_or_create_sheet(
                    self.parent.spreadsheet,
                    "Matches",
                    ["Match ID", "Team A", "Team B", "Proposed Date", "Scheduled Date", "Status", "Winner", "Loser", "Proposed By"]
                )
                weekly_sheet = get_or_create_sheet(
                    self.parent.spreadsheet,
                    "Weekly Matches",
                    ["Week", "Team A", "Team B", "Match ID", "Scheduled Date"]
                )

                rows = matches_sheet.get_all_values()
                header = rows[0]
                match_found = False

                for idx, row in enumerate(rows[1:], start=2):
                    if row[0].strip() == match_id:
                        # Update existing match
                        matches_sheet.update_cell(idx, 5, scheduled_date)  # Scheduled Date
                        matches_sheet.update_cell(idx, 6, "Scheduled")     # Status
                        match_found = True

                        # Update weekly sheet if present
                        for w_row in weekly_sheet.get_all_values()[1:]:
                            if w_row[3] == match_id:
                                w_idx = weekly_sheet.get_all_values().index(w_row) + 1
                                weekly_sheet.update_cell(w_idx, 5, scheduled_date)
                                break
                        break

                if not match_found:
                    # Create a new match entry manually
                    await self.parent.safe_send(i, "❗ Match ID not found. Creating as new manual match.")

                    # Ask for team names in follow-up (or create with placeholders)
                    matches_sheet.append_row([match_id, "TBD", "TBD", "TBD", scheduled_date, "Manual", "", "", "System"])
                    weekly_sheet.append_row(["Manual", "TBD", "TBD", match_id, scheduled_date])
                
                # 👥 Ping both teams if available
                teams_sheet = self.parent.teams_sheet
                team_a, team_b = None, None

                # Try to get team names from updated row
                for row in matches_sheet.get_all_values():
                    if row[0].strip() == match_id:
                        team_a = row[1]
                        team_b = row[2]
                        break

                def get_mentions(team_name):
                    row = next((r for r in teams_sheet.get_all_values() if r[0] == team_name), [])
                    mentions = [f"<@{p.split('(')[-1].split(')')[0]}>" for p in row[1:] if "(" in p and ")" in p]
                    return " ".join(mentions) if mentions else team_name

                mention_a = get_mentions(team_a) if team_a else "Unknown Team A"
                mention_b = get_mentions(team_b) if team_b else "Unknown Team B"

                channel_id = self.parent.config.get("scheduled_channel_id")
                channel = self.parent.bot.get_channel(int(channel_id))

                if channel:
                    await channel.send(
                        f"📢 **Match Scheduled: `{match_id}`**\n"
                        f"📅 **Date:** {scheduled_date}\n"
                        f"👤 _Match manually scheduled by League Mod {i.user.mention}_\n"
                        f"🔹 {team_a} vs {team_b}\n"
                        f"{mention_a} {mention_b}"
                    )

                await self.parent.safe_send(i, f"✅ Match `{match_id}` scheduled for **{scheduled_date}**.", ephemeral=True)

        await interaction.response.send_modal(ForceScheduleModal(self))


#    @discord.ui.button(label="♻️ Reset Weekly Matches", style=discord.ButtonStyle.red, custom_id="dev:reset_weekly_matches", disabled=True)
    async def reset_weekly(self, interaction, button):
        sheet = get_or_create_sheet(self.spreadsheet, "Weekly Matches", ["Week","Team A","Team B","Match ID","Scheduled Date"])
        sheet.clear(); sheet.append_row(["Week","Team A","Team B","Match ID","Scheduled Date"])
        await self.safe_send(interaction, "✅ Reset weekly matches.")

# -------------------- SCORE TOOLS --------------------

class DevPanel_Score(SafeView):
    def __init__(self, bot, spreadsheet, dev_ids, send_notification):
        super().__init__(timeout=None)
        self.bot = bot
        self.spreadsheet = spreadsheet
        self.dev_ids = dev_ids
        self.send_notification = send_notification

    async def interaction_check(self, interaction):
        return await check_dev(interaction, self.dev_ids)

    async def generic_clear(self, interaction, sheet_name):
        sheet = get_or_create_sheet(self.spreadsheet, sheet_name, [])
        rows = sheet.get_all_values()[1:]
        options = []
        for idx, row in enumerate(rows, 2):
            label = " | ".join(row)
            if len(label) > 100:
                label = label[:97] + "..."
            options.append(discord.SelectOption(label=label, value=str(idx)))
        if not options:
            await self.safe_send(interaction, "❗ No data found.")
            return

        class Confirm(View):
            @discord.ui.select(placeholder="Select to delete", options=options)
            async def select(self, i, select):
                sheet.delete_rows(int(select.values[0]))
                await self.parent.safe_send(i, "✅ Deleted.")

        view = Confirm()
        view.parent = self
        await interaction.response.send_message("Select to delete:", view=view, ephemeral=True)

    @discord.ui.button(label="❌ Clear Proposed Match", style=discord.ButtonStyle.primary, custom_id="dev:clear_match", disabled=False)
    async def clear_proposed(self, interaction: discord.Interaction, button: discord.ui.Button):
        class MatchIDModal(Modal, title="Clear Proposed Match"):
            match_id = TextInput(label="Enter Match ID", placeholder="E.g., Week1-M002, Challenge2-M001", required=True)

            def __init__(self, parent_view):
                super().__init__()
                self.parent_view = parent_view  # Store the DevPanel_Score view

            async def on_submit(self, interaction: discord.Interaction):
                match_id_value = self.match_id.value.strip()

                try:
                    sheet = self.parent_view.spreadsheet.worksheet("Match Proposed")
                    rows = sheet.get_all_values()

                    matched_rows = []
                    for i, row in enumerate(rows):
                        if len(row) >= 6 and row[0].strip() == match_id_value:  # Column A = Match ID
                            matched_rows.append((i + 1, row[5].strip()))  # row index, Channel ID

                    if not matched_rows:
                        await interaction.response.send_message(f"❗ No proposed match with ID `{match_id_value}` found.", ephemeral=True)
                        return

                    # Delete fallback thread (once)
                    first_channel_id = matched_rows[0][1]
                    if first_channel_id:
                        try:
                            channel = interaction.guild.get_channel(int(first_channel_id))
                            if channel and channel.permissions_for(interaction.guild.me).manage_channels:
                                await channel.delete()
                        except Exception as e:
                            print(f"⚠️ Failed to delete fallback channel: {e}")

                    for row_index, _ in reversed(matched_rows):
                        try:
                            sheet.delete_rows(row_index)
                        except Exception as e:
                            print(f"⚠️ Failed to delete row {row_index}: {e}")

                    await interaction.response.send_message(f"✅ Cleared match `{match_id_value}` and deleted fallback channel.", ephemeral=True)

                except Exception as e:
                    print(f"[❌] Failed to clear match by ID: {e}")
                    await interaction.response.send_message("❗ Failed to clear proposed match.", ephemeral=True)

        await interaction.response.send_modal(MatchIDModal(self))

    @discord.ui.button(label="❌ Clear Proposed Score", style=discord.ButtonStyle.blurple, custom_id="dev:clear_score", disabled=False)
    async def clear_proposed_score(self, interaction: discord.Interaction, button: discord.ui.Button):
        class ScoreIDModal(Modal, title="Clear Proposed Score"):
            match_id = TextInput(label="Enter Match ID", placeholder="E.g., Week1-M002, Challenge2-M001", required=True)

            def __init__(self, parent_view):
                super().__init__()
                self.parent_view = parent_view  # 🔁 Store the calling view (DevPanel_Score)

            async def on_submit(self, interaction: discord.Interaction):
                match_id_value = self.match_id.value.strip()

                try:
                    sheet = self.parent_view.spreadsheet.worksheet("Proposed Scores")
                    rows = sheet.get_all_values()

                    matched_rows = []
                    for i, row in enumerate(rows):
                        if len(row) >= 6 and row[0].strip() == match_id_value:  # Column A = Match ID
                            matched_rows.append((i + 1, row[5].strip()))  # row index, Channel ID from column F

                    if not matched_rows:
                        await interaction.response.send_message(f"❗ No score with Match ID `{match_id_value}` found.", ephemeral=True)
                        return

                    # Delete fallback thread (from first match only)
                    first_channel_id = matched_rows[0][1]
                    if first_channel_id:
                        try:
                            channel = interaction.guild.get_channel(int(first_channel_id))
                            if channel and channel.permissions_for(interaction.guild.me).manage_channels:
                                await channel.delete()
                        except Exception as e:
                            print(f"⚠️ Failed to delete fallback channel: {e}")

                    for row_index, _ in reversed(matched_rows):
                        try:
                            sheet.delete_rows(row_index)
                        except Exception as e:
                            print(f"⚠️ Failed to delete row {row_index}: {e}")

                    await interaction.response.send_message(f"✅ Cleared `{match_id_value}` score entry and deleted fallback channel.", ephemeral=True)

                except Exception as e:
                    print(f"[❌] Error clearing score by ID: {e}")
                    await interaction.response.send_message("❗ Failed to clear proposed score.", ephemeral=True)

        await interaction.response.send_modal(ScoreIDModal(self))

    @discord.ui.button(label="🏆 Undo Score For Match", style=discord.ButtonStyle.blurple, custom_id="dev:undo_score", disabled=True)
    async def undo_score(self, interaction, button):
        await self.generic_clear(interaction, "Scoring")

    @discord.ui.button(label="✅ Force Submit Final Score", style=discord.ButtonStyle.green, custom_id="dev:force_final_score")
    async def force_submit_final(self, interaction, button):
        class ForceSubmitFinalScore(Modal, title="Force Final Score"):
            match = TextInput(label="Match ID", required=True)
            winner = TextInput(label="Winner", required=True)
            loser = TextInput(label="Loser", required=True)
            score = TextInput(label="Final Score", required=True)
            def __init__(self, parent): super().__init__(); self.parent = parent
            async def on_submit(self, i):
                m = get_or_create_sheet(self.parent.spreadsheet, "Matches", ["Match ID","Team A","Team B","Proposed Date","Scheduled Date","Status","Winner","Loser","Proposed By"])
                for idx, row in enumerate(m.get_all_values()[1:], 2):
                    if row[0] == self.match.value:
                        m.update_cell(idx, 8, self.score.value)
                        m.update_cell(idx, 9, self.winner.value)
                        m.update_cell(idx, 10, self.loser.value)
                        m.update_cell(idx, 6, "Finished")
                        await self.parent.safe_send(i, "✅ Final score set.")
                        return
                await self.parent.safe_send(i, "❗ Match ID not found.")
        await interaction.response.send_modal(ForceSubmitFinalScore(self))

# -------------------- TEAM TOOLS --------------------

class DevPanel_Team(SafeView):
    def __init__(self, bot, spreadsheet, dev_ids, send_notification):
        super().__init__(timeout=None)
        self.bot = bot
        self.spreadsheet = spreadsheet
        self.dev_ids = dev_ids
        self.send_notification = send_notification

    async def interaction_check(self, interaction):
        return await check_dev(interaction, self.dev_ids)
    
    @discord.ui.button(label="📋 Set All Team Status", style=discord.ButtonStyle.blurple, custom_id="dev:set_all_team_status")
    async def set_all_team_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_dev(interaction, self.dev_ids):
            return

        class StatusDropdown(discord.ui.View):
            def __init__(self, parent):
                super().__init__(timeout=30)
                self.parent = parent

                options = [
                    discord.SelectOption(label="✅ Set All Active", value="Active"),
                    discord.SelectOption(label="🛑 Set All Inactive", value="Inactive"),
                ]
                select = discord.ui.Select(placeholder="Choose team status for ALL teams", options=options)
                select.callback = self.apply_bulk_status
                self.add_item(select)

            async def apply_bulk_status(self, i: discord.Interaction):
                new_status = i.data["values"][0]
                sheet = get_or_create_sheet(self.parent.spreadsheet, "Teams", [])
                rows = sheet.get_all_values()
                updated = 0

                for idx, row in enumerate(rows[1:], start=2):
                    if not row or not row[0].strip():
                        continue
                    while len(row) < 8:
                        row.append("")
                    if row[7].strip() != new_status:
                        sheet.update_cell(idx, 8, new_status)
                        updated += 1

  #              await self.parent.safe_send(i, f"✅ Set `{new_status}` for {updated} team(s).")
  #              await self.parent.send_notification(f"📋 Bulk status update: **{new_status}** applied to {updated} teams.")

        await interaction.response.send_message("Select a status to apply to all teams:", view=StatusDropdown(self), ephemeral=True)
    
    @discord.ui.button(label="📡 Set One Team Status", style=discord.ButtonStyle.blurple, custom_id="dev:set_team_status_modal")
    async def set_one_team_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_dev(interaction, self.dev_ids):
            return

        class TeamSearchModal(discord.ui.Modal, title="Search Team Name"):
            query = discord.ui.TextInput(label="Enter part of team name", required=True)

            def __init__(self, parent):
                super().__init__()
                self.parent = parent

            async def on_submit(self, i: discord.Interaction):
                sheet = get_or_create_sheet(self.parent.spreadsheet, "Teams", [])
                all_rows = sheet.get_all_values()[1:]
                matched = [r[0] for r in all_rows if r and self.query.value.lower() in r[0].lower()]

                if not matched:
                    await self.parent.safe_send(i, "❗ No team found matching that name.")
                    return

                matched = matched[:25]  # Discord max
                options = [discord.SelectOption(label=team, value=team) for team in matched]

                class StatusSelectView(discord.ui.View):
                    def __init__(self, parent, team_options):
                        super().__init__(timeout=60)
                        self.parent = parent
                        self.selected_team = None

                        select_team = discord.ui.Select(placeholder="Select team", options=team_options)
                        select_team.callback = self.select_team
                        self.add_item(select_team)

                    async def select_team(self, inner: discord.Interaction):
                        self.selected_team = inner.data["values"][0]
                        self.clear_items()
                        self.add_item(self.StatusOption(self))
                        await inner.response.edit_message(content=f"Set status for `{self.selected_team}`:", view=self)

                    class StatusOption(discord.ui.Select):
                        def __init__(self, parent_view):
                            self.parent_view = parent_view
                            options = [
                                discord.SelectOption(label="✅ Active", value="Active"),
                                discord.SelectOption(label="🛑 Inactive", value="Inactive")
                            ]
                            super().__init__(placeholder="Choose new status", options=options)

                        async def callback(self, i: discord.Interaction):
                            team = self.parent_view.selected_team
                            new_status = self.values[0]
                            sheet = get_or_create_sheet(self.parent_view.parent.spreadsheet, "Teams", [])
                            updated = False

                            for idx, row in enumerate(sheet.get_all_values()[1:], start=2):
                                if row[0] == team:
                                    while len(row) < 8:
                                        row.append("")
                                    sheet.update_cell(idx, 8, new_status)
                                    updated = True
                                    break

                            if updated:
                                await self.parent_view.parent.safe_send(i, f"✅ `{team}` status set to `{new_status}`.")
                                # Attempt to ping the captain
                                try:
                                    teams_sheet = get_or_create_sheet(self.parent_view.parent.spreadsheet, "Teams", [])
                                    team_row = next((r for r in teams_sheet.get_all_values()[1:] if r[0] == team), None)

                                    captain_mention = team
                                    if team_row and len(team_row) > 1 and "(" in team_row[1] and ")" in team_row[1]:
                                        captain_id = team_row[1].split("(")[-1].split(")")[0]
                                        captain_mention = f"<@{captain_id}>"

                                    await self.parent_view.parent.send_notification(
                                        f"📡 `{team}` status changed to **{new_status}** by a League Mod.\n👑 Notifying captain: {captain_mention}"
                                    )
                                except Exception as e:
                                    print(f"[⚠️] Failed to notify captain of {team}: {e}")
                                    await self.parent_view.parent.send_notification(
                                        f"📡 `{team}` status changed to **{new_status}** by a League Mod. (⚠️ Could not resolve captain mention)"
                                    )
                            else:
                                await self.parent_view.parent.safe_send(i, "❗ Failed to update status.")

                await i.response.send_message("Select the team and status:", view=StatusSelectView(self.parent, options), ephemeral=True)

        await interaction.response.send_modal(TeamSearchModal(self))

    @discord.ui.button(label="💥 Force Disband Team", style=discord.ButtonStyle.red, custom_id="dev:disband_team")
    async def force_disband(self, interaction, button):
        class DisbandModal(Modal, title="Force Disband Team"):
            team = TextInput(label="Team Name", required=True)
            def __init__(self, parent): super().__init__(); self.parent = parent
            async def on_submit(self, i):
                sheet = get_or_create_sheet(self.parent.spreadsheet, "Teams", ["Team Name","Captain","Player 2","Player 3","Player 4","Player 5","Player 6"])
                for idx, row in enumerate(sheet.get_all_values(), 1):
                    if row[0].lower() == self.team.value.lower():
                        team_name = row[0]
                        for suffix in ["", " Captain"]:
                            role_name = f"Team {team_name}{suffix}"
                            role = discord.utils.get(i.guild.roles, name=role_name)
                            if role:
                                try:
                                    await role.delete()
                                    print(f"[🧼] Deleted role: {role_name}")
                                except Exception as e:
                                    print(f"[⚠️] Could not delete role {role_name}: {e}")

                        sheet.delete_rows(idx)
                        await self.parent.send_notification(f"💥 **{row[0]}** was force disbanded by a Admin.")
                        await self.parent.safe_send(i, f"✅ Team **{team_name}** disbanded and roles deleted.")
                        return
                await self.parent.safe_send(i, "❗ Team not found.")
        await interaction.response.send_modal(DisbandModal(self))

    @discord.ui.button(label="👤 Force Remove Player", style=discord.ButtonStyle.red, custom_id="dev:Remove_player")
    async def force_remove_player(self, interaction, button):
        class RemovePlayerModal(Modal, title="Force Remove Player"):
            player = TextInput(label="Player (partial OK)", required=True)
            def __init__(self, parent): super().__init__(); self.parent = parent
            async def on_submit(self, i):
                sheet = get_or_create_sheet(self.parent.spreadsheet, "Teams", ["Team Name","Captain","Player 2","Player 3","Player 4","Player 5","Player 6"])
                for idx, row in enumerate(sheet.get_all_values(), 1):
                    for col in range(1, 7):
                        if self.player.value.lower() in row[col].lower():
                            sheet.update_cell(idx, col + 1, "")
                            await self.parent.safe_send(i, "✅ Player removed.")
                            await self.parent.send_notification(f"👤 `{row[col]}` was force removed from **{row[0]}** by a Admin.")
                            return
                await self.parent.safe_send(i, "❗ Player not found.")
        await interaction.response.send_modal(RemovePlayerModal(self))

    @discord.ui.button(label="📊 Adjust Team ELO", style=discord.ButtonStyle.blurple, custom_id="dev:adjust_elo")
    async def adjust_elo(self, interaction, button):
        class AdjustTeamELO(Modal, title="Adjust Team ELO"):
            team = TextInput(label="Team Name", required=True)
            change = TextInput(label="ELO Change (+ or -)", required=True)
            def __init__(self, parent): super().__init__(); self.parent = parent
            async def on_submit(self, i):
                sheet = get_or_create_sheet(self.parent.spreadsheet, "Leaderboard", ["Team Name","Rating","Wins","Losses","Matches Played"])
                for idx, row in enumerate(sheet.get_all_values(), 1):
                    if row[0].lower() == self.team.value.lower():
                        new_elo = int(row[1]) + int(self.change.value)
                        sheet.update_cell(idx, 2, new_elo)
                        await self.parent.safe_send(i, f"✅ ELO now {new_elo}.")
                        return
                await self.parent.safe_send(i, "❗ Team not found.")
        await interaction.response.send_modal(AdjustTeamELO(self))

# -------------------- PLAYER ENFORCEMENT --------------------

class DevPanel_Player(SafeView):
    def __init__(self, bot, spreadsheet, dev_ids, send_notification):
        super().__init__(timeout=None)
        self.bot = bot
        self.spreadsheet = spreadsheet
        self.dev_ids = dev_ids
        self.send_notification = send_notification

    async def interaction_check(self, interaction):
        return await check_dev(interaction, self.dev_ids)

    async def player_remove(self, interaction, action):
        class KickPlayerModal(Modal, title=f"{action} Player"):
            search = TextInput(label="Player Name / ID", required=True)
            def __init__(self, parent): super().__init__(); self.parent = parent
            async def on_submit(self, i):
                players = get_or_create_sheet(self.parent.spreadsheet, "Players", ["User ID","Username"])
                banned = get_or_create_sheet(self.parent.spreadsheet, "Banned", ["User ID","Username"])
                rows = players.get_all_values()[1:]
                options = [discord.SelectOption(label=f"{row[1]} ({row[0]})", value=str(idx)) for idx, row in enumerate(rows, 2) if self.search.value.lower() in row[1].lower() or self.search.value in row[0]]
                if not options:
                    await self.parent.safe_send(i, "❗ Player not found.")
                    return
                class Confirm(View):
                    @discord.ui.select(placeholder="Select player", options=options)
                    async def select(self, si, select):
                        idx = int(select.values[0])
                        row = players.row_values(idx)
                        if action == "Ban": banned.append_row(row)
                        players.delete_rows(idx)
                        teams = get_or_create_sheet(self.parent.spreadsheet, "Teams", ["Team Name","Captain","Player 2","Player 3","Player 4","Player 5","Player 6"])
                        for tidx, trow in enumerate(teams.get_all_values(), 1):
                            for col in range(1, 7):
                                if row[0] in trow[col] or row[1] in trow[col]:
                                    teams.update_cell(tidx, col + 1, "")
                        await self.parent.safe_send(si, f"✅ {action}ed player.")
                        await self.parent.send_notification(f"🚫 `{row[1]}` was {action.lower()}ed from the league by a Admin.")

                view = Confirm()
                view.parent = self.parent
                await i.response.send_message("Select player:", view=view, ephemeral=True)
        await interaction.response.send_modal(KickPlayerModal(self))

    @discord.ui.button(label="🚫 Kick Player", style=discord.ButtonStyle.danger, custom_id="dev:kick_player")
    async def kick_player(self, interaction, button):
        await self.player_remove(interaction, "Kick")

    @discord.ui.button(label="🚫 Ban Player", style=discord.ButtonStyle.red, custom_id="dev:ban_player")
    async def ban_player(self, interaction, button):
        await self.player_remove(interaction, "Ban")

# -------------------- Dev Panel Poster --------------------

async def post_dev_panel(bot, spreadsheet, dev_ids, send_notification):
    channel_id = bot.config.get("dev_channel_id")
    if not channel_id:
        return

    channel = await bot.fetch_channel(channel_id)

    panels = [
        ("📥 Match Tools", DevPanel_Match),
        ("📊 Score Tools", DevPanel_Score),
        ("🏷️ Team Tools", DevPanel_Team),
        ("🚫 Player Tools", DevPanel_Player),
    ]

    descriptions = {
        "📥 Match Tools": "Admin-only tools for match generation, scheduling, and clean-up.",
        "📊 Score Tools": "Force-clear, undo, or finalize scores manually. Use this to fix stuck entries.",
        "🏷️ Team Tools": "Manage team visibility, disband teams, remove players, or edit team ELO.",
        "🚫 Player Tools": "Kick or ban users directly from the league system.",
    }

    for title, view_cls in panels:
        # 🧹 Delete any existing matching panel
        async for msg in channel.history(limit=100, oldest_first=False):
            if msg.author == bot.user and msg.embeds:
                if title in msg.embeds[0].title:
                    await msg.delete()

        # 🆕 Post fresh panel
        desc = descriptions.get(title, "Some tools may be disabled. Refer to the sheet if needed.")
        embed = discord.Embed(title=title, description=desc, color=discord.Color.red())
        view = view_cls(bot, spreadsheet, dev_ids, send_notification)
        await channel.send(embed=embed, view=view)
        bot.add_view(view)

__all__ = ["cast", "post_dev_panel"]





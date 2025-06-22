import discord
from discord.ui import View, Modal, TextInput
import json

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
        class ForceScheduleMatch(Modal, title="Force Schedule Match"):
            team_a = TextInput(label="Team A")
            team_b = TextInput(label="Team B")
            date = TextInput(label="Date (TBD ok)")
            def __init__(self, parent): super().__init__(); self.parent = parent
            async def on_submit(self, i):
                m = get_or_create_sheet(self.parent.spreadsheet, "Matches", ["Match ID","Team A","Team B","Proposed Date","Scheduled Date","Status","Winner","Loser","Proposed By"])
                w = get_or_create_sheet(self.parent.spreadsheet, "Weekly Matches", ["Week","Team A","Team B","Match ID","Scheduled Date"])
                match_id = str(len(m.get_all_values()) + 1)
                m.append_row([match_id,self.team_a.value,self.team_b.value,"TBD",self.date.value,"Manual","","","System"])
                w.append_row(["Manual",self.team_a.value,self.team_b.value,match_id,self.date.value])
                await self.parent.safe_send(i, "✅ Match scheduled.")
        await interaction.response.send_modal(ForceScheduleMatch(self))

    @discord.ui.button(label="♻️ Reset Weekly Matches", style=discord.ButtonStyle.red, custom_id="dev:reset_weekly_matches", disabled=True)
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

    @discord.ui.button(label="❌ Clear Proposed Match", style=discord.ButtonStyle.primary, custom_id="dev:clear_match", disabled=True)
    async def clear_proposed(self, interaction, button):
        await self.generic_clear(interaction, "Match Proposed")

    @discord.ui.button(label="❌ Clear Proposed Score", style=discord.ButtonStyle.blurple, custom_id="dev:clear_score", disabled=True)
    async def clear_proposed_score(self, interaction, button):
        await self.generic_clear(interaction, "Scoring")

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

    for title, view_cls in panels:
        deleted = 0
        async for msg in channel.history(limit=100, oldest_first=False):
            if msg.author == bot.user and msg.embeds:
                if title in msg.embeds[0].title:
                    await msg.delete()
                    deleted += 1

        embed = discord.Embed(title=title, description=f"Greyed out are under construction, use the sheet for now.", color=discord.Color.red())
        view = view_cls(bot, spreadsheet, dev_ids, send_notification)
        await channel.send(embed=embed, view=view)
        bot.add_view(view)  # ✅ Register persistent view




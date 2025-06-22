import discord
import json

def get_or_create_sheet(spreadsheet, name, headers):
    try:
        sheet = spreadsheet.worksheet(name)
    except Exception:
        sheet = spreadsheet.add_worksheet(title=name, rows="100", cols=str(len(headers)))
        sheet.append_row(headers)
    return sheet

def get_next_match_id(matches_sheet):
    match_ids = matches_sheet.col_values(1)[1:]
    return str(len(match_ids) + 1)

def extract_user_id(user_string):
    if "(" in user_string and ")" in user_string:
        return user_string.split("(")[-1].split(")")[0]
    return None

def update_team_rating(leaderboard_sheet, team_name, won, elo_win, elo_loss):
    for idx, row in enumerate(leaderboard_sheet.get_all_values(), 1):
        if row[0] == team_name:
            rating = int(row[1])
            wins = int(row[2])
            losses = int(row[3])
            matches = int(row[4])

            new_rating = rating + (elo_win if won else elo_loss)
            leaderboard_sheet.update(
                f"B{idx}",
                [[new_rating, wins + (1 if won else 0), losses + (0 if won else 1), matches + 1]]
            )
            return

    # Team not found — add new row
    with open("config.json") as f:
        config = json.load(f)
    starting_elo = config.get("default_team_rating", 800)
    leaderboard_sheet.append_row([
        team_name, starting_elo, 1 if won else 0, 0 if won else 1, 1
    ])

def get_team_mentions(interaction, team_name, teams_sheet, ping_full_team):
    team_row = next((row for row in teams_sheet.get_all_values() if row[0] == team_name), None)
    if not team_row:
        return team_name

    mentions = []
    for player in team_row[1:]:
        if "(" in player and ")" in player:
            user_id = player.split("(")[-1].split(")")[0]
            member = interaction.guild.get_member(int(user_id))
            if member and ping_full_team:
                mentions.append(member.mention)
        elif player.strip():
            mentions.append(player)

    return " ".join(mentions) if mentions else team_name

def sync_leaderboard_with_teams(config_data, teams_sheet, leaderboard_sheet):
    team_min_players = int(config_data.get("team_min_players", 1))
    existing_teams = [row[0] for row in leaderboard_sheet.get_all_values()[1:]]
    team_rows = teams_sheet.get_all_values()[1:]

    added = 0
    for row in team_rows:
        team_name = row[0]
        players = [p for p in row[1:] if p.strip()]

        if len(players) >= team_min_players and team_name not in existing_teams:
            with open("config.json") as f:
                config = json.load(f)
            starting_elo = config.get("default_team_rating", 800)
            leaderboard_sheet.append_row([team_name, starting_elo, 0, 0, 0])
            added += 1

    print(f"[DEBUG] Synced {added} new teams to leaderboard.")

def update_team_rating(leaderboard_sheet, team_name, won, elo_win, elo_loss):
    for idx, row in enumerate(leaderboard_sheet.get_all_values(), 1):
        if row[0] == team_name:
            rating = int(row[1])
            wins = int(row[2])
            losses = int(row[3])
            matches = int(row[4])

            new_rating = rating + (elo_win if won else elo_loss)
            leaderboard_sheet.update(
                f"B{idx}",
                [[new_rating, wins + (1 if won else 0), losses + (0 if won else 1), matches + 1]]
            )
            break
    else:
        with open("config.json") as f:
            config = json.load(f)
        starting_elo = config.get("default_team_rating", 800)
        leaderboard_sheet.append_row([team_name, starting_elo, 1 if won else 0, 0 if won else 1, 1])

    # ✅ Re-sort the leaderboard by rating (column 2, descending)
    data = leaderboard_sheet.get_all_values()
    header, rows = data[0], data[1:]
    sorted_rows = sorted(rows, key=lambda x: int(x[1]), reverse=True)

    leaderboard_sheet.clear()
    leaderboard_sheet.append_row(header)
    leaderboard_sheet.append_rows(sorted_rows)

def log_forfeit_to_history(sheet, week, match_id, team_a, team_b, reason):
    sheet.append_row([
        week, match_id, team_a, team_b,
        "", "",  # Proposed & Scheduled Date
        "", "", "", "", "", "", "", "", "", "", "", "", reason  # Winner column
    ])

def archive_and_clear_challenges(spreadsheet):
    from datetime import datetime

    challenge_sheet = get_or_create_sheet(
        spreadsheet,
        "Challenge Matches",
        ["Week", "Team A", "Team B", "Proposer ID", "Completion Date"]
    )

    match_history_sheet = get_or_create_sheet(
        spreadsheet,
        "Match History",
        [
            "Week", "Match ID", "Team A", "Team B", "Proposed Date", "Scheduled Date",
            "Map 1 Mode", "Map 1 A", "Map 1 B",
            "Map 2 Mode", "Map 2 A", "Map 2 B",
            "Map 3 Mode", "Map 3 A", "Map 3 B",
            "Total A", "Total B", "Maps Won A", "Maps Won B", "Winner"
        ]
    )

    challenge_data = challenge_sheet.get_all_values()[1:]
    if not challenge_data:
        return

    for row in challenge_data:
        week = row[0]
        team_a = row[1]
        team_b = row[2]
        completion_date = row[4]

        # Archive to Match History (minimal row for challenge matches)
        match_history_sheet.append_row([
            week,
            "challenge",
            team_a,
            team_b,
            "",                # proposed date
            completion_date,   # scheduled date
            "", "", "",        # map 1
            "", "", "",        # map 2
            "", "", "",        # map 3
            "", "", "", "", "" # totals + winner
        ])

    # Reset challenge sheet
    challenge_sheet.clear()
    challenge_sheet.append_row(["Week", "Team A", "Team B", "Proposer ID", "Completion Date"])

async def generate_weekly_matches(interaction, spreadsheet, week_number, force=False):
    if not interaction.response.is_done():
        archive_and_clear_challenges(spreadsheet)
        await interaction.response.defer()

    with open("config.json") as f:
        config_data = json.load(f)

    min_teams_required = config_data.get("minimum_teams_start", 2)
    team_min_players = config_data.get("team_min_players", 1)
    match_channel_id = config_data.get("weekly_channel_id")
    elo_win = config_data.get("elo_win_points", 25)
    elo_loss = config_data.get("elo_loss_points", -25)
    affect_elo = config_data.get("forfeit_affects_elo", True)
    ping_full_team = config_data.get("match_ping_full_team", True)

    matches_sheet = get_or_create_sheet(spreadsheet, "Matches", ["Match ID", "Team A", "Team B", "Proposed Date", "Scheduled Date", "Status", "Winner", "Loser", "Proposed By"])
    leaderboard_sheet = get_or_create_sheet(spreadsheet, "Leaderboard", ["Team Name", "Rating", "Wins", "Losses", "Matches Played"])
    weekly_sheet = get_or_create_sheet(spreadsheet, "Weekly Matches", ["Week", "Team A", "Team B", "Match ID", "Scheduled Date"])
    teams_sheet = get_or_create_sheet(spreadsheet, "Teams", ["Team Name", "Captain", "Player 2", "Player 3", "Player 4", "Player 5", "Player 6"])
    sync_leaderboard_with_teams(config_data, teams_sheet, leaderboard_sheet)

    all_teams = leaderboard_sheet.get_all_values()[1:]
    if len(all_teams) < min_teams_required:
        await interaction.followup.send("❗ Not enough teams to generate matchups.", ephemeral=True)
        return
        

    valid_teams = []
    team_players = {}

    for team_row in teams_sheet.get_all_values()[1:]:
        team_name = team_row[0]
        players = [p for p in team_row[1:] if p.strip()]
        team_players[team_name] = len(players)

        if len(players) >= team_min_players:
            valid_teams.append(team_name)

    if len(valid_teams) < min_teams_required:
        await interaction.followup.send("❗ Not enough valid teams to generate matchups.", ephemeral=True)
        return

    if force:
        # ✅ Clear weekly-related sheets before new matchups
        weekly_sheet.clear()
        weekly_sheet.append_row(["Week", "Team A", "Team B", "Match ID", "Scheduled Date"])

        proposed_sheet = get_or_create_sheet(spreadsheet, "Match Propose", ["Team A", "Team B", "Proposer ID", "Proposed Date"])
        proposed_sheet.clear()
        proposed_sheet.append_row(["Team A", "Team B", "Proposer ID", "Proposed Date"])

        scheduled_sheet = get_or_create_sheet(spreadsheet, "Match Scheduled", ["Match ID", "Team A", "Team B", "Scheduled Date"])
        scheduled_sheet.clear()
        scheduled_sheet.append_row(["Match ID", "Team A", "Team B", "Scheduled Date"])

        challenge_sheet = get_or_create_sheet(spreadsheet, "Challenge Matches", ["Week", "Team A", "Team B", "Proposer ID", "Proposed Date", "Completion Date"])
        challenge_sheet.clear()
        challenge_sheet.append_row(["Week", "Team A", "Team B", "Proposer ID", "Proposed Date", "Completion Date"])

        match_history_sheet = get_or_create_sheet(
            spreadsheet, "Match History",
            ["Week", "Match ID", "Team A", "Team B", "Proposed Date", "Scheduled Date",
            "Map 1 Mode", "Map 1 A", "Map 1 B", "Map 2 Mode", "Map 2 A", "Map 2 B",
            "Map 3 Mode", "Map 3 A", "Map 3 B", "Total A", "Total B", "Maps Won A", "Maps Won B", "Winner"]
        )

        existing = matches_sheet.get_all_values()[1:]
        for idx, row in enumerate(existing, start=2):
            fields = row[:9]
            if len(fields) < 9:
                print(f"⚠️ Skipping malformed row (too short): {row}")
                continue

            match_id, team_a, team_b, _, _, status, winner, loser, _ = fields

            if status.strip() not in ["Finished", "Cancelled", "Forfeited"]:
                team_a_valid = team_players.get(team_a, 0) >= team_min_players
                team_b_valid = team_players.get(team_b, 0) >= team_min_players

                if team_a_valid and team_b_valid:
                    matches_sheet.update_cell(idx, 6, "Double Forfeit")
                    log_forfeit_to_history(match_history_sheet, week_number, match_id, team_a, team_b, "Double Forfeit")

                elif team_a_valid:
                    matches_sheet.update_cell(idx, 6, "Forfeited")
                    matches_sheet.update_cell(idx, 7, team_a)
                    matches_sheet.update_cell(idx, 8, team_b)
                    if affect_elo:
                        update_team_rating(leaderboard_sheet, team_a, True, elo_win, elo_loss)
                        update_team_rating(leaderboard_sheet, team_b, False, elo_win, elo_loss)
                    log_forfeit_to_history(match_history_sheet, week_number, match_id, team_a, team_b, f"{team_b} Forfeit")

                elif team_b_valid:
                    matches_sheet.update_cell(idx, 6, "Forfeited")
                    matches_sheet.update_cell(idx, 7, team_b)
                    matches_sheet.update_cell(idx, 8, team_a)
                    if affect_elo:
                        update_team_rating(leaderboard_sheet, team_b, True, elo_win, elo_loss)
                        update_team_rating(leaderboard_sheet, team_a, False, elo_win, elo_loss)
                    log_forfeit_to_history(match_history_sheet, week_number, match_id, team_a, team_b, f"{team_a} Forfeit")

                else:
                    matches_sheet.update_cell(idx, 6, "Double Forfeit")
                    log_forfeit_to_history(match_history_sheet, week_number, match_id, team_a, team_b, "Double Forfeit")

    valid_teams.sort(key=lambda x: int(next((row[1] for row in all_teams if row[0] == x), 1000)), reverse=True)

    from collections import defaultdict

    matchups = []
    used_pairs = set()
    match_count = defaultdict(int)

    for i, team in enumerate(valid_teams):
        if match_count[team] >= 2:
            continue
        for opponent in valid_teams:
            if opponent == team or match_count[opponent] >= 2:
                continue

            pair = tuple(sorted([team, opponent]))
            if pair in used_pairs:
                continue

            # Schedule the match
            used_pairs.add(pair)
            matchups.append((team, opponent))
            match_count[team] += 1
            match_count[opponent] += 1

            if match_count[team] >= 2:
                break

    match_channel = interaction.guild.get_channel(int(match_channel_id))
    message_lines = [f"📢 **Week {week_number} Matchups:**\n"]

    for team_a, team_b in matchups:
        team_a_id, team_b_id = sorted([team_a[:3], team_b[:3]])
        existing_week_matches = [row for row in weekly_sheet.get_all_values()[1:] if str(row[0]) == str(week_number)]
        match_id = f"Week{week_number}-M{len(existing_week_matches) + 1:02d}"

        weekly_sheet.append_row([week_number, team_a, team_b, match_id, "TBD"])
        matches_sheet.append_row([match_id, team_a, team_b, "TBD", "", "Auto Proposed", "", "", "", "System"])

        mentions_a = get_team_mentions(interaction, team_a, teams_sheet, ping_full_team)
        mentions_b = get_team_mentions(interaction, team_b, teams_sheet, ping_full_team)

        message_lines.append(f"🔹 {team_a} vs {team_b}\n{mentions_a} vs {mentions_b}\n")

    if match_channel:
        await match_channel.send("\n".join(message_lines))

    await interaction.followup.send(f"✅ Week {week_number} matchups generated and posted in <#{match_channel_id}>.", ephemeral=True)

def setup_match_module(bot, spreadsheet):
    from discord import app_commands

    class WeeklyMatchGenerator(app_commands.Group):
        def __init__(self):
            super().__init__(name="weekly", description="Weekly match generation commands.")

        @app_commands.command(name="generate", description="Generate weekly matchups")
        @app_commands.describe(week_number="Week number")
        async def generate(self, interaction: discord.Interaction, week_number: int):
            await generate_weekly_matches(interaction, spreadsheet, week_number)

     ### bot.tree.add_command(WeeklyMatchGenerator())



from datetime import datetime, timezone, timedelta
import discord
from discord.ext import commands

from lib.bot import TMWBot
from lib.media_types import LOG_CHOICES, MEDIA_TYPES

from lib.immersion_helpers import is_valid_channel

from typing import Optional

CREATE_USER_GOALS_TABLE = """
    CREATE TABLE IF NOT EXISTS user_goals (
    goal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    goal_type TEXT NOT NULL CHECK(goal_type IN ('points', 'amount')),
    goal_value INTEGER NOT NULL,
    end_date TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
"""

CREATE_GOAL_QUERY = """
    INSERT INTO user_goals (user_id, media_type, goal_type, goal_value, end_date, created_at)
    VALUES (?, ?, ?, ?, ?, ?);
"""

CREATE_GOAL_QUERY_DEFAULT = """
    INSERT INTO user_goals (user_id, media_type, goal_type, goal_value, end_date)
    VALUES (?, ?, ?, ?, ?);
"""

GET_USER_GOALS_QUERY = """
    SELECT goal_id, media_type, goal_type, goal_value, end_date
    FROM user_goals
    WHERE user_id = ?;
"""

DELETE_GOAL_QUERY = """
    DELETE FROM user_goals
    WHERE goal_id = ? AND user_id = ?;
"""

GET_GOAL_STATUS_QUERY = """
    SELECT goal_id, goal_type, goal_value, end_date, created_at, 
        CASE
            WHEN goal_type = 'points' THEN (
                SELECT COALESCE(SUM(points_received), 0) 
                FROM logs 
                WHERE user_id = ? 
                AND media_type = ? 
                AND log_date BETWEEN user_goals.created_at AND user_goals.end_date)
            WHEN goal_type = 'amount' THEN (
                SELECT COALESCE(SUM(amount_logged), 0) 
                FROM logs 
                WHERE user_id = ? 
                AND media_type = ? 
                AND log_date BETWEEN user_goals.created_at AND user_goals.end_date)
        END as progress
    FROM user_goals
    WHERE user_id = ? 
    AND media_type = ?;
"""

GET_EXPIRED_GOALS_QUERY = """
    SELECT goal_id, media_type, goal_type, goal_value, end_date
    FROM user_goals
    WHERE user_id = ? AND end_date < ?;
"""

DELETE_ALL_EXPIRED_GOALS_QUERY = """
    DELETE FROM user_goals
    WHERE user_id = ? AND end_date < ?;
"""


async def goal_undo_autocomplete(interaction: discord.Interaction, current_input: str):
    current_input = current_input.strip()
    tmw_bot = interaction.client
    tmw_bot: TMWBot
    user_goals = await tmw_bot.GET(GET_USER_GOALS_QUERY, (interaction.user.id,))
    choices = []

    for goal_id, media_type, goal_type, goal_value, end_date in user_goals:
        end_date_dt = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        end_date_str = end_date_dt.strftime('%Y-%m-%d %H:%M UTC')
        goal_entry = f"{goal_type.capitalize()} goal of {goal_value} for {media_type} by {end_date_str}"
        if current_input.lower() in goal_entry.lower():
            choices.append(discord.app_commands.Choice(name=goal_entry, value=str(goal_id)))

    return choices[:10]


async def check_goal_status(bot: TMWBot, user_id: int, media_type: str):
    result = await bot.GET(GET_GOAL_STATUS_QUERY, (user_id, media_type, user_id, media_type, user_id, media_type))
    goal_statuses = []

    for goal_id, goal_type, goal_value, end_date, created_at, progress in result:
        end_date_dt = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        created_at_dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        current_time = discord.utils.utcnow()
        timestamp_end = int(end_date_dt.timestamp())
        timestamp_created = int(created_at_dt.timestamp())
        if goal_type == 'amount':
            unit_name = MEDIA_TYPES[media_type]['unit_name']
            unit_name = f"{unit_name}{'s' if goal_value > 1 else ''}"
        else:
            unit_name = 'points'

        # Calculate progress percentage and generate emoji progress bar
        percentage = min(int((progress / goal_value) * 100), 100)
        bar_filled = "🟩" * (percentage // 10)  # each green square represents 10%
        bar_empty = "⬜" * (10 - (percentage // 10))
        progress_bar = f"{bar_filled}{bar_empty} ({percentage}%)"

        # Create status message based on goal progress
        if (created_at_dt <= current_time <= end_date_dt) and progress < goal_value:
            goal_status = f"Goal in progress: `{progress}`/`{goal_value}` {unit_name} for `{media_type}` - Ends <t:{timestamp_end}:R>. \n{progress_bar} "
        elif progress >= goal_value:
            goal_status = f"🎉 Congratulations! You've achieved your goal of `{goal_value}` {unit_name} for `{media_type}` between <t:{timestamp_created}:D> and <t:{timestamp_end}:D>."
        else:
            goal_status = f"⚠️ Goal failed: `{progress}`/`{goal_value}` {unit_name} for `{media_type}` by <t:{timestamp_end}:R>. \n{progress_bar}"

        goal_statuses.append(goal_status)

    return goal_statuses


class GoalsCog(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot

    async def cog_load(self):
        await self.bot.RUN(CREATE_USER_GOALS_TABLE)

    @discord.app_commands.command(name='log_set_goal', description='Set an immersion goal for yourself!')
    @discord.app_commands.describe(
        media_type='The type of media for which you want to set a goal.',
        goal_type='The type of goal, either points or amount.',
        goal_value='The goal value you want to achieve.',
        end_date_or_hours='The date you want to achieve the goal by (YYYY-MM-DD format) or number of hours from now.',
        start_date='The date you want to start tracking the goal (Either YYYY-MM-DD or YYYY-MM-DD HH:MM format). Do note that YYYY-MM-DD format will set the start time to 0:00 UTC.'
    )
    @discord.app_commands.choices(goal_type=[
        discord.app_commands.Choice(name='Points', value='points'),
        discord.app_commands.Choice(name='Amount', value='amount')],
        media_type=LOG_CHOICES)
    async def log_set_goal(self, interaction: discord.Interaction, media_type: str, goal_type: str, goal_value: int, end_date_or_hours: str, start_date: Optional[str]):
        if not await is_valid_channel(interaction):
            return await interaction.response.send_message("You can only use this command in DM or in the log channels.", ephemeral=True)
        try:
            if end_date_or_hours.isdigit():
                hours = int(end_date_or_hours)
                end_date_dt = (discord.utils.utcnow() + timedelta(hours=hours))
            else:
                end_date_dt = datetime.strptime(end_date_or_hours, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                if end_date_dt < discord.utils.utcnow().replace(minute=0, second=0, microsecond=0):
                    return await interaction.response.send_message("The end date must be in the future.", ephemeral=True)
        except ValueError:
            return await interaction.response.send_message("Invalid input. Please use either a number of hours or a date in YYYY-MM-DD format.", ephemeral=True)

        if start_date:
            try:
                try:
                    start_date_dt = datetime.strptime(start_date, '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
                except ValueError:
                    start_date_dt = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                if start_date_dt > end_date_dt:
                    return await interaction.response.send_message("The start date must be before the end date.", ephemeral=True)
            except ValueError:
                return await interaction.response.send_message("Invalid input. Please use a date in YYYY-MM-DD or YYYY-MM-DD HH:MM format.", ephemeral=True)
        else:
            start_date_dt = None
        
        if start_date_dt == None:
            await self.bot.RUN(CREATE_GOAL_QUERY_DEFAULT, (interaction.user.id, media_type, goal_type, goal_value, end_date_dt.strftime('%Y-%m-%d %H:%M:%S')))
        else:
            await self.bot.RUN(CREATE_GOAL_QUERY, (interaction.user.id, media_type, goal_type, goal_value, end_date_dt.strftime('%Y-%m-%d %H:%M:%S'), start_date_dt.strftime('%Y-%m-%d %H:%M:%S')))

        unit_name = MEDIA_TYPES[media_type]['unit_name'] if goal_type == 'amount' else 'points'
        timestamp = int(end_date_dt.timestamp())
        embed = discord.Embed(title="Goal Set!", color=discord.Color.green())
        embed.add_field(name="Media Type", value=media_type, inline=True)
        embed.add_field(name="Goal Type", value=goal_type.capitalize(), inline=True)
        embed.add_field(name="Goal Value", value=f"{goal_value} {unit_name}{'s' if goal_value > 1 else ''}", inline=True)
        if start_date:
            embed.add_field(name="Start Date", value=f"<t:{int(start_date_dt.timestamp())}:R>", inline=True)
        embed.add_field(name="End Date", value=f"<t:{timestamp}:R>", inline=True)
        embed.set_footer(text=f"Goal set by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name='log_remove_goal', description='Remove one of your goals.')
    @discord.app_commands.describe(goal_entry='Select the goal you want to remove.')
    @discord.app_commands.autocomplete(goal_entry=goal_undo_autocomplete)
    async def log_remove_goal(self, interaction: discord.Interaction, goal_entry: str):
        if not goal_entry.isdigit():
            return await interaction.response.send_message("Invalid goal entry selected.", ephemeral=True)

        goal_id = int(goal_entry)
        user_goals = await self.bot.GET(GET_USER_GOALS_QUERY, (interaction.user.id,))
        goal_ids = [goal[0] for goal in user_goals]

        if goal_id not in goal_ids:
            return await interaction.response.send_message("The selected goal entry does not exist or does not belong to you.", ephemeral=True)

        goal_to_remove = next(goal for goal in user_goals if goal[0] == goal_id)
        goal_type, goal_value, media_type = goal_to_remove[2], goal_to_remove[3], goal_to_remove[1]
        unit_name = MEDIA_TYPES[media_type]['unit_name'] if goal_type == 'amount' else 'points'

        await self.bot.RUN(DELETE_GOAL_QUERY, (goal_id, interaction.user.id))
        await interaction.response.send_message(f"> {interaction.user.mention} Your `{goal_type}` goal of `{goal_value} {unit_name}{'s' if goal_value > 1 else ''}` for `{media_type}` has been removed.")

    @discord.app_commands.command(name='log_view_goals', description='View your current goals or the goals of another user.')
    @discord.app_commands.describe(member='The member whose goals you want to view (optional).')
    async def log_view_goals(self, interaction: discord.Interaction, member: Optional[discord.Member]):
        member = member or interaction.user
        user_goals = await self.bot.GET(GET_USER_GOALS_QUERY, (member.id,))

        if not user_goals:
            return await interaction.response.send_message(f"> {member.display_name} has no active goals.", ephemeral=True)

        embed = discord.Embed(title=f"{member.display_name}'s Goals", color=discord.Color.blue())
        fields_added = 0

        for media_type in MEDIA_TYPES.keys():
            goal_statuses = await check_goal_status(self.bot, member.id, media_type)
            for i, goal_status in enumerate(goal_statuses):
                if fields_added < 24:
                    embed.add_field(name=f"Goal {fields_added + 1}", value=goal_status, inline=False)
                    fields_added += 1
                else:
                    embed.add_field(name="Notice", value="You have reached the maximum number of fields. Please clear some of your goals to view more.", inline=False)
                    break
            if fields_added >= 24:
                break

        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name='log_clear_goals', description='Clear all expired goals.')
    async def log_clear_goals(self, interaction: discord.Interaction):
        current_time = discord.utils.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        expired_goals = await self.bot.GET(GET_EXPIRED_GOALS_QUERY, (interaction.user.id, current_time))

        if not expired_goals:
            return await interaction.response.send_message(f"> You have no expired goals to clear.", ephemeral=True)

        await self.bot.RUN(DELETE_ALL_EXPIRED_GOALS_QUERY, (interaction.user.id, current_time))

        removed_goals = []
        for goal_id, media_type, goal_type, goal_value, end_date in expired_goals:
            end_time_int = int(datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp())
            removed_goals.append(f"- `{goal_type}` goal of `{goal_value}` for `{media_type}` (ended <t:{end_time_int}:R>)")

        removed_goals_str = "\n".join(removed_goals)
        await interaction.response.send_message(f"The following expired goals have been removed:\n{removed_goals_str}")


async def setup(bot):
    await bot.add_cog(GoalsCog(bot))

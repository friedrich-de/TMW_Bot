import io
import asyncio
import pandas as pd
import bar_chart_race as bcr
import discord
from discord.ext import commands
import warnings
from datetime import datetime, timedelta
from typing import Optional
import tempfile
import os
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt

from lib.bot import TMWBot
from lib.media_types import LOG_CHOICES, MEDIA_TYPES
from lib.immersion_helpers import is_valid_channel
from .username_fetcher import get_username_db

GET_LOGS_FOR_RACE_QUERY = """
    SELECT user_id, media_type, amount_logged, points_received, log_date
    FROM logs
    WHERE log_date BETWEEN ? AND ?
    AND (? IS NULL OR media_type = ?)
    ORDER BY log_date;
"""


def admin_cooldown(
    interaction: discord.Interaction,
) -> Optional[discord.app_commands.Cooldown]:
    if interaction.channel.permissions_for(interaction.user).administrator:
        return None
    return discord.app_commands.Cooldown(1, 300.0)


class ImmersionBarRaces(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot
        self.set_fonts()

    def set_fonts(self):
        font_list = []
        japanese_fonts = [
            "Noto Sans CJK JP",
            "Noto Sans JP",
            "Yu Gothic",
            "MS Gothic",
            "Hiragino Sans",
        ]
        emoji_fonts = ["Noto Emoji"]

        for font_name in japanese_fonts:
            try:
                font_path = fm.findfont(
                    fm.FontProperties(family=font_name),
                    fallback_to_default=False,
                    rebuild_if_missing=True,
                )
                if font_path:
                    font_list.append(font_name)
                    break
            except:
                continue

        for font_name in emoji_fonts:
            try:
                font_path = fm.findfont(
                    fm.FontProperties(family=font_name),
                    fallback_to_default=False,
                    rebuild_if_missing=True,
                )
                if font_path:
                    font_list.append(font_name)
                    break
            except:
                continue

        font_list.append("sans-serif")
        plt.rcParams["font.family"] = font_list

    def generate_bar_race(
        self, logs_data, start_date, end_date, media_type=None, race_type="points"
    ):
        df = pd.DataFrame(
            logs_data,
            columns=[
                "username",
                "media_type",
                "amount_logged",
                "points_received",
                "log_date",
            ],
        )

        df["log_date"] = pd.to_datetime(df["log_date"], utc=True)

        value_col = "points_received" if race_type == "points" else "amount_logged"

        # Calculate cumulative sum for each user over time
        # groupby('username') groups all entries by user
        # cumsum() calculates running total for each user
        df["cumsum"] = df.groupby("username")[value_col].cumsum()

        # Create a pivot table for the bar chart race
        # index='log_date': dates become row indices
        # columns='username': each user gets their own column
        # values='cumsum': the values shown in the chart
        # aggfunc='max': if multiple entries exist for same date, take the maximum
        pivot_df = df.pivot_table(
            index="log_date", columns="username", values="cumsum", aggfunc="max"
        ).ffill()

        # Determine appropriate sampling frequency based on date range
        # This reduces the number of frames for longer time periods
        if (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days > 210:
            freq = "7D"
        elif (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days > 150:
            freq = "6D"
        elif (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days > 120:
            freq = "5D"
        elif (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days > 90:
            freq = "4D"
        elif (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days > 60:
            freq = "3D"
        elif (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days > 31:
            freq = "2D"
        else:
            freq = "D"

        # Resample data according to chosen frequency
        # max() keeps the highest value in each period
        # fillna methods handle missing values
        pivot_df = pivot_df.resample(freq).max().ffill().fillna(0)

        # Generate chart title
        title = f"{'Points' if race_type == 'points' else 'Amount'} Race"
        if media_type and race_type == "points":
            title += f" - {media_type}"
        elif media_type and race_type == "amount":
            title += f" - {MEDIA_TYPES[media_type]['log_name']}"
        title += f"\n{start_date.split()[0]} to {end_date.split()[0]}"

        # Create temporary file for video output
        # delete=False allows us to read the file after closing it
        temp_file = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        temp_file.close()

        # Generate the bar chart race animation
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bcr.bar_chart_race(
                df=pivot_df,
                filename=temp_file.name,
                title=title,
                n_bars=15,
                filter_column_colors=True,
                period_length=500,
                steps_per_period=20,
                period_fmt="%b %#d, %Y",
            )

        with open(temp_file.name, "rb") as f:
            buffer = io.BytesIO(f.read())
            buffer.seek(0)

        os.unlink(temp_file.name)

        return buffer

    @discord.app_commands.command(
        name="log_race",
        description="Generate a bar chart race visualization of immersion progress!",
    )
    @discord.app_commands.describe(
        from_date="Start date (YYYY-MM-DD)",
        to_date="End date (YYYY-MM-DD)",
        media_type="Optional: Filter by media type",
        race_type="Optional: Race by points or amount",
    )
    @discord.app_commands.choices(
        media_type=LOG_CHOICES,
        race_type=[
            discord.app_commands.Choice(name="Points", value="points"),
            discord.app_commands.Choice(name="Amount", value="amount"),
        ],
    )
    @discord.app_commands.guild_only()
    @discord.app_commands.checks.dynamic_cooldown(admin_cooldown)
    async def log_race(
        self,
        interaction: discord.Interaction,
        from_date: str,
        to_date: str,
        media_type: Optional[str] = None,
        race_type: Optional[str] = "points",
    ):
        # if not await is_valid_channel(interaction):
        # return await interaction.response.send_message("You can only use this command in DM or in the log channels.", ephemeral=True)

        try:
            start_date = datetime.strptime(from_date, "%Y-%m-%d")
            end_date = datetime.strptime(to_date, "%Y-%m-%d")
        except ValueError:
            return await interaction.response.send_message(
                "Invalid date format. Please use YYYY-MM-DD.", ephemeral=True
            )

        if end_date < start_date:
            return await interaction.response.send_message(
                "End date must be after start date.", ephemeral=True
            )

        if (end_date - start_date).days > 210:
            return await interaction.response.send_message(
                "Date range must be 210 days or less (subject to change...)",
                ephemeral=True,
            )

        await interaction.response.defer()

        logs_data = await self.bot.GET(
            GET_LOGS_FOR_RACE_QUERY,
            (
                start_date.strftime("%Y-%m-%d 00:00:00"),
                end_date.strftime("%Y-%m-%d 23:59:59"),
                media_type,
                media_type,
            ),
        )

        if not logs_data:
            return await interaction.followup.send(
                "No logs found for the specified period.", ephemeral=True
            )

        unique_user_ids = set(log[0] for log in logs_data)
        user_names = {}
        for user_id in unique_user_ids:
            user_names[user_id] = await get_username_db(self.bot, user_id)

        logs_with_names = []
        for log in logs_data:
            log_list = list(log)
            log_list[0] = user_names[log[0]]
            logs_with_names.append(log_list)

        initial_logs = []
        start_datetime = (start_date - timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
        for username in user_names.values():
            initial_logs.append([username, media_type or "ALL", 0, 0, start_datetime])

        logs_with_names = initial_logs + logs_with_names

        buffer = await asyncio.to_thread(
            self.generate_bar_race,
            logs_with_names,
            from_date,
            to_date,
            media_type,
            race_type,
        )

        message = f"Bar chart for {from_date} to {to_date}"
        if media_type and race_type == "points":
            message += f" - {media_type}"
        elif media_type and race_type == "amount":
            message += f" - {MEDIA_TYPES[media_type]['log_name']}"

        file = discord.File(buffer, filename="race.mp4")
        await interaction.followup.send(message, file=file)


async def setup(bot):
    await bot.add_cog(ImmersionBarRaces(bot))

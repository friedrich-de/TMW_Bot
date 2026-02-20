import io
import numpy as np
import seaborn as sns
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib import colormaps
from matplotlib import patches
import matplotlib.colors as mcolors
import discord
from discord.ext import commands
from typing import Optional
from datetime import datetime
import asyncio
from lib.media_types import MEDIA_TYPES, LOG_CHOICES
from lib.bot import TMWBot
from lib.immersion_helpers import is_valid_channel
from .username_fetcher import get_username_db
import matplotlib
matplotlib.use('Agg')

GET_USER_LOGS_FOR_PERIOD_QUERY_BASE = """
    SELECT media_type, amount_logged, points_received, log_date
    FROM logs
    WHERE user_id = ? AND log_date BETWEEN ? AND ?
"""

GET_USER_LOGS_FOR_PERIOD_QUERY_WITH_MEDIA_TYPE = GET_USER_LOGS_FOR_PERIOD_QUERY_BASE + " AND media_type = ? ORDER BY log_date;"
GET_USER_LOGS_FOR_PERIOD_QUERY_BASE += " ORDER BY log_date;"


def modify_cmap(cmap_name, zero_color="black", nan_color="black", truncate_high=0.7):
    """
    Modify a colormap to have specific colors for 0 and NaN values, and truncate the upper range.
    """
    base_cmap = colormaps[cmap_name]
    truncated_cmap = base_cmap(np.linspace(0, truncate_high, base_cmap.N))
    modified_cmap = mcolors.ListedColormap(truncated_cmap)
    modified_cmap.colors[0] = mcolors.to_rgba(zero_color)

    # Set NaN color
    modified_cmap.set_bad(color=nan_color)

    return modified_cmap


def embedded_info(df: pd.DataFrame) -> tuple:
    points_total = df['points_received'].sum()
    breakdown = df.groupby('media_type').agg({'amount_logged': 'sum', 'points_received': 'sum'}).reset_index()
    breakdown['unit_name'] = breakdown['media_type'].apply(lambda x: MEDIA_TYPES[x]['unit_name'])
    breakdown_str = "\n".join([
        f"{row['media_type']}: {row['amount_logged']} {row['unit_name']}{'s' if row['amount_logged'] > 1 else ''} → {round(row['points_received'], 2)} pts"
        for _, row in breakdown.iterrows()
    ])

    return breakdown_str, points_total


def set_plot_styles():
    plt.rcParams.update({
        'axes.titlesize': 20,
        'axes.titleweight': 'bold',
        'axes.labelsize': 14,
        'axes.labelweight': 'bold',
        'xtick.labelsize': 12,
        'ytick.labelsize': 12,
        'axes.facecolor': '#2c2c2d',
        'figure.facecolor': '#2c2c2d',
        'text.color': 'white',
        'axes.labelcolor': 'white',
        'xtick.color': 'white',
        'ytick.color': 'white'
    })


def process_bar_data(df: pd.DataFrame, from_date: datetime, to_date: datetime, immersion_type: str = None) -> tuple:
    bar_df = df[from_date:to_date]
    if immersion_type:
        bar_df = bar_df.pivot_table(index=bar_df.index.date, columns='media_type', values='amount_logged', aggfunc='sum', fill_value=0)
    else:
        bar_df = bar_df.pivot_table(index=bar_df.index.date, columns='media_type', values='points_received', aggfunc='sum', fill_value=0)
    bar_df.index = pd.DatetimeIndex(bar_df.index)

    time_frame = pd.date_range(bar_df.index.date.min(), to_date, freq='D')
    bar_df = bar_df.reindex(time_frame, fill_value=0)

    if not isinstance(bar_df.index, pd.DatetimeIndex):
        bar_df.index = pd.to_datetime(bar_df.index)

    if len(bar_df) > 365 * 2:
        df_plot = bar_df.resample('QE').sum()
        x_lab = " (year-quarter)"
        date_labels = df_plot.index.map(lambda date: f"{date.year}-Q{(date.month - 1) // 3 + 1}")
    elif len(bar_df) > 30 * 7:
        df_plot = bar_df.resample('ME').sum()
        x_lab = " (year-month)"
        date_labels = df_plot.index.strftime("%Y-%m")
    elif len(bar_df) > 31:
        df_plot = bar_df.resample('W').sum()
        x_lab = " (year-week)"
        date_labels = df_plot.index.strftime("%Y-%W")
    else:
        df_plot = bar_df
        date_labels = df_plot.index.strftime("%Y-%m-%d")
        x_lab = ""

    return df_plot, x_lab, date_labels


def process_heatmap_data(df: pd.DataFrame, from_date: datetime, to_date: datetime) -> dict:
    # Keep only numeric points data so reindex fill values do not touch string columns.
    df = df[["points_received"]].resample("D").sum()
    full_date_range = pd.date_range(start=datetime(df.index.year.min(), 1, 1), end=datetime(df.index.year.max(), 12, 31))
    df = df.reindex(full_date_range, fill_value=0)
    df["day"] = df.index.weekday
    df["year"] = df.index.year

    # Generate heatmap data for each year
    heatmap_data = {}
    for year, group in df.groupby("year"):
        year_begins_on = group.index.date.min().weekday()
        group["week"] = (group.index.dayofyear + year_begins_on - 1) // 7
        year_data = group.pivot_table(index="day", columns="week", values="points_received", aggfunc="sum", fill_value=np.nan)

        heatmap_data[year] = year_data

    return heatmap_data


# Function to generate the bar chart
def generate_bar_chart(df: pd.DataFrame, from_date: datetime, to_date: datetime, immersion_type: str = None) -> io.BytesIO:
    # Apply consistent plot styles
    set_plot_styles()

    df_plot, x_lab, date_labels = process_bar_data(df, from_date, to_date, immersion_type)

    fig, ax = plt.subplots(figsize=(16, 12))
    fig.patch.set_facecolor('#2c2c2d')
    df_plot.plot(kind='bar', stacked=True, ax=ax, color=[MEDIA_TYPES[col].get('color', 'gray') for col in df_plot.columns])
    ax.set_title('Points Over Time' if not immersion_type else f"{MEDIA_TYPES[immersion_type]['log_name']} Over Time")
    ax.set_ylabel('Points' if not immersion_type else MEDIA_TYPES[immersion_type]['unit_name'] + 's')
    ax.set_xlabel('Date' + x_lab)
    ax.set_xticklabels(date_labels, rotation=45, ha='right')
    ax.grid(color='#8b8c8c', axis='y')
    # remove splines
    for spline in ax.spines.values():
        if spline.spine_type != 'bottom':
            spline.set_visible(False)

    buffer = io.BytesIO()
    plt.savefig(buffer, format='png', facecolor=fig.get_facecolor(), bbox_inches='tight')
    plt.close()  # Close the figure
    buffer.seek(0)

    return buffer


# Function to generate the heatmap
def generate_heatmap(df: pd.DataFrame, from_date: datetime, to_date: datetime, immersion_type) -> io.BytesIO:
    set_plot_styles()
    heatmap_data = process_heatmap_data(df, from_date, to_date)
    cmap = modify_cmap('Blues_r', zero_color="#222222", nan_color="#2c2c2d")

    num_years = len(heatmap_data)
    fig_height = num_years * 3
    fig, axes = plt.subplots(nrows=num_years, ncols=1, figsize=(18, fig_height))
    fig.patch.set_facecolor('#2c2c2d')

    if num_years == 1:
        axes = [axes]

    current_date = datetime.now().date()
    for ax, (year, data) in zip(axes, heatmap_data.items()):
        sns.heatmap(
            data,
            cmap=cmap,
            linewidths=1.5,
            linecolor="#2c2c2d",
            cbar=False,
            square=True,
            ax=ax
        )
        # ax.set_title(f"Heatmap - {year}")
        ax.set_title(f"{MEDIA_TYPES[immersion_type]['Achievement_Group']} Heatmap - {year}" if immersion_type else f"Immersion Heatmap - {year}")
        ax.axis("off")
        # add a colorbar for the heatmap
        cbar = fig.colorbar(ax.collections[0], ax=ax, orientation='horizontal', fraction=0.1, pad=0.02, aspect=50)
        cbar.ax.yaxis.set_tick_params(color='white')
        cbar.outline.set_edgecolor('#222222')
        plt.setp(plt.getp(cbar.ax.axes, 'yticklabels'), color='white')
        # Highlight the current day with a dark border
        if current_date.year == year:
            current_week = current_date.isocalendar().week - 1
            current_day = current_date.weekday()
            rect = patches.Rectangle(
                (current_week, current_day),
                1, 1,
                linewidth=2,
                edgecolor='black',
                facecolor='none'
            )
            ax.add_patch(rect)

    plt.tight_layout(pad=2.0)

    buffer = io.BytesIO()
    plt.savefig(buffer, format='png', facecolor=fig.get_facecolor(), bbox_inches='tight')
    plt.close()
    buffer.seek(0)

    return buffer


class ImmersionLogMe(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot

    async def get_user_logs(self, user_id, from_date, to_date, immersion_type=None):
        if immersion_type:
            query = GET_USER_LOGS_FOR_PERIOD_QUERY_WITH_MEDIA_TYPE
            params = (user_id, from_date.strftime('%Y-%m-%d %H:%M:%S'), to_date.strftime('%Y-%m-%d %H:%M:%S'), immersion_type)
        else:
            query = GET_USER_LOGS_FOR_PERIOD_QUERY_BASE
            params = (user_id, from_date.strftime('%Y-%m-%d %H:%M:%S'), to_date.strftime('%Y-%m-%d %H:%M:%S'))

        user_logs = await self.bot.GET(query, params)
        return user_logs

    @discord.app_commands.command(name='log_stats', description='Display an immersion overview with a specified.')
    @discord.app_commands.describe(
        user='Optional user to display the immersion overview for.',
        from_date='Optional start date (YYYY-MM-DD).',
        to_date='Optional end date (YYYY-MM-DD).',
        immersion_type='Optional type of immersion to filter by (e.g., reading, listening, etc.).',
    )
    @discord.app_commands.choices(immersion_type=LOG_CHOICES)
    async def log_stats(self, interaction: discord.Interaction, user: Optional[discord.User] = None, from_date: Optional[str] = None, to_date: Optional[str] = None, immersion_type: Optional[str] = None):
        if not await is_valid_channel(interaction):
            return await interaction.response.send_message("You can only use this command in DM or in the log channels.", ephemeral=True)
        await interaction.response.defer()

        user_id = user.id if user else interaction.user.id
        user_name = await get_username_db(self.bot, user_id)

        try:
            if from_date:
                from_date = datetime.strptime(from_date, '%Y-%m-%d')
                start_of_year = datetime(from_date.year, 1, 1)
            else:
                now = datetime.now()
                from_date = now.replace(day=1, hour=0, minute=0, second=0)
                start_of_year = datetime(now.year, 1, 1, 0, 0, 0)
        except ValueError:
            return await interaction.followup.send("Invalid from_date format. Please use YYYY-MM-DD.", ephemeral=True)

        try:
            to_date = datetime.strptime(to_date, '%Y-%m-%d') if to_date else datetime.now()
            to_date = to_date.replace(hour=23, minute=59, second=59)
        except ValueError:
            return await interaction.followup.send("Invalid to_date format. Please use YYYY-MM-DD.", ephemeral=True)

        user_logs = await self.get_user_logs(user_id, start_of_year, to_date, immersion_type)
        logs_df = pd.DataFrame(user_logs, columns=['media_type', 'amount_logged', 'points_received', 'log_date'])
        logs_df['log_date'] = pd.to_datetime(logs_df['log_date'])
        logs_df = logs_df.set_index('log_date')

        if logs_df[from_date:to_date].empty:
            return await interaction.followup.send("No logs found for the specified period.", ephemeral=True)
        figure_buffer_bar = await asyncio.to_thread(generate_bar_chart, logs_df, from_date, to_date, immersion_type)
        figure_buffer_heatmap = await asyncio.to_thread(generate_heatmap, logs_df, from_date, to_date, immersion_type)

        breakdown_str, points_total = await asyncio.to_thread(embedded_info, logs_df[from_date:to_date])
        timeframe_str = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}"

        embed = discord.Embed(title="Immersion Overview", color=discord.Color.blurple())
        embed.add_field(name="User", value=user_name, inline=True)
        embed.add_field(name="Timeframe", value=timeframe_str, inline=True)
        embed.add_field(name="Points", value=f"{points_total:.2f}", inline=True)
        if immersion_type:
            embed.add_field(name="Immersion Type", value=immersion_type.capitalize(), inline=True)
        embed.add_field(name="Breakdown", value=breakdown_str, inline=False)

        file_bar = discord.File(figure_buffer_bar, filename='bar_chart.png')
        file_heatmap = discord.File(figure_buffer_heatmap, filename='heatmap.png')
        embed.set_image(url="attachment://bar_chart.png")

        await interaction.followup.send(file=file_bar, embed=embed)
        await interaction.followup.send(file=file_heatmap)


async def setup(bot):
    await bot.add_cog(ImmersionLogMe(bot))

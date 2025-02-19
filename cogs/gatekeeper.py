from lib.bot import TMWBot
import discord
import re
import aiohttp
import asyncio
import yaml
import os
from typing import Optional
from datetime import datetime, timedelta, timezone
from discord.ext import commands, tasks
from discord.utils import utcnow


KOTOBA_BOT_ID = 251239170058616833

GATEKEEPER_SETTINGS_PATH = os.getenv("ALT_GATEKEEPER_SETTINGS_PATH") or "config/gatekeeper_settings.yml"
with open(GATEKEEPER_SETTINGS_PATH, "r", encoding="utf-8") as f:
    gatekeeper_settings = yaml.safe_load(f)

CREATE_QUIZ_ATTEMPTS_TABLE = """
    CREATE TABLE IF NOT EXISTS quiz_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    quiz_name TEXT NOT NULL,
    created_at TIMESTAMP);"""

CREATE_PASSED_QUIZZES_TABLE = """
    CREATE TABLE IF NOT EXISTS passed_quizzes (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    quiz_name TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id, quiz_name));"""

CREATE_USER_THREADS_TABLE = """
    CREATE TABLE IF NOT EXISTS user_threads (
    user_id INTEGER NOT NULL,
    thread_id INTEGER NOT NULL,
    PRIMARY KEY (user_id)
);"""

ADD_QUIZ_ATTEMPT = """INSERT INTO quiz_attempts (guild_id, user_id, quiz_name, created_at) VALUES (?,?,?,?);"""

GET_LAST_QUIZ_ATTEMPT = """SELECT quiz_name, created_at FROM quiz_attempts
                        WHERE guild_id = ? AND user_id = ? AND quiz_name = ? ORDER BY created_at DESC LIMIT 1;"""

RESET_ALL_QUIZ_ATTEMPTS = """DELETE FROM quiz_attempts WHERE guild_id = ? AND user_id = ?"""

RESET_SPECIFIC_QUIZ_ATTEMPTS = """DELETE FROM quiz_attempts WHERE guild_id = ? AND user_id = ? AND quiz_name = ?"""

ADD_PASSED_QUIZ = """INSERT INTO passed_quizzes (guild_id, user_id, quiz_name) VALUES (?,?,?)
                    ON CONFLICT(guild_id, user_id, quiz_name) DO NOTHING;"""

GET_PASSED_QUIZZES = """SELECT quiz_name FROM passed_quizzes WHERE guild_id = ? AND user_id = ?;"""

ADD_USER_THREAD = """INSERT INTO user_threads (user_id, thread_id) VALUES (?, ?)
                     ON CONFLICT(user_id) DO UPDATE SET thread_id = excluded.thread_id;"""

GET_USER_THREAD = """SELECT thread_id FROM user_threads WHERE user_id = ?;"""


async def quiz_autocomplete(interaction: discord.Interaction, current_input: str):
    rank_names = [quiz['name'] for quiz in gatekeeper_settings['rank_structure']
                  [interaction.guild.id] if quiz["combination_rank"] is False and quiz["no_timeout"] is False]
    possible_choices = [discord.app_commands.Choice(name=rank_name, value=rank_name) for rank_name in rank_names]
    return possible_choices[0:25]


async def verify_quiz_settings(quiz_data, quiz_result, member: discord.Member):
    """Ensures a user didn't use cheat settings for the quiz."""
    answer_count = quiz_data["score_limit"]
    answer_time_limit = quiz_data["time_limit"]
    font = quiz_data["font"]
    font_size = quiz_data["font_size"]
    fail_count = quiz_data["max_missed"]

    foreground_color = quiz_data["foreground"]
    effect = quiz_data["effect"]

    if quiz_data["deck_range"]:
        start_index, end_index = quiz_data["deck_range"]
        index_specified = True
    else:
        index_specified = False

    user_count = len(quiz_result["participants"])
    if user_count > 1:
        return False, "Quiz failed due to multiple people participating."

    shuffle = quiz_result["settings"]["shuffle"]
    if not shuffle:
        return False, "Quiz failed due to the shuffle setting being activated."

    is_loaded = quiz_result["isLoaded"]
    if is_loaded:
        return False, "Quiz failed due to being loaded."

    for deck in quiz_result["decks"]:
        if deck["mc"]:
            return False, "Quiz failed due to being set to multiple choice."

    if index_specified:
        for deck in quiz_result["decks"]:
            try:
                if deck["startIndex"] != start_index:
                    return False, "Quiz failed due to having the wrong start index."
                if deck["endIndex"] != end_index:
                    return False, "Quiz failed due to having the wrong end index."
            except KeyError:
                return False, "Quiz failed due to not having an index specified."
    else:
        for deck in quiz_result["decks"]:
            try:
                if deck["startIndex"]:
                    return False, "Quiz failed due to having a start index."
            except KeyError:
                pass
            try:
                if deck["endIndex"]:
                    return False, "Quiz failed due to having an end index."
            except KeyError:
                pass

    if foreground_color:
        if quiz_result["settings"]["fontColor"] != foreground_color:
            return False, "Foreground color does not match required color."

    if effect:
        if quiz_result["settings"]["effect"] != effect:
            return False, "Effect does not match required effect."

    if answer_count != quiz_result["settings"]["scoreLimit"]:
        return False, "Set score limit and required score limit don't match."

    if answer_time_limit < quiz_result["settings"]["answerTimeLimitInMs"]:
        return False, "Set answer time does match required answer time."

    if font and font != quiz_result["settings"]["font"]:
        return False, "Set font does not match required font."

    if font_size and font_size != quiz_result["settings"]["fontSize"]:
        return False, "Set font size does not match required font size."

    failed_question_count = len(quiz_result["questions"]) - quiz_result["scores"][0]["score"]
    if failed_question_count >= fail_count:
        return False, f"Failed too many questions. Score: {quiz_result['scores'][0]['score']} out of {answer_count}."

    if answer_count != quiz_result["scores"][0]["score"]:
        return False, f"Not enough questions answered. Score: {quiz_result['scores'][0]['score']} out of {answer_count}."

    return (
        True,
        f"{member.mention} has passed the {quiz_data['name']} quiz!")


async def get_quiz_id(message: discord.Message):
    """Extract the ID of a quiz to use with the API."""
    try:
        if "Ended" in message.embeds[0].title:
            return re.findall(r"game_reports/([\da-z]*)", message.embeds[0].fields[-1].value)[0]
    except IndexError:
        return False
    except TypeError:
        return False


kotoba_request_lock = asyncio.Lock()
thread_deletion_lock = asyncio.Lock()


async def extract_quiz_result_from_id(quiz_id):
    async with kotoba_request_lock:
        await asyncio.sleep(2)
        jsonurl = f"https://kotobaweb.com/api/game_reports/{quiz_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(jsonurl) as resp:
                return await resp.json()


async def timeout_member(member: discord.Member, duration_in_minutes: int, reason: str):
    try:
        await member.timeout(utcnow() + timedelta(minutes=duration_in_minutes), reason=reason)
    except discord.Forbidden:
        pass


def get_next_sunday_midnight_from(dt):
    days_until_sunday = (6 - dt.weekday()) % 7
    if days_until_sunday == 0:
        days_until_sunday = 7
    next_sunday = dt + timedelta(days=days_until_sunday)
    next_sunday_midnight = datetime(next_sunday.year, next_sunday.month, next_sunday.day, 0, 0, 0, tzinfo=timezone.utc)
    return next_sunday_midnight


async def delete_inactive_threads(channel: discord.TextChannel):
    for thread in channel.threads:
        last_message = thread.last_message
        if not last_message:
            last_message_id = thread.last_message_id
            async with thread_deletion_lock:
                await asyncio.sleep(1)
                last_message = await thread.fetch_message(last_message_id)

        if not last_message:
            continue

        if last_message.created_at < utcnow() - timedelta(minutes=20):
            async with thread_deletion_lock:
                await asyncio.sleep(5)
                await thread.delete(reason="Thread inactive for over 20 minutes.")


class DynamicQuizMenu(discord.ui.DynamicItem[discord.ui.Select[discord.ui.View]], template=r"quizmenu-guild:(?P<guild_id>\d+)"):
    def __init__(self, levelup: "LevelUp", guild_id: int):
        self.levelup = levelup
        self.guild_id = guild_id
        rank_names = [
            (quiz["name"], quiz.get("emoji"))
            for quiz in gatekeeper_settings["rank_structure"][guild_id]
            if quiz["command"]
        ]
        super().__init__(
            discord.ui.Select(
                custom_id=f"quizmenu-guild:{guild_id}",
                options=[
                    discord.SelectOption(
                        label=name,
                        emoji=emoji,
                        description=f"Select to take the {name} quiz!",
                    )
                    for name, emoji in rank_names
                ],
                placeholder="Click here to take a quiz!",
                min_values=1,
                max_values=1,
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item, match: re.Match[str]) -> discord.ui.DynamicItem:
        guild_id = int(match.group("guild_id"))
        levelup = interaction.client.get_cog("LevelUp")
        if not levelup:
            raise RuntimeError("LevelUp cog is not loaded.")
        return cls(levelup, guild_id)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        assert interaction.data is not None and "custom_id" in interaction.data, "Invalid interaction data"
        rank = self.item.values[0]
        guild_id = interaction.guild.id
        rank_structure = gatekeeper_settings['rank_structure'][guild_id]
        quiz_command = None

        for quiz in rank_structure:
            if quiz['name'].lower() == rank.lower():
                quiz_command = quiz['command']
                break

        rank_has_cooldown = await self.levelup.rank_has_cooldown(interaction.guild.id, rank)
        is_on_cooldown, cooldown_message = await self.levelup.is_on_cooldown_create(interaction.user, rank, rank_has_cooldown)
        if is_on_cooldown:
            await interaction.followup.send(cooldown_message, ephemeral=True)
            return

        await interaction.followup.send(f"Creating your quiz thread for {rank}. Good luck!", ephemeral=True)

        quiz_thread_record = await self.levelup.bot.GET_ONE(GET_USER_THREAD, (interaction.user.id,))
        quiz_thread = None
        if quiz_thread_record:
            thread_id = quiz_thread_record[0]
            quiz_thread = interaction.guild.get_thread(thread_id)
        if quiz_thread is None:
            quiz_thread = await interaction.channel.create_thread(
                name=f"{interaction.user.name} - Quiz"[:100],
                auto_archive_duration=60,
                reason='Quiz Thread'
            )
            await self.levelup.bot.RUN(ADD_USER_THREAD, (interaction.user.id, quiz_thread.id))

        kotoba_bot_user = interaction.guild.get_member(KOTOBA_BOT_ID)
        if not kotoba_bot_user:
            kotoba_bot_user = await interaction.guild.fetch_member(KOTOBA_BOT_ID)
        if kotoba_bot_user not in quiz_thread.members:
            await quiz_thread.add_user(kotoba_bot_user)
        if interaction.user not in quiz_thread.members:
            await quiz_thread.add_user(interaction.user)

        await quiz_thread.send(f"{interaction.user.mention} To begin the {rank} quiz, copy and paste the following command exactly:")
        await quiz_thread.send(f"{quiz_command}")


class LevelUp(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot

    async def cog_load(self):
        await self.bot.RUN(CREATE_QUIZ_ATTEMPTS_TABLE)
        await self.bot.RUN(CREATE_PASSED_QUIZZES_TABLE)
        await self.bot.RUN(CREATE_USER_THREADS_TABLE)

        self.bot.add_dynamic_items(DynamicQuizMenu)
        self.inactive_quiz_thread_deleter.start()

    @tasks.loop(minutes=1)
    async def inactive_quiz_thread_deleter(self):
        for guild_id in gatekeeper_settings['rank_settings']:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            channel_id = gatekeeper_settings['rank_settings'][guild_id]['quiz_channel']
            channel = guild.get_channel(channel_id)
            if not channel:
                continue
            await delete_inactive_threads(channel)

    async def is_in_levelup_channel(self, message: discord.Message):
        thread_id = await self.bot.GET_ONE(GET_USER_THREAD, (message.author.id,))
        if thread_id and message.channel.id == thread_id[0]:
            return True
        return False

    async def is_restricted_quiz(self, message: discord.Message):
        restricted_quizzes = gatekeeper_settings['rank_settings'][message.guild.id]['restricted_quiz_names']
        for quiz_name in restricted_quizzes:
            if quiz_name.lower() in message.content.lower():
                return quiz_name, True
        return None, False

    async def is_valid_quiz(self, message: discord.Message, rank_structure: dict):
        for quiz in rank_structure:
            if message.content == quiz['command']:
                return True, quiz['name']
        return False, None

    async def rank_has_cooldown(self, guild_id: int, rank_name: str):
        rank_structure = gatekeeper_settings['rank_structure'][guild_id]
        for rank in rank_structure:
            if rank['name'] == rank_name:
                return not rank['no_timeout']

    async def is_command_input_valid(self, message: discord.Message):
        if message.author.bot:
            return True

        restricted_quiz_name, is_restricted = await self.is_restricted_quiz(message)
        is_in_levelup_channel = await self.is_in_levelup_channel(message)
        is_valid_quiz, performed_quiz_name = await self.is_valid_quiz(message, gatekeeper_settings['rank_structure'][message.guild.id])

        rank_has_cooldown = await self.rank_has_cooldown(message.guild.id, performed_quiz_name)

        is_on_cooldown = await self.is_on_cooldown(message, performed_quiz_name, rank_has_cooldown)
        if is_on_cooldown:
            await timeout_member(message.author, 2, "Quiz on cooldown.")
            return False

        if is_in_levelup_channel and not is_valid_quiz:
            await message.channel.send(f"{message.author.mention} Please copy and paste the command **exactly** and try again.")
            await timeout_member(message.author, 2, "Invalid quiz attempt.")
            return False

        if is_restricted:
            if not is_in_levelup_channel or not is_valid_quiz:
                await message.channel.send(f"{message.author.mention} {restricted_quiz_name} quiz is restricted.\nYou can only use it in the level-up channel with the exact commands.")
                await timeout_member(message.author, 2, "Restricted quiz attempt.")
                return False

        if is_valid_quiz and not is_in_levelup_channel:
            await message.channel.send(f"{message.author.mention} Please use this quiz command in the level-up channels.")
            await timeout_member(message.author, 2, "Invalid channel for quiz attempt.")
            return False

        return True

    async def is_on_cooldown(self, message: discord.Message, quiz_name, rank_has_cooldown):
        if not rank_has_cooldown:
            return False
        last_attempt = await self.bot.GET_ONE(GET_LAST_QUIZ_ATTEMPT, (message.guild.id, message.author.id, quiz_name))
        if not last_attempt:
            return False
        quiz_name, last_attempt_time = last_attempt
        last_attempt_time = datetime.fromisoformat(last_attempt_time)
        next_sunday_midnight = get_next_sunday_midnight_from(last_attempt_time)
        if utcnow() < next_sunday_midnight:
            unix_timestamp = int(next_sunday_midnight.timestamp())
            await message.channel.send(
                f"{message.author.mention} You can only attempt this quiz once per week. Your next attempt will be available <t:{unix_timestamp}:R> (on <t:{unix_timestamp}:F>).")
            return True
        return False

    async def register_quiz_attempt(self, member: discord.Member, channel: discord.TextChannel, quiz_name):
        await self.bot.RUN(ADD_QUIZ_ATTEMPT, (member.guild.id, member.id, quiz_name, utcnow()))
        next_sunday_midnight = get_next_sunday_midnight_from(utcnow())
        unix_timestamp = int(next_sunday_midnight.timestamp())
        await channel.send(f"{member.mention} registered attempt for {quiz_name}. You can try again <t:{unix_timestamp}:R> (on <t:{unix_timestamp}:F>).")

    async def get_corresponding_quiz_data(self, message: discord.Message, quiz_result: dict):
        rank_structure = gatekeeper_settings['rank_structure'][message.guild.id]
        if not quiz_result["decks"][0].get("shortName"):
            return None
        deck_names = [deck['shortName'] for deck in quiz_result["decks"]]
        index_specified = bool(quiz_result["decks"][0].get("startIndex"))
        for rank in rank_structure:
            index_required = rank.get("deck_range", None) is not None
            rank_decks = set(rank["decks"]) if rank.get("decks") is not None else set()
            if rank_decks == set(deck_names) and index_required == index_specified:
                return rank
        return None

    async def get_all_quiz_roles(self, guild: discord.Guild):
        rank_structure = gatekeeper_settings['rank_structure'][guild.id]
        return [guild.get_role(role['rank_to_get']) for role in rank_structure if role['rank_to_get']]

    async def reward_user(self, member: discord.Member, quiz_data: dict):
        await self.bot.RUN(ADD_PASSED_QUIZ, (member.guild.id, member.id, quiz_data['name']))
        if quiz_data['rank_to_get']:
            roles = [role for role in await self.get_all_quiz_roles(member.guild) if role is not None]
            role_to_get = member.guild.get_role(quiz_data['rank_to_get'])
            await member.remove_roles(*roles)
            await member.add_roles(role_to_get)
            return role_to_get
        else:
            await self.check_if_combination_rank_earned(member)

    async def check_if_combination_rank_earned(self, member: discord.Member):
        rank_structure = gatekeeper_settings['rank_structure'][member.guild.id]
        combination_ranks = [rank_data for rank_data in rank_structure if rank_data['combination_rank'] is True]
        earned_ranks = await self.bot.GET(GET_PASSED_QUIZZES, (member.guild.id, member.id))
        earned_ranks = [rank[0] for rank in earned_ranks]
        combination_ranks.reverse()
        for rank in combination_ranks:

            if await self.already_owns_higher_or_same_role(rank['rank_to_get'], member):
                return

            if all(quiz_name in earned_ranks for quiz_name in rank['quizzes_required']):
                role_to_get = await self.reward_user(member, rank)
                await self.send_in_announcement_channel(member, f"{member.mention} is now a {role_to_get.name}!")

    async def send_in_announcement_channel(self, member: discord.Member, message: str):
        announcement_channel = member.guild.get_channel(
            gatekeeper_settings['rank_settings'][member.guild.id]['announce_channel'])
        await announcement_channel.send(message)

    async def already_owns_higher_or_same_role(self, rank_to_get_id: int, member: discord.Member):
        role_to_get = member.guild.get_role(rank_to_get_id)
        if not role_to_get:
            return False

        all_rank_roles = sorted(
            [role for role in await self.get_all_quiz_roles(member.guild) if role is not None],
            key=lambda r: r.position,
            reverse=True
        )

        for role in member.roles:
            if role in all_rank_roles and role.position >= role_to_get.position:
                return True
        return False

    async def get_next_attempt_time(self, guild_id: int, user_id: int, quiz_name: str) -> Optional[int]:
        """Returns the Unix timestamp of when the user can next attempt the quiz."""
        last_attempt = await self.bot.GET_ONE(GET_LAST_QUIZ_ATTEMPT, (guild_id, user_id, quiz_name))
        if not last_attempt:
            return None

        quiz_name, last_attempt_time = last_attempt
        last_attempt_time = datetime.fromisoformat(last_attempt_time)
        next_attempt_time = last_attempt_time + timedelta(days=6)
        return int(next_attempt_time.timestamp())

    @commands.Cog.listener(name="on_message")
    async def level_up_routine(self, message: discord.Message):
        if message.author == self.bot.user:
            return

        if not message.guild:
            return

        if not message.author.id == KOTOBA_BOT_ID and not 'k!q' in message.content.lower():
            return

        is_valid_command = await self.is_command_input_valid(message)
        if not is_valid_command:
            return

        quiz_id = await get_quiz_id(message)
        if not quiz_id:
            return

        quiz_result = await extract_quiz_result_from_id(quiz_id)
        quiz_data = await self.get_corresponding_quiz_data(message, quiz_result)
        if not quiz_data:
            return

        member = message.guild.get_member(int(quiz_result["participants"][0]["discordUser"]["id"]))

        success, quiz_message = await verify_quiz_settings(quiz_data, quiz_result, member)

        if await self.already_owns_higher_or_same_role(quiz_data['rank_to_get'], member):
            return

        if success and quiz_data['require_role']:
            role_to_have = message.guild.get_role(quiz_data['require_role'])
            if role_to_have not in member.roles:
                await message.channel.send(
                    f"{member.mention} You need the {role_to_have.mention} role to take this quiz.",
                    allowed_mentions=discord.AllowedMentions(roles=False)
                )
                return

        if success:
            await self.reward_user(member, quiz_data)
            await self.send_in_announcement_channel(member, quiz_message)
            try:
                await member.send(f"Congratulations! You passed the {quiz_data['name']} quiz!")
            except discord.Forbidden:
                pass
        else:
            if await self.rank_has_cooldown(message.guild.id, quiz_data['name']):
                await self.register_quiz_attempt(member, message.channel, quiz_data['name'])

                next_sunday_midnight = get_next_sunday_midnight_from(utcnow())
                next_attempt = int(next_sunday_midnight.timestamp())
                if next_attempt:
                    try:
                        await member.send(
                            f"Your attempt at the {quiz_data['name']} quiz was unsuccessful: {quiz_message}\n"
                            f"You can try again <t:{next_attempt}:R> (on <t:{next_attempt}:F>).")
                    except discord.Forbidden:
                        pass

    @discord.app_commands.command(name="reset_user_cooldown",  description="Reset a users quiz cooldown.")
    @discord.app_commands.guild_only()
    @discord.app_commands.describe(user="The user to clear the cooldown of.", quiz_to_reset="The quiz to reset the cooldown for.")
    @discord.app_commands.autocomplete(quiz_to_reset=quiz_autocomplete)
    @discord.app_commands.default_permissions(administrator=True)
    async def clear_user_cooldown(self, interaction: discord.Interaction, user: discord.Member, quiz_to_reset: Optional[str]):
        if not quiz_to_reset:
            await self.bot.RUN(RESET_ALL_QUIZ_ATTEMPTS, (interaction.guild.id, user.id))
            await interaction.response.send_message(f"Cleared all quiz cooldown for {user.mention}.")
        else:
            if not any(quiz_to_reset in rank['name'] for rank in gatekeeper_settings['rank_structure'][interaction.guild.id]):
                await interaction.response.send_message("Invalid quiz name.", ephemeral=True)
                return
            await self.bot.RUN(RESET_SPECIFIC_QUIZ_ATTEMPTS, (interaction.guild.id, user.id, quiz_to_reset))
            await interaction.response.send_message(f"Cleared quiz cooldown for {user.mention} for `{quiz_to_reset}`.")

    @discord.app_commands.command(name="ranktable",  description="Display the distribution of quiz roles in the server.")
    @discord.app_commands.guild_only()
    async def ranktable(self, interaction: discord.Interaction):
        quiz_roles = await self.get_all_quiz_roles(interaction.guild)
        total_ranked_members = len(set([member for role in quiz_roles for member in role.members]))

        description = "\n".join([
            f"{role.mention}: {len(role.members)} ({len(role.members) / total_ranked_members * 100:.2f}%)"
            for role in quiz_roles
        ])

        description += f"\n\nTotal ranked members: {total_ranked_members}"
        description += f"\nTotal member count: {interaction.guild.member_count}"

        embed = discord.Embed(title=f"Role Distribution", description=description, color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @discord.app_commands.command(name="rankusers", description="See all users with a specific role.")
    @discord.app_commands.describe(role="Role for which all members should be displayed.")
    @discord.app_commands.guild_only()
    async def rankusers(self, interaction: discord.Interaction, role: discord.Role):
        member_count = len(role.members)
        mention_string = []
        for member in role.members:
            mention_string.append(member.mention)
        if len(" ".join(mention_string)) < 500:
            mention_string.append(f"\n\nA total {member_count} members have the role {role.mention}.")
            await interaction.response.send_message(" ".join(mention_string), allowed_mentions=discord.AllowedMentions.none())
        else:
            member_string = [str(member) for member in role.members]
            member_string.append(f"\nTotal {member_count} members.")
            with open("data/rank_user_count.txt", "w", encoding="utf-8") as text_file:
                text_file.write("\n".join(member_string))
            await interaction.response.send_message("List of role members too large. Providing role member list in a file:", file=discord.File("data/rank_user_count.txt"))
            os.remove("data/rank_user_count.txt")

    async def rank_to_get(self, guild_id: int, rank: dict):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return "`Unretreivable`"
        else:
            return guild.get_role(rank['rank_to_get']).mention

    @discord.app_commands.command(name="list_role_commands", description="List all commands required for the quizzes.")
    @discord.app_commands.describe(guild_id="The guild for which to display the role commands.")
    @discord.app_commands.guild_only()
    async def list_role_commands(self, interaction: discord.Interaction, guild_id: Optional[str]):
        if guild_id and not guild_id.isdigit():
            await interaction.response.send_message("Invalid command input", ephemeral=True)
        elif guild_id:
            guild_id = int(guild_id)
        else:
            guild_id = interaction.guild.id

        rank_structure = gatekeeper_settings['rank_structure'][guild_id]

        rank_command_embed = discord.Embed(title="Rank Commands", color=discord.Color.blurple())

        for rank in rank_structure:
            if rank['command']:
                next_attempt_time = await self.get_next_attempt_time(guild_id, interaction.user.id, rank['name'])
                if next_attempt_time and next_attempt_time < int(utcnow().timestamp()):
                    next_attempt_time = None

                description = rank['command'] + "\n"
                if rank['rank_to_get']:
                    rank_to_get = await self.rank_to_get(guild_id, rank)
                    description += f"Reward role: {rank_to_get}\n"

                if next_attempt_time and not rank['combination_rank']:
                    description += f"Cooldown: <t:{next_attempt_time}:R> (on <t:{next_attempt_time}:F>)"
                else:
                    description += "Cooldown: Not on cooldown."

                if rank['require_role']:
                    description += f"\nRequired role: {interaction.guild.get_role(rank['require_role']).mention}"

                rank_command_embed.add_field(name=rank['name'], value=description, inline=False)

            elif rank['combination_rank']:
                rank_to_get = await self.rank_to_get(guild_id, rank)
                rank_command_embed.add_field(name=rank['name'],
                                             value=f"Required quizzes: {', '.join(rank['quizzes_required'])}" +
                                             f"\nReward role: {rank_to_get}", inline=False)

        await interaction.response.send_message(embed=rank_command_embed, ephemeral=True)

    async def is_on_cooldown_create(self, member: discord.Member, quiz_name: str, rank_has_cooldown: bool):
        if not rank_has_cooldown:
            return False, None
        last_attempt = await self.bot.GET_ONE(GET_LAST_QUIZ_ATTEMPT, (member.guild.id, member.id, quiz_name))
        if not last_attempt:
            return False, None
        quiz_name, last_attempt_time = last_attempt
        last_attempt_time = datetime.fromisoformat(last_attempt_time)
        next_sunday_midnight = get_next_sunday_midnight_from(last_attempt_time)
        if utcnow() < next_sunday_midnight:
            unix_timestamp = int(next_sunday_midnight.timestamp())
            cooldown_message = (
                f"You can only attempt this quiz once per week. Your next attempt will be available <t:{unix_timestamp}:R> (on <t:{unix_timestamp}:F>)."
            )
            return True, cooldown_message
        return False, None

    @discord.app_commands.command(
        name="create_quiz_menu",
        description="Creates the menu for the quizzes in the current channel.",
    )
    @discord.app_commands.guild_only()
    @discord.app_commands.default_permissions(administrator=True)
    async def create_quiz_menu(self, interaction: discord.Interaction):
        await interaction.response.send_message("Creating menu...", ephemeral=True)

        view = discord.ui.View(timeout=None)
        view.add_item(DynamicQuizMenu(self, interaction.guild.id))
        quiz_menu_message = gatekeeper_settings['rank_settings'][interaction.guild.id]['quiz_menu_message']
        await interaction.channel.send(quiz_menu_message, view=view)


async def setup(bot):
    await bot.add_cog(LevelUp(bot))

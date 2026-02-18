import discord
import yaml
import os
from discord.ext import commands, tasks
from lib.bot import TMWBot
from discord.utils import utcnow
import aiohttp

DAILY_QUESTIONS_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS daily_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

GET_RECENT_QUESTIONS = """
SELECT question FROM daily_questions 
WHERE guild_id = ? AND channel_id = ?
ORDER BY created_at DESC LIMIT 10;
"""

GET_TODAYS_QUESTION = """
SELECT question FROM daily_questions 
WHERE guild_id = ? AND channel_id = ? 
AND date(created_at) = date('now')
LIMIT 1;
"""

INSERT_QUESTION = """
INSERT INTO daily_questions (guild_id, channel_id, question, created_at)
VALUES (?, ?, ?, ?);
"""

PROMPT = """Create a daily question in Japanese that is easy to respond to and encourages conversation.
Here are the last questions that were asked (avoid similar topics):

{recent_questions_str}

Provide only the question text in Japanese, nothing else."""


DAILY_QUESTIONS_SETTINGS_PATH = os.getenv("DAILY_QUESTIONS_SETTINGS_PATH") or "config/daily_questions_settings.yml"
with open(DAILY_QUESTIONS_SETTINGS_PATH, "r", encoding="utf-8") as f:
    daily_questions_settings = yaml.safe_load(f)


class DailyQuestion(commands.Cog):
    def __init__(self, bot: TMWBot):
        self.bot = bot
        self.api_key = os.getenv("OPENAI_KEY")

    async def cog_load(self):
        await self.bot.RUN(DAILY_QUESTIONS_CREATE_TABLE)
        if not self.api_key:
            return
        self.check_daily_questions.start()

    def cog_unload(self):
        self.check_daily_questions.cancel()

    async def get_question_prompt(self, guild_id: int, channel_id: int) -> str:
        recent_questions = await self.bot.GET(GET_RECENT_QUESTIONS, (guild_id, channel_id))
        recent_questions_str = "\n".join([q[0] for q in recent_questions])

        return PROMPT.format(recent_questions_str=recent_questions_str)

    async def generate_question(self, guild_id: int, channel_id: int) -> str:
        prompt = await self.get_question_prompt(guild_id, channel_id)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a helpful assistant that creates engaging daily questions in Japanese."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.9,
            "max_tokens": 200
        }

        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload) as response:
                if response.status != 200:
                    error_data = await response.json()
                    raise Exception(f"OpenAI API error: {error_data}")

                data = await response.json()
                return data['choices'][0]['message']['content'].strip()

    async def post_daily_question(self, guild_id: int, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        existing_question = await self.bot.GET_ONE(GET_TODAYS_QUESTION, (guild_id, channel_id))
        if existing_question:
            return

        try:
            question = await self.generate_question(guild_id, channel_id)
            current_time = utcnow()

            await self.bot.RUN(INSERT_QUESTION, (guild_id, channel_id, question, current_time))

            embed = discord.Embed(title="今日の質問 / Daily Question", description=question, color=discord.Color.blue(),)

            await channel.send(embed=embed)

        except Exception as e:
            raise

    @tasks.loop(minutes=1)
    async def check_daily_questions(self):
        for guild_id, settings in daily_questions_settings.items():
            guild_id = int(guild_id)
            for channel_id in settings['channels']:
                channel_id = int(channel_id)
                await self.post_daily_question(guild_id, channel_id)


async def setup(bot):
    await bot.add_cog(DailyQuestion(bot))

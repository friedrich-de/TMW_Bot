import aiohttp
import discord
import os
from discord.ext import commands
from discord.ext import tasks

from lib.bot import TMWBot

CACHED_TMDB_RESULTS_CREATE_TABLE_QUERY = """
CREATE TABLE IF NOT EXISTS cached_tmdb_results (
    primary_key INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id INTEGER UNIQUE,
    title TEXT,
    original_title TEXT,
    poster_path TEXT,
    media_type TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_TMDB_FTS5_TABLE_QUERY = """
CREATE VIRTUAL TABLE IF NOT EXISTS tmdb_fts USING fts5(
    tmdb_id UNINDEXED,
    title,
    original_title,
    poster_path UNINDEXED,
    media_type UNINDEXED,
    content='cached_tmdb_results',
    tokenize = 'porter'
);
"""

CREATE_TMDB_TRIGGER_INSERT = """
CREATE TRIGGER IF NOT EXISTS tmdb_fts_insert AFTER INSERT ON cached_tmdb_results
BEGIN
  INSERT INTO tmdb_fts(rowid, tmdb_id, title, original_title, media_type)
  VALUES (new.rowid, new.tmdb_id, new.title, new.original_title, new.media_type);
END;
"""

CREATE_TMDB_TRIGGER_UPDATE = """
CREATE TRIGGER IF NOT EXISTS tmdb_fts_update AFTER UPDATE ON cached_tmdb_results
BEGIN
  UPDATE tmdb_fts SET 
    title = new.title,
    original_title = new.original_title,
    media_type = new.media_type
  WHERE rowid = old.rowid;
END;
"""

CREATE_TMDB_TRIGGER_DELETE = """
CREATE TRIGGER IF NOT EXISTS tmdb_fts_delete AFTER DELETE ON cached_tmdb_results
BEGIN
  DELETE FROM tmdb_fts WHERE rowid = old.rowid;
END;
"""

CACHED_TMDB_RESULTS_INSERT_QUERY = """
INSERT INTO cached_tmdb_results (tmdb_id, title, original_title, poster_path, media_type)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(tmdb_id) DO UPDATE SET
    title=excluded.title,
    original_title=excluded.original_title,
    poster_path=excluded.poster_path,
    media_type=excluded.media_type,
    timestamp=CURRENT_TIMESTAMP;
"""

CACHED_TMDB_RESULTS_SEARCH_QUERY = """
SELECT tmdb_id, title, original_title, poster_path, media_type
FROM tmdb_fts
WHERE (title LIKE '%' || ? || '%' OR original_title LIKE '%' || ? || '%')
LIMIT 10;
"""

CACHED_TMDB_THUMBNAIL_QUERY = """
SELECT poster_path FROM cached_tmdb_results
WHERE tmdb_id = ?;
"""

CACHED_TMDB_TITLE_QUERY = """
SELECT title FROM cached_tmdb_results
WHERE tmdb_id = ?;
"""

CACHED_TMDB_GET_MEDIA_TYPE_QUERY = """
SELECT media_type FROM cached_tmdb_results
WHERE tmdb_id = ?;
"""


async def query_tmdb(interaction: discord.Interaction, current_input: str, bot: TMWBot):
    api_key = os.getenv("TMDB_API_KEY")
    if not api_key:
        raise ValueError("TMDB API Key not found in environment variables")

    url = f"https://api.themoviedb.org/3/search/multi?api_key={api_key}&query={current_input}"
    base_image_url = "https://image.tmdb.org/t/p/original"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                media_list = data.get("results", [])

                choices = []
                for media in media_list:
                    media_id = media.get("id")
                    title = media.get("name") or media.get("title")
                    original_title = media.get("original_name") or media.get("original_title")
                    media_type = media.get("media_type")
                    poster_path = media.get("poster_path")
                    poster_path = f"{base_image_url}{poster_path}" if poster_path else None
                    if not title or not media_id:
                        continue

                    choice_name = f"{title[:80]} (ID: {media_id}) (API)"[:100]
                    if title:
                        choices.append(discord.app_commands.Choice(name=choice_name, value=str(media_id)))

                    await bot.RUN(CACHED_TMDB_RESULTS_INSERT_QUERY, (media_id, title, original_title, poster_path, media_type))

                return choices[:10]
            elif response.status == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                print(f"API rate limit exceeded. Please wait {retry_after} seconds before retrying.")
                return []
            else:
                return []


async def listening_autocomplete(interaction: discord.Interaction, current_input: str):
    tmw_bot = interaction.client
    tmw_bot: TMWBot

    cached_results = await tmw_bot.GET(CACHED_TMDB_RESULTS_SEARCH_QUERY, (current_input, current_input))
    choices = []
    for cached_result in cached_results:
        tmdb_id, title, original_title, _, _ = cached_result
        choice_name = f"{title[:80]} (ID: {tmdb_id}) (Cached)"[:100]
        choices.append(discord.app_commands.Choice(name=choice_name, value=str(tmdb_id)))

    if len(choices) < 1:
        tmdb_choices = await query_tmdb(interaction, current_input, tmw_bot)
        choices.extend(tmdb_choices)

    return choices[:10]

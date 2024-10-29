import aiohttp
import discord
from discord.ext import commands
from discord.ext import tasks

from lib.bot import TMWBot

CACHED_VNDB_RESULTS_CREATE_TABLE_QUERY = """
CREATE TABLE IF NOT EXISTS cached_vndb_results (
    primary_key INTEGER PRIMARY KEY AUTOINCREMENT,
    vndb_id TEXT UNIQUE,
    title TEXT,
    cover_image_url TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    cover_image_nsfw INTEGER DEFAULT 0
);
"""

CREATE_VNDB_FTS5_TABLE_QUERY = """
CREATE VIRTUAL TABLE IF NOT EXISTS vndb_fts USING fts5(
    vndb_id UNINDEXED,
    title,
    cover_image_url UNINDEXED,
    content='cached_vndb_results',
    tokenize = 'porter'
);
"""

CREATE_VNDB_TRIGGER_INSERT = """
CREATE TRIGGER IF NOT EXISTS vndb_fts_insert AFTER INSERT ON cached_vndb_results
BEGIN
  INSERT INTO vndb_fts(rowid, vndb_id, title)
  VALUES (new.rowid, new.vndb_id, new.title);
END;
"""

CREATE_VNDB_TRIGGER_UPDATE = """
CREATE TRIGGER IF NOT EXISTS vndb_fts_update AFTER UPDATE ON cached_vndb_results
BEGIN
  UPDATE vndb_fts SET 
    title = new.title
  WHERE rowid = old.rowid;
END;
"""

CREATE_VNDB_TRIGGER_DELETE = """
CREATE TRIGGER IF NOT EXISTS vndb_fts_delete AFTER DELETE ON cached_vndb_results
BEGIN
  DELETE FROM vndb_fts WHERE rowid = old.rowid;
END;
"""

CACHED_VNDB_RESULTS_INSERT_QUERY = """
INSERT INTO cached_vndb_results (vndb_id, title, cover_image_url, cover_image_nsfw) 
VALUES (?, ?, ?, ?)
ON CONFLICT(vndb_id) DO UPDATE SET 
    title=excluded.title,
    cover_image_url=excluded.cover_image_url,
    timestamp=CURRENT_TIMESTAMP;
"""

CACHED_VNDB_RESULTS_SEARCH_QUERY = """
SELECT vndb_id, title, cover_image_url 
FROM vndb_fts 
WHERE title LIKE '%' || ? || '%' 
LIMIT 10;
"""

CACHED_VNDB_RESULTS_BY_ID_QUERY = """
SELECT vndb_id, title, cover_image_url FROM cached_vndb_results 
WHERE vndb_id = ?;
"""

CACHED_VNDB_THUMBNAIL_QUERY = """
SELECT cover_image_url FROM cached_vndb_results
WHERE vndb_id = ? AND cover_image_nsfw = 0;
"""

CACHED_VNDB_TITLE_QUERY = """
SELECT title FROM cached_vndb_results
WHERE vndb_id = ?;
"""


async def query_vndb(interaction: discord.Interaction, current_input: str, bot: TMWBot):
    url = "https://api.vndb.org/kana/vn"

    if current_input.isdigit():
        if not "v" in current_input:
            current_input = f"v{current_input}"
        filters = ["id", "=", f"{current_input}"]
    else:
        filters = ["search", "=", current_input]

    payload = {
        "filters": filters,
        "fields": "title, image.url, image.sexual"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            if response.status == 200:
                data = await response.json()
                vns = data.get("results", [])

                choices = []
                for vn in vns:
                    vndb_id = vn.get("id")
                    title = vn.get("title")
                    cover_image_url = vn.get("image", {}).get("url")
                    cover_image_nsfw = vn.get("image", {}).get("sexual", False)
                    if cover_image_nsfw == 0:
                        cover_image_nsfw = False
                    else:
                        cover_image_nsfw = True

                    if not title or not vndb_id:
                        continue

                    choice_name = f"{title[:80]} (ID: {vndb_id}) (API)"
                    if title:
                        choices.append(discord.app_commands.Choice(name=choice_name, value=str(vndb_id)))

                    await bot.RUN(CACHED_VNDB_RESULTS_INSERT_QUERY, (vndb_id, title, cover_image_url, cover_image_nsfw))

                return choices[:10]
            elif response.status == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                print(f"API rate limit exceeded. Please wait {retry_after} seconds before retrying.")
                return []
            else:
                return []


async def vn_name_autocomplete(interaction: discord.Interaction, current_input: str):
    tmw_bot = interaction.client
    tmw_bot: TMWBot

    if current_input.startswith("v") and current_input[1:].isdigit():
        current_input = current_input[1:]
    if current_input.isdigit():
        cached_result = await tmw_bot.GET_ONE(CACHED_VNDB_RESULTS_BY_ID_QUERY, (f"v{current_input}",))
        if cached_result:
            vndb_id, title, _ = cached_result
            choice_name = f"{title[:80]} (ID: {vndb_id}) (Cached)"
            return [discord.app_commands.Choice(name=choice_name, value=str(vndb_id))]
        else:
            return await query_vndb(interaction, current_input, tmw_bot)
    else:
        cached_results = await tmw_bot.GET(CACHED_VNDB_RESULTS_SEARCH_QUERY, (current_input,))
        choices = []
        for cached_result in cached_results:
            vndb_id, title, _ = cached_result
            choice_name = f"{title[:80]} (ID: {vndb_id}) (Cached)"
            choices.append(discord.app_commands.Choice(name=choice_name, value=str(vndb_id)))

        if len(choices) < 1:
            vndb_choices = await query_vndb(interaction, current_input, tmw_bot)
            choices.extend(vndb_choices)

        return choices[:10]

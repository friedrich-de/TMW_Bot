[![Deploy Discord Bot](https://github.com/friedrich-de/TMW_Bot/actions/workflows/main.yml/badge.svg)](https://github.com/friedrich-de/TMW_Bot/actions/workflows/main.yml)

# TheMoeWay Discord Bot

Multi-purpose Discord bot developed for the TMW Discord server. The bot is designed to be modular and easy to extend with new features.


## Features/Commands

#### `auto_receive.py`

Lets admins set roles which automatically get assigned another role. For example, if a user has 'Eternal Idol' role they get the 'Custom Role' role, which gives more permissions like creating custom roles.

Commands:
* `/_add_auto_receive` `<role_to_have>` `<role_to_get>` - Set up automatic role assignment. Users with `role_to_have` will automatically receive `role_to_get`.
* `/_remove_auto_receive` `<role_to_have>` `<role_to_get>` - Remove an automatic role assignment rule.
* `/_ban_auto_receive` `<member>` `<role>` - Prevent a specific member from automatically receiving a role.
* `/_unban_auto_receive` `<member>` - Remove all auto-receive bans for a member.

All commands here require administrator permissions by default.

---

#### `bookmark.py`

Lets users bookmark messages by reacting with 🔖. Bookmarked messages are sent to the user's DMs and can be removed with ❌. Also tracks most bookmarked messages per server.

Commands:
* `/bookmarkboard` - Shows the top 10 most bookmarked messages in the server with links to jump to them.
* `/checkbookmarks` - Admin only by default. Cleans up the bookmark leaderboard by removing entries for deleted messages.

User Actions:
* React with 🔖 to bookmark a message
* React with ❌ on the bookmark DM to remove it

---

#### `custom_role.py`

Lets users create and manage their own custom roles with custom colors and icons (if the server has enough boosts). Custom roles are positioned below a reference role and are automatically removed if the user loses the required permissions.

Commands:
* `/make_custom_role` `<role_name>` `<color_code>` `<role_icon>` - Create a custom role. Role name limited to 14 characters, color must be hex code (e.g. #A47267), role icon is optional.
* `/delete_custom_role` - Remove your custom role.
* `/_create_custom_role_settings` `<reference_role>` - Admin only. Set up which roles can create custom roles and the reference role that custom roles are positioned under.

Note: Users need one of the allowed roles (set by admin) to create and keep custom roles.

---

#### `dumb_db.py`

Simple command that allows users to download a compressed copy of the bot's database file.

Commands:
* `/post_db` - Creates a gzipped copy of the database file and posts it to the channel. 

---

#### `event_roles.py`

Automatically creates and manages roles for Discord scheduled events. When an event is created, a corresponding role is made and assigned to participants. The role is automatically deleted when the event ends.

No user commands - fully automatic.

---

#### `gatekeeper.py`

Works with Kotoba bot quizzes to verify and award roles based on quiz performance.

Commands:
* `/reset_user_cooldown` `<user>` `<quiz_to_reset>` - Admin only. Reset a user's quiz cooldown. Optional quiz parameter to reset specific quiz.
* `/ranktable` - Display the distribution of quiz roles in the server, showing percentage of users with each role.
* `/rankusers` `<role>` - See all users with a specific role.
* `/list_role_commands` `<guild_id>` - List all quiz commands and their corresponding reward roles. Guild ID is optional.

Note: Requires configuration in `gatekeeper_settings.yml` to define quiz requirements and role structure.

---

#### `immersion_log.py`, `immersion_goals.py`, `immersion_stats.py`

Comprehensive immersion tracking system that allows users to log their Japanese learning activities, set goals, and view statistics.

Commands:
* `/log` `<media_type>` `<amount>` `<name>` `<comment>` `<backfill_date>` - Log immersion activity. Media type can be books, manga, anime, etc. Amount is in units or minutes.
* `/log_undo` `<log_entry>` - Remove a previous log entry.
* `/log_achievements` - Display all your immersion achievements.
* `/log_export` `<user>` - Export immersion logs as CSV file. User parameter is optional.
* `/logs` `<user>` - Output immersion logs as a nicely formatted text file. User parameter is optional.
* `/log_leaderboard` `<media_type>` `<month>` - Display monthly leaderboard. Can filter by media type and month (YYYY-MM or "ALL").

Goal Management:
* `/log_set_goal` `<media_type>` `<goal_type>` `<goal_value>` `<end_date_or_hours>` - Set a new immersion goal.
* `/log_remove_goal` `<goal_entry>` - Remove a specific goal.
* `/log_view_goals` `<member>` - View your goals or another user's goals.
* `/log_clear_goals` - Clear all expired goals.

Statistics:
* `/log_stats` `<user>` `<from_date>` `<to_date>` `<immersion_type>` - Display detailed immersion statistics with graphs. All parameters optional.

---

#### `immersion_bar_races.py`

Creates a racing bar chart of immersion logs for a specified time perid which visualizes
how much users logged over time and who is in the lead.

Commands:
* `/log_race` `<from_date>` `<to_date>` `<media_type>` `<race_type>` - Create a racing bar chart of immersion logs for a specified time period. 

---

#### `info.py`

Provides informational commands that display predefined knowledge and documentation to users. Commands and their content are configured through a YAML file.

Commands:
* `/info` `<topic>` - Display information about a specific topic.

Note: Requires configuration in `config/info_commands.yml` to define available topics and their content.

---

#### `kneels.py`

Tracks and displays statistics for "kneeling" reactions on messages. Users can react with 🧎, 🧎‍♂️, 🧎‍♀️ or custom emojis containing "ikneel" to "kneel".

Commands:
* `/kneelderboard` `<guild_id>` - Display the top 20 users with the most kneels received, along with your own kneel count. Guild ID is optional.

User Actions:
* React with kneel emojis (🧎, 🧎‍♂️, 🧎‍♀️ or custom :ikneel:) to count a kneel for the message author

---

#### `rank_saver.py`

Automatically saves and restores user roles when they leave and rejoin the server. Runs every 10 minutes to save current roles and restores applicable roles when a user rejoins.

Note: Requires configuration in `rank_saver_settings.yml` to define ignored roles and announcement channels. No user commands - fully automatic.

---

#### `selfmute.py`

Allows users to temporarily mute themselves for a specified duration. Users can choose from multiple mute roles and their existing roles are automatically restored when the mute expires.

Commands:
* `/selfmute` `<hours>` `<minutes>` - Mute yourself for a specified duration (max 7 days). Presents a selection menu of available mute roles.
* `/check_mute` - Check your current mute status or remove mute if the time has expired.
* `/unmute_user` `<member>` - Admin only. Remove a mute from a specified user.

Note: Requires configuration in `selfmute_settings.yml` to define mute roles and announcement channels.

---

#### `sticky_messages.py`

Allows moderators to make messages "sticky" in channels, meaning they will reappear after new messages, making the message always visible at the bottom of the channel.

Commands:
* `/sticky_last_message` - Make the last message in the channel sticky (ignoring bot commands). Requires manage messages permission by default.
* `/unsticky` - Remove the current sticky message from the channel. Requires manage messages permission by default.

---

#### `sync.py`

Manages command synchronization between the bot and Discord's command system. For authorized users only.

Commands (used with prefix):
* `sync_guild` - Sync commands to the current guild.
* `sync_global` - Sync commands globally across all guilds.
* `clear_global_commands` - Remove all global commands.
* `clear_guild_commands` - Remove all commands from the current guild.

Note: All commands require the user to be listed in the AUTHORIZED_USERS environment variable.

---

#### `thread_resolver.py`

Manages help threads in forum channels by tracking solved status and prompting for updates on inactive threads.

Commands:
* `/solved` - Mark a help thread as solved, which adds "[SOLVED]" to the title and archives it.

Note: Requires configuration in `thread_resolver_settings.yml` to define help forum channels.

---

#### `username_fetcher.py`

Internal utility module that maintains a database of user IDs and usernames. Used by other modules to efficiently fetch and cache usernames.

Note: No user commands - internal utility module only.

## How to contribute

1. Report bugs and suggest features in the issues tab.

2. Create a PR with your changes. The bot is made to run server indepedently, so you can run it anywhere you want to test your changes.

## How to run

1. Clone the repository
2. Create a virtual environment and install the requirements with `pip install -r requirements.txt`
3. Create a copy of `.env.example` and rename it to `.env` in the root directory and modify the following variables:

    `TOKEN=YOUR_DISCORD_BOT_TOKEN`

    `AUTHORIZED_USERS=960526101833191435,501009840437592074` Comma separated list of user IDs who can use bot management commands.

    `DEBUG_USER=960526101833191435` User ID of the user who gets sent debug messages.

    `COMMAND_PREFIX=%`

    `PATH_TO_DB=data/db.sqlite3`

    `TMDB_API_KEY=YOUR_TMDB_API_KEY`

4. Run the bot with `python main.py`, make sure your bot has [Privledged Message Intents](https://discord.com/developers/docs/events/gateway#privileged-intents)
5. Run `%sync_global` or `%sync_guild` to create application commands within your server

## How to run on Docker

1. Clone the repository
2. Build the docker image with `docker build -t discord-tmw-bot .`
3. Create a copy of `.env.example` and rename it `.env`, modify the variables to fit your environment
4. `docker compose up -d`

## Overwrite Settings

You can link to alternative setting files by setting the path in the corresponding environment variable. 

Each part of the bot can be configured separately. Please look into the cog files for the variable names.

## Acknowledgements
This bot is based on the following project (mostly used as a feature reference without or minimal direct code reuse):

- **DJT-Discord Bot** - Unlicensed (Corresponding code in this project is licensed under the GPL-3.0 license).
  - Original source: [https://github.com/friedrich-de/DJT-Discord-Bot](https://github.com/friedrich-de/DJT-Discord-Bot)
- **gatekeeper** - Licensed under the GPL-3.0 license.
  - Original source: [https://github.com/themoeway/gatekeeper](https://github.com/themoeway/gatekeeper)
- **selfmutebot** - Licensed under the MIT license.
  - Original source: [https://github.com/themoeway/selfmutebot](https://github.com/themoeway/selfmutebot)
- **tmw-utility-bot** - Licensed under the GPL-3.0 license.
  - Original source: [https://github.com/themoeway/tmw-utility-bot](https://github.com/themoeway/tmw-utility-bot)
- **Immersionbot** - Licensed under the MIT license.
  - Original source: [https://github.com/themoeway/Immersionbot](https://github.com/themoeway/Immersionbot)

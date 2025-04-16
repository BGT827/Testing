
import random
import time
import logging
import asyncio
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, Set, List, Tuple
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ChatType
from pyrogram.errors import FloodWait
import configparser
from pymongo import MongoClient
import redis
from flask import Flask, render_template

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Load configuration
config = configparser.ConfigParser()
config.read("config.ini")
api_id = config["Pyrogram"]["api_id"]
api_hash = config["Pyrogram"]["api_hash"]
bot_token = config["Pyrogram"]["bot_token"]
mongo_uri = config["Database"]["mongo_uri"]
redis_host = config["Redis"]["host"]
redis_port = config["Redis"]["port"]

# Initialize Pyrogram client
app = Client("WordSeekBot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)

# MongoDB setup
mongo_client = MongoClient(mongo_uri)
db = mongo_client["wordseek"]
games_coll = db["games"]
scores_coll = db["scores"]
stats_coll = db["stats"]
bot_stats_coll = db["bot_stats"]
users_coll = db["users"]

# Redis setup for rate limiting
try:
    redis_client = redis.Redis(host=redis_host, port=int(redis_port), decode_responses=True)
except Exception as e:
    logger.warning(f"Redis connection failed: {e}, falling back to in-memory rate limiting")
    redis_client = None

# Flask setup for analytics dashboard
flask_app = Flask(__name__)

# Word source (Datamuse API)
def fetch_words(length: int = 5, max_words: int = 1000) -> List[str]:
    try:
        response = requests.get(f"https://api.datamuse.com/words?ml=word&max={max_words}&sp={'?' * length}")
        words = [word["word"].lower() for word in response.json() if len(word["word"]) == length and word["word"].isalpha()]
        logger.info(f"Fetched {len(words)} words from Datamuse")
        return words
    except Exception as e:
        logger.error(f"Error fetching words: {e}")
        return ["apple", "brave", "cloud", "dream", "eagle", "flame", "grape", "house", "jolly", "knife"]

WORDS = fetch_words()

# Localization
LANGUAGES = {
    "en": {
        "welcome": "Welcome to WordSeek! Use /new to start a game.",
        "new_game": "New game started! Guess a {length}-letter word. Hints: 🟩=correct, 🟨=wrong spot, 🟥=not in word.",
        "invalid_guess": "Please guess a {length}-letter word!",
        "not_valid_word": "Not a valid word!",
        "game_in_progress": "A game is already in progress! Use /end to stop it.",
        "no_game": "No game is in progress!",
        "game_ended": "Game ended! The word was: {word}",
        "win": "🎉 {name} wins! The word was: {word}",
        "game_over": "Game over! No one guessed the word: {word}",
        "guesses_left": "{guess}: {hint}\nGuesses left: {left}",
        "admin_only": "Only admins can use this command!",
        "leaderboard": "{scope} Leaderboard ({period}):\n{data}",
        "myscore": "Your {scope} score ({period}): {score}",
        "no_scores": "You have no scores yet!",
        "stats": "Bot Stats\nGames Started: {games}\nGuesses Made: {guesses}",
        "settings_updated": "Settings updated: {settings}",
        "banned": "{name} has been banned from the game!",
        "kicked": "{name} has been kicked from the game!",
        "achievement": "🏆 Achievement Unlocked: {name}!",
        "reminder": "⏰ {left} guesses left! Keep guessing!",
        "help": """
**WordSeek Bot Help**
- /new [team/competitive]: Start a new game (modes: team, competitive).
- /end: End the current game (group admins only).
- /settings [max_guesses/length/timeout/theme] [value]: Adjust game settings (admins only).
- /ban [user_id]: Ban a user from the game (admins only).
- /kick [user_id]: Kick a user from the game (admins only).
- /leaderboard [global/group] [today/week/month/all]: View leaderboards.
- /myscore [group/global] [today/week/month/all]: View your score.
- /stats: View bot usage stats (bot admins only).
- /profile: View your game profile.
- /language [en/es]: Set language.
- /achievements: View your achievements.

**How to Play**
- Use /new to start a game.
- Guess a {length}-letter word.
- Hints: 🟩=correct, 🟨=wrong spot, 🟥=not in word.
- Game ends when the word is guessed or {max_guesses} guesses are used.
- First to guess correctly wins (or team with most points in team mode)!
        """
    },
    "es": {
        # Same structure as English, translated to Spanish (omitted for brevity, see previous code)
    }
}

# Game settings
DEFAULT_SETTINGS = {
    "max_guesses": 30,
    "word_length": 5,
    "timeout": 3600,  # 1 hour
    "theme": "default",  # Custom message styles
    "mode": "standard"  # standard, team, competitive
}

# Achievements
ACHIEVEMENTS = {
    "first_win": {"name": "First Win", "condition": lambda stats: stats.get("wins", 0) >= 1},
    "ten_wins": {"name": "Deca-Winner", "condition": lambda stats: stats.get("wins", 0) >= 10},
    "hundred_guesses": {"name": "Guess Master", "condition": lambda stats: stats.get("total_guesses", 0) >= 100}
}

# Rate limiting
RATE_LIMIT = 2  # seconds
user_last_guess: Dict[int, float] = {}

# Helper functions
def is_admin(chat_id: int, user_id: int, message: Message) -> bool:
    if message.chat.type == ChatType.PRIVATE:
        return True
    try:
        member = app.get_chat_member(chat_id, user_id)
        return member.status in ["administrator", "creator"]
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return False

def get_hint(guess: str, target: str) -> str:
    hint = []
    target_list = list(target)
    for i in range(len(guess)):
        if guess[i] == target[i]:
            hint.append("🟩")
            target_list[i] = None
        elif guess[i] in target_list:
            hint.append("🟨")
            target_list[target_list.index(guess[i])] = None
        else:
            hint.append("🟥")
    return "".join(hint)

def update_score(user_id: int, chat_id: int, mode: str, team: str = None):
    now = datetime.now()
    update = {
        "$inc": {
            f"scores.{chat_id}.all_time": 1,
            f"scores.{chat_id}.today": 1 if now.date() == datetime.now().date() else 0,
            f"scores.{chat_id}.week": 1 if now.isocalendar().week == datetime.now().isocalendar().week else 0,
            f"scores.{chat_id}.month": 1 if now.month == datetime.now().month else 0,
            f"global.all_time": 1,
            f"global.today": 1 if now.date() == datetime.now().date() else 0,
            f"global.week": 1 if now.isocalendar().week == datetime.now().isocalendar().week else 0,
            f"global.month": 1 if now.month == datetime.now().month else 0
        }
    }
    if mode == "team" and team:
        update["$inc"][f"teams.{team}.score"] = 1
    scores_coll.update_one({"user_id": user_id}, update, upsert=True)
    stats_coll.update_one({"user_id": user_id}, {"$inc": {"wins": 1}}, upsert=True)

def update_stats(user_id: int, guesses: int):
    stats_coll.update_one(
        {"user_id": user_id},
        {"$inc": {"games_played": 1, "total_guesses": guesses}},
        upsert=True
    )
    bot_stats_coll.update_one(
        {"_id": 1},
        {"$inc": {"guesses_made": guesses, "games_started": 0}, "$set": {"last_updated": time.time()}},
        upsert=True
    )

def check_achievements(user_id: int) -> List[str]:
    user_stats = stats_coll.find_one({"user_id": user_id}) or {}
    unlocked = []
    for ach_id, ach in ACHIEVEMENTS.items():
        if ach["condition"](user_stats) and ach_id not in user_stats.get("achievements", []):
            unlocked.append(ach["name"])
            stats_coll.update_one(
                {"user_id": user_id},
                {"$addToSet": {"achievements": ach_id}},
                upsert=True
            )
    return unlocked

def get_leaderboard(scope: str, chat_id: int, period: str, page: int = 1, per_page: int = 5) -> Tuple[str, InlineKeyboardMarkup]:
    pipeline = [
        {"$match": {f"{scope}.{period}": {"$gt": 0}}},
        {"$sort": {f"{scope}.{period}": -1}},
        {"$skip": (page - 1) * per_page},
        {"$limit": per_page}
    ]
    if scope == "scores":
        pipeline[0]["$match"]["scores.chat_id"] = chat_id
    
    leaderboard = []
    for user in scores_coll.aggregate(pipeline):
        try:
            user_info = app.get_users(user["user_id"])
            leaderboard.append((user_info.first_name, user[scope][period]))
        except Exception as e:
            logger.error(f"Error fetching user {user['user_id']}: {e}")
    
    total = scores_coll.count_documents({f"{scope}.{period}": {"$gt": 0}})
    result = f"{scope.capitalize()} Leaderboard ({period}):\n"
    for i, (name, score) in enumerate(leaderboard, 1 + (page - 1) * per_page):
        result += f"{i}. {name}: {score}\n"
    
    if not leaderboard:
        result = "No scores yet!"
    
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("Previous", callback_data=f"leaderboard_{scope}_{period}_{page-1}"))
    if total > page * per_page:
        buttons.append(InlineKeyboardButton("Next", callback_data=f"leaderboard_{scope}_{period}_{page+1}"))
    keyboard = InlineKeyboardMarkup([buttons]) if buttons else None
    
    return result, keyboard

def get_user_language(user_id: int) -> str:
    user = users_coll.find_one({"user_id": user_id})
    return user.get("language", "en") if user else "en"

# Flask dashboard
@flask_app.route("/dashboard")
def dashboard():
    stats = bot_stats_coll.find_one({"_id": 1}) or {}
    top_players = scores_coll.find().sort("global.all_time", -1).limit(5)
    return render_template(
        "dashboard.html",
        games_started=stats.get("games_started", 0),
        guesses_made=stats.get("guesses_made", 0),
        top_players=[{"name": app.get_users(p["user_id"]).first_name, "score": p["global"]["all_time"]} for p in top_players]
    )

# Command handlers
@app.on_message(filters.command("new"))
async def new_game(client: Client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    lang = get_user_language(user_id)
    args = message.command[1:]
    mode = args[0] if args else "standard"
    
    if mode not in ["standard", "team", "competitive"]:
        await message.reply("Invalid mode! Use: standard, team, competitive")
        return
    
    if games_coll.find_one({"chat_id": chat_id}):
        await message.reply(LANGUAGES[lang]["game_in_progress"])
        return
    
    settings = DEFAULT_SETTINGS.copy()
    settings["mode"] = mode
    word = random.choice(fetch_words(settings["word_length"]))
    game = {
        "chat_id": chat_id,
        "word": word,
        "guesses": [],
        "players": set(),
        "teams": {"team1": set(), "team2": set()} if mode == "team" else {},
        "start_time": time.time(),
        "settings": settings,
        "banned": set()
    }
    games_coll.insert_one(game)
    bot_stats_coll.update_one({"_id": 1}, {"$inc": {"games_started": 1}}, upsert=True)
    
    await message.reply(LANGUAGES[lang]["new_game"].format(length=settings["word_length"]))
    
    # Schedule timeout and reminders
    async def game_tasks():
        await asyncio.sleep(settings["timeout"] / 2)
        game = games_coll.find_one({"chat_id": chat_id})
        if game:
            guesses_left = settings["max_guesses"] - len(game["guesses"])
            await client.send_message(chat_id, LANGUAGES[lang]["reminder"].format(left=guesses_left))
        
        await asyncio.sleep(settings["timeout"] / 2)
        game = games_coll.find_one({"chat_id": chat_id})
        if game:
            games_coll.delete_one({"chat_id": chat_id})
            await client.send_message(chat_id, LANGUAGES[lang]["game_ended"].format(word=game["word"]))
    
    asyncio.create_task(game_tasks())

@app.on_message(filters.command("end") & filters.group)
async def end_game(client: Client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    lang = get_user_language(user_id)
    if not is_admin(chat_id, user_id, message):
        await message.reply(LANGUAGES[lang]["admin_only"])
        return
    
    game = games_coll.find_one({"chat_id": chat_id})
    if not game:
        await message.reply(LANGUAGES[lang]["no_game"])
        return
    
    games_coll.delete_one({"chat_id": chat_id})
    await message.reply(LANGUAGES[lang]["game_ended"].format(word=game["word"]))

@app.on_message(filters.command("settings") & filters.group)
async def update_settings(client: Client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    lang = get_user_language(user_id)
    if not is_admin(chat_id, user_id, message):
        await message.reply(LANGUAGES[lang]["admin_only"])
        return
    
    args = message.command[1:]
    if len(args) != 2:
        await message.reply("Usage: /settings [max_guesses/length/timeout/theme] [value]")
        return
    
    key, value = args
    try:
        if key in ["max_guesses", "length", "timeout"]:
            value = int(value)
            if value <= 0:
                raise ValueError
        elif key == "theme" and value not in ["default", "dark", "light"]:
            raise ValueError
    except ValueError:
        await message.reply("Invalid setting or value!")
        return
    
    game = games_coll.find_one({"chat_id": chat_id})
    settings = game["settings"] if game else DEFAULT_SETTINGS.copy()
    settings[key] = value
    games_coll.update_one({"chat_id": chat_id}, {"$set": {"settings": settings}}, upsert=True)
    await message.reply(LANGUAGES[lang]["settings_updated"].format(settings=settings))

@app.on_message(filters.command("ban") & filters.group)
async def ban_user(client: Client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    lang = get_user_language(user_id)
    if not is_admin(chat_id, user_id, message):
        await message.reply(LANGUAGES[lang]["admin_only"])
        return
    
    args = message.command[1:]
    if not args:
        await message.reply("Usage: /ban [user_id]")
        return
    
    try:
        target_id = int(args[0])
        user = await client.get_users(target_id)
        games_coll.update_one({"chat_id": chat_id}, {"$addToSet": {"banned": target_id}})
        await message.reply(LANGUAGES[lang]["banned"].format(name=user.first_name))
    except Exception as e:
        await message.reply("Invalid user ID!")
        logger.error(f"Error banning user: {e}")

@app.on_message(filters.command("kick") & filters.group)
async def kick_user(client: Client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    lang = get_user_language(user_id)
    if not is_admin(chat_id, user_id, message):
        await message.reply(LANGUAGES[lang]["admin_only"])
        return
    
    args = message.command[1:]
    if not args:
        await message.reply("Usage: /kick [user_id]")
        return
    
    try:
        target_id = int(args[0])
        user = await client.get_users(target_id)
        game = games_coll.find_one({"chat_id": chat_id})
        if game:
            game["players"].discard(target_id)
            games_coll.update_one({"chat_id": chat_id}, {"$set": {"players": game["players"]}})
        await message.reply(LANGUAGES[lang]["kicked"].format(name=user.first_name))
    except Exception as e:
        await message.reply("Invalid user ID!")
        logger.error(f"Error kicking user: {e}")

@app.on_message(filters.command("achievements"))
async def achievements_command(client: Client, message: Message):
    user_id = message.from_user.id
    lang = get_user_language(user_id)
    user_stats = stats_coll.find_one({"user_id": user_id}) or {}
    achievements = user_stats.get("achievements", [])
    text = "Your Achievements:\n"
    for ach_id in achievements:
        text += f"- {ACHIEVEMENTS[ach_id]['name']}\n"
    await message.reply(text or "No achievements yet!")

@app.on_message(filters.command("help"))
async def help_command(client: Client, message: Message):
    lang = get_user_language(message.from_user.id)
    settings = DEFAULT_SETTINGS
    await message.reply(LANGUAGES[lang]["help"].format(length=settings["word_length"], max_guesses=settings["max_guesses"]))

@app.on_message(filters.command("leaderboard"))
async def leaderboard_command(client: Client, message: Message):
    chat_id = message.chat.id
    lang = get_user_language(message.from_user.id)
    args = message.command[1:]
    scope = args[0] if len(args) > 0 else "scores"
    period = args[1] if len(args) > 1 else "all_time"
    
    if scope not in ["scores", "global"] or period not in ["today", "week", "month", "all_time"]:
        await message.reply("Usage: /leaderboard [global/group] [today/week/month/all]")
        return
    
    result, keyboard = get_leaderboard("scores" if scope == "group" else "global", chat_id, period)
    await message.reply(LANGUAGES[lang]["leaderboard"].format(scope=scope, period=period, data=result), reply_markup=keyboard)

@app.on_message(filters.command("myscore"))
async def myscore_command(client: Client, message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    lang = get_user_language(user_id)
    args = message.command[1:]
    scope = args[0] if len(args) > 0 else "group"
    period = args[1] if len(args) > 1 else "all_time"
    
    if scope not in ["group", "global"] or period not in ["today", "week", "month", "all_time"]:
        await message.reply("Usage: /myscore [global/group] [today/week/month/all]")
        return
    
    user = scores_coll.find_one({"user_id": user_id})
    score = 0
    if user:
        if scope == "group" and str(chat_id) in user.get("scores", {}):
            score = user["scores"][str(chat_id)].get(period, 0)
        elif scope == "global":
            score = user["global"].get(period, 0)
    
    await message.reply(LANGUAGES[lang]["myscore"].format(scope=scope, period=period, score=score))

@app.on_message(filters.command("stats") & filters.private)
async def stats_command(client: Client, message: Message):
    user_id = message.from_user.id
    lang = get_user_language(user_id)
    admin_ids = [123456789]  # Replace with actual admin IDs
    if user_id not in admin_ids:
        await message.reply(LANGUAGES[lang]["admin_only"])
        return
    
    stats = bot_stats_coll.find_one({"_id": 1}) or {}
    await message.reply(LANGUAGES[lang]["stats"].format(
        games=stats.get("games_started", 0),
        guesses=stats.get("guesses_made", 0)
    ))

@app.on_message(filters.command("profile"))
async def profile_command(client: Client, message: Message):
    user_id = message.from_user.id
    lang = get_user_language(user_id)
    user_stats = stats_coll.find_one({"user_id": user_id}) or {}
    games_played = user_stats.get("games_played", 0)
    wins = user_stats.get("wins", 0)
    total_guesses = user_stats.get("total_guesses", 0)
    avg_guesses = total_guesses / games_played if games_played > 0 else 0
    profile_text = f"""
    **Your Profile**
    Games Played: {games_played}
    Wins: {wins}
    Average Guesses: {avg_guesses:.2f}
    Achievements: {len(user_stats.get("achievements", []))}
    """
    await message.reply(profile_text)

@app.on_message(filters.command("language"))
async def language_command(client: Client, message: Message):
    user_id = message.from_user.id
    args = message.command[1:]
    if not args or args[0] not in LANGUAGES:
        await message.reply(f"Available languages: {', '.join(LANGUAGES.keys())}")
        return
    users_coll.update_one({"user_id": user_id}, {"$set": {"language": args[0]}}, upsert=True)
    await message.reply(f"Language set to {args[0]}")

@app.on_callback_query(filters.regex(r"leaderboard_(\w+)_(\w+)_(\d+)"))
async def leaderboard_pagination(client: Client, callback_query):
    scope, period, page = callback_query.data.split("_")[1:]
    page = int(page)
    chat_id = callback_query.message.chat.id
    lang = get_user_language(callback_query.from_user.id)
    result, keyboard = get_leaderboard(scope, chat_id, period, page)
    await callback_query.message.edit_text(
        LANGUAGES[lang]["leaderboard"].format(scope=scope, period=period, data=result),
        reply_markup=keyboard
    )

# Handle guesses
@app.on_message(filters.text & ~filters.command)
async def handle_guess(client: Client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    lang = get_user_language(user_id)
    guess = message.text.lower().strip()
    
    # Rate limiting
    now = time.time()
    if redis_client:
        key = f"guess:{user_id}"
        if redis_client.get(key):
            await message.reply("Slow down! Wait a moment before guessing again.")
            return
        redis_client.setex(key, RATE_LIMIT, 1)
    else:
        if user_id in user_last_guess and now - user_last_guess[user_id] < RATE_LIMIT:
            await message.reply("Slow down! Wait a moment before guessing again.")
            return
        user_last_guess[user_id] = now
    
    game = games_coll.find_one({"chat_id": chat_id})
    if not game:
        return
    
    if user_id in game.get("banned", set()):
        await message.reply("You are banned from this game!")
        return
    
    settings = game["settings"]
    if len(guess) != settings["word_length"] or not guess.isalpha():
        await message.reply(LANGUAGES[lang]["invalid_guess"].format(length=settings["word_length"]))
        return
    
    if guess not in WORDS:
        await message.reply(LANGUAGES[lang]["not_valid_word"])
        return
    
    team = None
    if settings["mode"] == "team":
        team = "team1" if len(game["guesses"]) % 2 == 0 else "team2"
        game["teams"][team].add(user_id)
    
    game["guesses"].append((user_id, guess))
    game["players"].add(user_id)
    games_coll.update_one(
        {"chat_id": chat_id},
        {"$set": {"guesses": game["guesses"], "players": game["players"], "teams": game["teams"]}}
    )
    update_stats(user_id, 1)
    
    hint = get_hint(guess, game["word"])
    if guess == game["word"]:
        update_score(user_id, chat_id, settings["mode"], team)
        unlocked = check_achievements(user_id)
        games_coll.delete_one({"chat_id": chat_id})
        user = await client.get_users(user_id)
        reply = LANGUAGES[lang]["win"].format(name=user.first_name, word=game["word"])
        if unlocked:
            reply += "\n" + LANGUAGES[lang]["achievement"].format(name=", ".join(unlocked))
        await message.reply(reply)
        return
    
    if len(game["guesses"]) >= settings["max_guesses"]:
        games_coll.delete_one({"chat_id": chat_id})
        await message.reply(LANGUAGES[lang]["game_over"].format(word=game["word"]))
        return
    
    await message.reply(LANGUAGES[lang]["guesses_left"].format(
        guess=guess,
        hint=hint,
        left=settings["max_guesses"] - len(game["guesses"])
    ))

# Start Flask dashboard in a separate thread
def run_flask():
    flask_app.run(host="0.0.0.0", port=5000)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    print("Bot is running...")
    try:
        app.run()
    except FloodWait as e:
        logger.warning(f"FloodWait: Sleeping for {e.x} seconds")
        time.sleep(e.x)
        app.run()

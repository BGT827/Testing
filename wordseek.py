import random
import time
import sqlite3
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Set, List, Tuple
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ChatType
import configparser
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Load configuration
config = configparser.ConfigParser()
config.read("config.ini")
api_id = config["Pyrogram"]["api_id"]
api_hash = config["Pyrogram"]["api_hash"]
bot_token = config["Pyrogram"]["bot_token"]

# Initialize Pyrogram client
app = Client("WordSeekBot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)

# Database setup
def init_db():
    conn = sqlite3.connect("wordseek.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS games (
            chat_id INTEGER PRIMARY KEY,
            word TEXT,
            guesses TEXT,
            players TEXT,
            start_time REAL,
            settings TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS scores (
            user_id INTEGER,
            chat_id INTEGER,
            today INTEGER DEFAULT 0,
            week INTEGER DEFAULT 0,
            month INTEGER DEFAULT 0,
            all_time INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, chat_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            user_id INTEGER PRIMARY KEY,
            games_played INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            total_guesses INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bot_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            games_started INTEGER DEFAULT 0,
            guesses_made INTEGER DEFAULT 0,
            last_updated REAL
        )
    """)
    c.execute("INSERT OR IGNORE INTO bot_stats (id, games_started, guesses_made, last_updated) VALUES (1, 0, 0, ?)", (time.time(),))
    conn.commit()
    conn.close()

init_db()

# Load word list
WORDS = []
def load_words():
    try:
        with open("words.txt", "r") as f:
            WORDS.extend([line.strip().lower() for line in f if len(line.strip()) == 5 and line.strip().isalpha()])
        logger.info(f"Loaded {len(WORDS)} words")
    except FileNotFoundError:
        WORDS.extend(["apple", "brave", "cloud", "dream", "eagle", "flame", "grape", "house", "jolly", "knife"])
        logger.warning("words.txt not found, using default word list")

load_words()

# Localization
LANGUAGES = {
    "en": {
        "welcome": "Welcome to WordSeek! Use /new to start a game.",
        "new_game": "New game started! Guess a {length}-letter word. Hints: 🟩=correct spot, 🟨=wrong spot, 🟥=not in word.",
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
        "help": """
**WordSeek Bot Help**
- /new: Start a new game.
- /end: End the current game (group admins only).
- /settings [max_guesses/length/timeout] [value]: Adjust game settings (admins only).
- /leaderboard [global/group] [today/week/month/all]: View leaderboards.
- /myscore [group/global] [today/week/month/all]: View your score.
- /stats: View bot usage stats (bot admins only).
- /profile: View your game profile.
- /language [en/es]: Set language.

**How to Play**
- Use /new to start a game.
- Guess a {length}-letter word.
- Hints: 🟩=correct spot, 🟨=wrong spot, 🟥=not in word.
- Game ends when the word is guessed or {max_guesses} guesses are used.
- First to guess correctly wins!
        """
    },
    "es": {
        "welcome": "¡Bienvenido a WordSeek! Usa /new para comenzar un juego.",
        "new_game": "¡Nuevo juego iniciado! Adivina una palabra de {length} letras. Pistas: 🟩=posición correcta, 🟨=posición incorrecta, 🟥=no está en la palabra.",
        "invalid_guess": "¡Por favor, adivina una palabra de {length} letras!",
        "not_valid_word": "¡No es una palabra válida!",
        "game_in_progress": "¡Ya hay un juego en curso! Usa /end para detenerlo.",
        "no_game": "¡No hay ningún juego en curso!",
        "game_ended": "¡Juego terminado! La palabra era: {word}",
        "win": "🎉 ¡{name} gana! La palabra era: {word}",
        "game_over": "¡Juego terminado! Nadie adivinó la palabra: {word}",
        "guesses_left": "{guess}: {hint}\nIntentos restantes: {left}",
        "admin_only": "¡Solo los administradores pueden usar este comando!",
        "leaderboard": "Tabla de clasificación {scope} ({period}):\n{data}",
        "myscore": "Tu puntuación {scope} ({period}): {score}",
        "no_scores": "¡Aún no tienes puntuaciones!",
        "stats": "Estadísticas del bot\nJuegos iniciados: {games}\nIntentos realizados: {guesses}",
        "settings_updated": "Configuración actualizada: {settings}",
        "help": """
**Ayuda de WordSeek Bot**
- /new: Iniciar un nuevo juego.
- /end: Terminar el juego actual (solo administradores de grupo).
- /settings [max_guesses/length/timeout] [value]: Ajustar configuraciones del juego (solo administradores).
- /leaderboard [global/group] [today/week/month/all]: Ver tablas de clasificación.
- /myscore [group/global] [today/week/month/all]: Ver tu puntuación.
- /stats: Ver estadísticas de uso del bot (solo administradores del bot).
- /profile: Ver tu perfil de juego.
- /language [en/es]: Cambiar idioma.

**Cómo jugar**
- Usa /new para iniciar un juego.
- Adivina una palabra de {length} letras.
- Pistas: 🟩=posición correcta, 🟨=posición incorrecta, 🟥=no está en la palabra.
- El juego termina cuando se adivina la palabra o se usan {max_guesses} intentos.
- ¡El primero en adivinar correctamente gana!
        """
    }
}

# Game settings
DEFAULT_SETTINGS = {
    "max_guesses": 30,
    "word_length": 5,
    "timeout": 3600  # 1 hour in seconds
}

# Rate limiting
RATE_LIMIT = 2  # seconds between guesses
user_last_guess: Dict[int, float] = {}

# Helper functions
def get_db():
    return sqlite3.connect("wordseek.db")

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

def update_score(user_id: int, chat_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO scores (user_id, chat_id) VALUES (?, ?)", (user_id, chat_id))
    c.execute("""
        UPDATE scores SET all_time = all_time + 1,
                         today = today + CASE WHEN date('now') = date('now') THEN 1 ELSE 0 END,
                         week = week + CASE WHEN strftime('%W', 'now') = strftime('%W', 'now') THEN 1 ELSE 0 END,
                         month = month + CASE WHEN strftime('%m', 'now') = strftime('%m', 'now') THEN 1 ELSE 0 END
        WHERE user_id = ? AND chat_id = ?
    """, (user_id, chat_id))
    c.execute("UPDATE stats SET wins = wins + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def update_stats(user_id: int, guesses: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO stats (user_id) VALUES (?)", (user_id,))
    c.execute("UPDATE stats SET games_played = games_played + 1, total_guesses = total_guesses + ? WHERE user_id = ?", (guesses, user_id))
    c.execute("UPDATE bot_stats SET guesses_made = guesses_made + ?, last_updated = ? WHERE id = 1", (guesses, time.time()))
    conn.commit()
    conn.close()

def get_leaderboard(scope: str, chat_id: int, period: str, page: int = 1, per_page: int = 5) -> Tuple[str, InlineKeyboardMarkup]:
    conn = get_db()
    c = conn.cursor()
    query = f"SELECT user_id, {period} FROM scores WHERE {period} > 0"
    params = []
    if scope == "group":
        query += " AND chat_id = ?"
        params.append(chat_id)
    query += f" ORDER BY {period} DESC LIMIT ? OFFSET ?"
    params.extend([per_page, (page - 1) * per_page])
    
    c.execute(query, params)
    rows = c.fetchall()
    leaderboard = []
    for user_id, score in rows:
        try:
            user = app.get_users(user_id)
            leaderboard.append((user.first_name, score))
        except Exception as e:
            logger.error(f"Error fetching user {user_id}: {e}")
    
    total = len(leaderboard)
    result = f"{scope.capitalize()} Leaderboard ({period}):\n"
    for i, (name, score) in enumerate(leaderboard, 1 + (page - 1) * per_page):
        result += f"{i}. {name}: {score}\n"
    
    if not leaderboard:
        result = "No scores yet!"
    
    # Pagination buttons
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("Previous", callback_data=f"leaderboard_{scope}_{period}_{page-1}"))
    if total == per_page:
        buttons.append(InlineKeyboardButton("Next", callback_data=f"leaderboard_{scope}_{period}_{page+1}"))
    keyboard = InlineKeyboardMarkup([buttons]) if buttons else None
    
    conn.close()
    return result, keyboard

def get_user_language(user_id: int) -> str:
    # Placeholder: Store user language in database or config
    return "en"  # Default to English

# Command handlers
@app.on_message(filters.command("new"))
async def new_game(client: Client, message: Message):
    chat_id = message.chat.id
    lang = get_user_language(message.from_user.id)
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT word FROM games WHERE chat_id = ?", (chat_id,))
    if c.fetchone():
        await message.reply(LANGUAGES[lang]["game_in_progress"])
        conn.close()
        return
    
    settings = DEFAULT_SETTINGS.copy()
    word = random.choice(WORDS)
    c.execute("INSERT INTO games (chat_id, word, guesses, players, start_time, settings) VALUES (?, ?, ?, ?, ?, ?)",
              (chat_id, word, "", "", time.time(), str(settings)))
    c.execute("UPDATE bot_stats SET games_started = games_started + 1, last_updated = ? WHERE id = 1", (time.time(),))
    conn.commit()
    conn.close()
    
    await message.reply(LANGUAGES[lang]["new_game"].format(length=settings["word_length"]))
    
    # Schedule timeout
    async def timeout_game():
        await asyncio.sleep(settings["timeout"])
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT word FROM games WHERE chat_id = ?", (chat_id,))
        result = c.fetchone()
        if result:
            word = result[0]
            c.execute("DELETE FROM games WHERE chat_id = ?", (chat_id,))
            conn.commit()
            await client.send_message(chat_id, LANGUAGES[lang]["game_ended"].format(word=word))
        conn.close()
    
    asyncio.create_task(timeout_game())

@app.on_message(filters.command("end") & filters.group)
async def end_game(client: Client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    lang = get_user_language(user_id)
    if not is_admin(chat_id, user_id, message):
        await message.reply(LANGUAGES[lang]["admin_only"])
        return
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT word FROM games WHERE chat_id = ?", (chat_id,))
    result = c.fetchone()
    if not result:
        await message.reply(LANGUAGES[lang]["no_game"])
        conn.close()
        return
    
    word = result[0]
    c.execute("DELETE FROM games WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()
    await message.reply(LANGUAGES[lang]["game_ended"].format(word=word))

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
        await message.reply("Usage: /settings [max_guesses/length/timeout] [value]")
        return
    
    key, value = args
    try:
        value = int(value)
        if key not in ["max_guesses", "length", "timeout"] or value <= 0:
            raise ValueError
    except ValueError:
        await message.reply("Invalid setting or value!")
        return
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT settings FROM games WHERE chat_id = ?", (chat_id,))
    result = c.fetchone()
    settings = eval(result[0]) if result else DEFAULT_SETTINGS.copy()
    settings[key] = value
    c.execute("UPDATE games SET settings = ? WHERE chat_id = ?", (str(settings), chat_id))
    conn.commit()
    conn.close()
    await message.reply(LANGUAGES[lang]["settings_updated"].format(settings=settings))

@app.on_message(filters.command("help"))
async def help_command(client: Client, message: Message):
    lang = get_user_language(message.from_user.id)
    settings = DEFAULT_SETTINGS  # Fetch from DB if needed
    await message.reply(LANGUAGES[lang]["help"].format(length=settings["word_length"], max_guesses=settings["max_guesses"]))

@app.on_message(filters.command("leaderboard"))
async def leaderboard_command(client: Client, message: Message):
    chat_id = message.chat.id
    lang = get_user_language(message.from_user.id)
    args = message.command[1:]
    scope = args[0] if len(args) > 0 else "group"
    period = args[1] if len(args) > 1 else "all"
    
    if scope not in ["group", "global"] or period not in ["today", "week", "month", "all_time"]:
        await message.reply("Usage: /leaderboard [global/group] [today/week/month/all]")
        return
    
    result, keyboard = get_leaderboard(scope, chat_id, period)
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
    
    conn = get_db()
    c = conn.cursor()
    if scope == "group":
        c.execute(f"SELECT {period} FROM scores WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
    else:
        c.execute(f"SELECT SUM({period}) FROM scores WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    score = result[0] if result else 0
    conn.close()
    
    await message.reply(LANGUAGES[lang]["myscore"].format(scope=scope, period=period, score=score))

@app.on_message(filters.command("stats") & filters.private)
async def stats_command(client: Client, message: Message):
    user_id = message.from_user.id
    lang = get_user_language(user_id)
    admin_ids = [123456789]  # Replace with actual admin IDs
    if user_id not in admin_ids:
        await message.reply(LANGUAGES[lang]["admin_only"])
        return
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT games_started, guesses_made FROM bot_stats WHERE id = 1")
    games, guesses = c.fetchone()
    conn.close()
    await message.reply(LANGUAGES[lang]["stats"].format(games=games, guesses=guesses))

@app.on_message(filters.command("profile"))
async def profile_command(client: Client, message: Message):
    user_id = message.from_user.id
    lang = get_user_language(user_id)
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT games_played, wins, total_guesses FROM stats WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    if not result:
        await message.reply("You haven't played any games yet!")
        conn.close()
        return
    
    games_played, wins, total_guesses = result
    avg_guesses = total_guesses / games_played if games_played > 0 else 0
    profile_text = f"""
    **Your Profile**
    Games Played: {games_played}
    Wins: {wins}
    Average Guesses: {avg_guesses:.2f}
    """
    conn.close()
    await message.reply(profile_text)

@app.on_message(filters.command("language"))
async def language_command(client: Client, message: Message):
    args = message.command[1:]
    if not args or args[0] not in LANGUAGES:
        await message.reply(f"Available languages: {', '.join(LANGUAGES.keys())}")
        return
    # Update user language in database (placeholder)
    await message.reply(f"Language set to {args[0]}")

# Callback query for leaderboard pagination
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
    if user_id in user_last_guess and now - user_last_guess[user_id] < RATE_LIMIT:
        await message.reply("Slow down! Wait a moment before guessing again.")
        return
    user_last_guess[user_id] = now
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT word, guesses, players, settings FROM games WHERE chat_id = ?", (chat_id,))
    result = c.fetchone()
    if not result:
        conn.close()
        return
    
    word, guesses, players, settings = result
    settings = eval(settings)
    guesses = eval(guesses) if guesses else []
    players = eval(players) if players else set()
    
    if len(guess) != settings["word_length"] or not guess.isalpha():
        await message.reply(LANGUAGES[lang]["invalid_guess"].format(length=settings["word_length"]))
        conn.close()
        return
    
    if guess not in WORDS:
        await message.reply(LANGUAGES[lang]["not_valid_word"])
        conn.close()
        return
    
    guesses.append((user_id, guess))
    players.add(user_id)
    c.execute("UPDATE games SET guesses = ?, players = ? WHERE chat_id = ?", (str(guesses), str(players), chat_id))
    update_stats(user_id, 1)
    conn.commit()
    
    hint = get_hint(guess, word)
    if guess == word:
        update_score(user_id, chat_id)
        c.execute("DELETE FROM games WHERE chat_id = ?", (chat_id,))
        conn.commit()
        conn.close()
        user = await client.get_users(user_id)
        await message.reply(LANGUAGES[lang]["win"].format(name=user.first_name, word=word))
        return
    
    if len(guesses) >= settings["max_guesses"]:
        c.execute("DELETE FROM games WHERE chat_id = ?", (chat_id,))
        conn.commit()
        conn.close()
        await message.reply(LANGUAGES[lang]["game_over"].format(word=word))
        return
    
    conn.close()
    await message.reply(LANGUAGES[lang]["guesses_left"].format(guess=guess, hint=hint, left=settings["max_guesses"] - len(guesses)))

# Start the bot
if __name__ == "__main__":
    print("Bot is running...")
    app.run()

from discord.ext import commands
import discord
import os
import io
from dotenv import load_dotenv
from json_data_helpers import card_collection, load_collections, save_collections, ensure_card_ids, load_user_emojis, save_user_emojis
import random
from image_helpers import apply_frame, merge_cards_horizontally, resize_image
import asyncio
import time
from collections import defaultdict
from utils.paginator import CollectionView
import asyncpg
from datetime import datetime, timezone
from data_helpers import add_entry, read_entries
import json

SUGGESTIONS_FILE = "suggestions.json"
BUGFIXES_FILE = "bugfixes.json"

current_time = datetime.now(timezone.utc)


FRAME_PATH = "./images/frame.png"

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = 1397431382741090314

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all(), case_insensitive=True)

# Load cards database at startup
cards = card_collection()

user_collections = defaultdict(list, ensure_card_ids(load_collections()))

PRIORITY_WINDOW = 10  # Seconds only the dropper can claim


RARITY_TIERS = {
    "Common": {"color": 0xAAAAAA, "chance": 54},
    "Rare": {"color": 0x3498DB, "chance": 25},
    "Epic": {"color": 0x9B59B6, "chance": 15},
    "Legendary": {"color": 0xFFD700, "chance": 5},
    "Mythic": {"color": 0xFFD700, "chance": 1}
}

user_cooldowns = {}
drop_cooldowns = {}

COOLDOWN_DURATION = 1800 # 30mins
DROP_COOLDOWN_DURATION = 1800 # 2 hours = 7200 1 hour = 3600


db_pool = None

async def get_db_pool():
    return await asyncpg.create_pool(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME")
    )

def assign_rarity():
    roll = random.randint(1, 100)
    total = 0
    for rarity, data in RARITY_TIERS.items():
        total += data["chance"]
        if roll <= total:
            return rarity
    return "Common"  # Fallback

def get_card_by_emoji(emoji, dropped_cards):
    for card in dropped_cards:
        if card['reaction'] == emoji:
            return card
    return None

def generate_card_uid(name, short_id, edition):
    name_code = ''.join(filter(str.isalpha, name.upper()))[:4]
    return f"{name_code}{short_id:02}{edition:02}"

# @bot.event
# async def on_ready():
#     # start up message
#     print(f"Yo! Mingyu bot ({bot.user}) has logged in.")
#     channel = bot.get_channel(CHANNEL_ID)
#     # send message to channel
#     await channel.send(f"Yo, Mingyu is here! Let's party!!")

@bot.event
async def on_ready():
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(
            host=os.getenv("DB_HOST"),
            port = int(os.getenv("DB_PORT", 5432)),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME")
        )
        await bot.change_presence(activity=discord.Game(name="!drop to play"))
    print(f"Mingyu Bot ready and connected to DB!")

# drop command !drop
@bot.command()
async def drop(ctx):
    user_id = ctx.author.id
    channel = bot.get_channel(CHANNEL_ID)
    now = time.time()

    # Send a message if !drop is used in the wrong channel
    if ctx.channel.id != CHANNEL_ID:
        await ctx.send(f"Hey! The photocards are not in this area.")
        return
    
    # Check dropper cooldown
    if user_id in drop_cooldowns:
        elapsed = now - drop_cooldowns[user_id]
        if elapsed < DROP_COOLDOWN_DURATION:
            remaining = int(DROP_COOLDOWN_DURATION - elapsed)
            hours, remainder = divmod(remaining, 3600)
            minutes, seconds = divmod(remainder, 60)
            await ctx.send(f"⏳ {ctx.author.mention} you can drop again in **{hours}h {minutes}m {seconds}s** ⏳")
            return
    
    # Announce user is dropping cards
    drop_message = await channel.send(f"🚨 {ctx.author.mention} came to drop some photocards! 🚨")
    print("Cards available for dropping: ", cards)

    dropper_id = ctx.author.id
    drop_time = time.time()

    dropped_cards = []
    reactions = ["🫰", "🫶", "🥰"]
    # Randomly select 3 cards from database
    selected_cards = random.sample(cards, 3)

    for i, card in enumerate(selected_cards):
        rarity = assign_rarity()
        card_copy = card.copy()
        card_copy['rarity'] = rarity
        card_copy['color'] = RARITY_TIERS[rarity]['color']
        card_copy['reaction'] = reactions[i]
        dropped_cards.append(card_copy)

    framed_cards = []
    for card in dropped_cards:
        card_path = card['image']
        framed = apply_frame(card_path, FRAME_PATH)
        framed_cards.append(framed)
    print(f"Number of framed cards: {len(framed_cards)}")
    final_image = merge_cards_horizontally(framed_cards)
    resized_image = resize_image(final_image, max_width=800)

    buffer = io.BytesIO()
    resized_image.save(buffer, format="PNG")
    buffer.seek(0)

    file = discord.File(fp=buffer, filename="drop.png")

    # Embed when user drops cards
    embed = discord.Embed(
        title="✨ Card Drop! ✨",
        description=f"{ctx.author.mention} just dropped some cards!",
        color=discord.Color.blue()
    )
    # for card in dropped_cards:
    #     embed.add_field(name=f"{card['name']}")

    # Message before sending the image
    # await ctx.send("✨ React with one of the emojis to claim a card below!")

    embed.set_image(url="attachment://drop.png")
    message = await ctx.send(file=file, embed=embed)

    # Add reactions to drop message
    for card in dropped_cards:
        await message.add_reaction(card['reaction'])

    drop_cooldowns[user_id] = now

    claimed = {}
    already_claimed_users = set()
    claim_challengers = {emoji: [] for emoji in reactions}

    def check(reaction, user):
        return (
            reaction.message.id == message.id and
            user != bot.user and
            str(reaction.emoji) in reactions
        )
    
    while len(claimed) < 3:
        try:
            reaction, user = await bot.wait_for("reaction_add", timeout=120.0, check=check)
            now = time.time()
            emoji = str(reaction.emoji)

            # cooldown check
            now = time.time()

            if user.id != dropper_id and (now - drop_time) < PRIORITY_WINDOW:
                continue

            if user.id not in claim_challengers[emoji]:
                claim_challengers[emoji].append(user.id)

            if user.id in user_cooldowns:
                elapsed = now - user_cooldowns[user.id]
                if elapsed < COOLDOWN_DURATION:
                    remaining = int(COOLDOWN_DURATION - elapsed)
                    hours, remainder = divmod(remaining, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    await ctx.send(f"⏳ {user.mention} you're still on cooldown!! Remaining: **{hours}h {minutes}m {seconds}s ⏳**")
                    continue

            # # already claimed
            if user.id in already_claimed_users:
                await ctx.send(f"{user.mention}, you've already claimed a card!")
                continue

            if emoji in claimed:
                await ctx.send(f"⚠️ Sorry {user.mention} that card is out of stock.")
                continue

            og_card = get_card_by_emoji(emoji, dropped_cards)
            card = og_card.copy()
            card.pop("reaction", None)

            async with db_pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT MAX(short_id::int) AS max_short_id
                    FROM user_cards
                    WHERE user_id = $1
                """, user.id)
                short_id = (row['max_short_id'] or 0) + 1

                edition_row = await conn.fetchrow("""
                    SELECT COUNT(*) AS count
                    FROM user_cards
                    WHERE user_id = $1 AND member_name = $2 AND rarity = $3 AND concept = $4
                """, user.id, card['name'], card['rarity'], card.get('concept', 'Base'))
                edition = edition_row['count'] + 1

                card["short_id"] = short_id
                card["edition"] = edition
                card["card_uid"] = generate_card_uid(card["name"], short_id, edition)

            # # DATABASE -- AFTER UID IS SET
            # async with db_pool.acquire() as conn:
            #     rows = await conn.fetch("""
            #         SELECT COUNT(*) FROM user_cards
            #         WHERE user_id = $1 AND card_uid = $2
            #     """, user.id, card['uid'])
            #     count = rows[0]['count'] if rows else 0
            #     edition = count + 1


            # Claimed card into user_cards table
                await conn.execute("""
                    INSERT INTO user_cards(user_id, card_uid, short_id, date_obtained, rarity, edition, member_name, group_name, concept)
                    VALUES($1, $2, $3, CURRENT_TIMESTAMP, $4, $5, $6, $7, $8)
                """, int(user.id), card['card_uid'], card['short_id'],
                    card['rarity'], edition, card['name'], card['group'], card.get('concept', 'Base'))


            challengers = [cid for cid in claim_challengers[emoji] if cid != user.id]
            if challengers:
                fought_off_mentions = ", ".join(f"<@{cid}>" for cid in challengers)
                await ctx.send(f"{user.mention} fought off {fought_off_mentions} and gained a {card['rarity']}-Tier **{card['name']}** photocard! 🤩")
            else:
                await ctx.send(f"{user.mention} gained a {card['rarity']}-Tier **{card['name']}** photocard! 🤩")

            claimed[emoji] = user.id
            already_claimed_users.add(user.id)
            user_cooldowns[user.id] = now

        except asyncio.TimeoutError:
            break

@bot.command()
async def collection(ctx, sort_key: str = "date_obtained", member: discord.Member = None):
    target = member or ctx.author
    user_id = target.id

    # get user's tag emoji
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT emoji FROM users WHERE user_id = $1", user_id)
        emoji = row["emoji"] if row and row["emoji"] else "📸"

    # Validate sort key
    valid_sorts = {
        "date_obtained": "date_obtained DESC",
        "rarity": "rarity ASC",
        "member_name": "member_name ASC",
        "group_name": "group_name ASC"
    }

    order_by = valid_sorts.get(sort_key.lower(), "date_obtained DESC")

    # Get cards
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT * FROM user_cards
            WHERE user_id = $1
            ORDER BY {order_by};
        """, int(user_id))

    if not rows:
        await ctx.send(f"{target.display_name} doesn't have any photocards yet. 😢")
        return

    # PAGINATION
    page_size = 10
    pages = [rows[i:i + page_size] for i in range(0, len(rows), page_size)]
    view = CollectionView(ctx, pages, emoji, target, sort_key.lower())
    embed = view.generate_embed()
    view.message = await ctx.send(embed=embed, view=view)


pending_trades = {}

# COMMAND TRADE !trade
@bot.command()
async def trade(ctx, partner: discord.Member, card_uid: str):

    sender_id = ctx.author.id
    recipient_id = partner.id

    async with db_pool.acquire() as conn:
        card = await conn.fetchrow("""
            SELECT * FROM user_cards
            WHERE user_id = $1 AND card_uid = $2
        """, sender_id, card_uid)


        if not card:
            await ctx.send("❌ You don't own a card with that UID.")
            return
        
        # SAVE PENDING TRADE to memory
        pending_trades[sender_id] = {
            "recipient_id": recipient_id,
            "card_uid": card_uid,
            "member_name": card['member_name'],
            "rarity": card['rarity'],
            "message_id": None
        }

        # confirmation message
        message = await ctx.send(f"{partner.mention}! {ctx.author.display_name} wants to give you their [**{card['rarity']}**] **{card['member_name']}** photocard. Accept?")

        await message.add_reaction("🤝")
        await message.add_reaction("❌")

        pending_trades[sender_id]["message_id"] = message.id

        # timeout auto-cancel (5 minutes)
        async def auto_cancel():
            await asyncio.sleep(300)
            if sender_id in pending_trades and pending_trades[sender_id]["message_id"] == message.id:
                del pending_trades[sender_id]
                try:
                    await message.channel.send("⌛ Trade request timed out.")
                except discord.HTTPException:
                    pass
        
        asyncio.create_task(auto_cancel())

@bot.event
async def on_reaction_add(reaction, user):
    message = reaction.message
    emoji = str(reaction.emoji)

    for sender_id, trade in list(pending_trades.items()):
        if trade["message_id"] != message.id:
            continue

        if user.id != trade["recipient_id"]:
            continue # only allows recipient to respond

        card_uid = trade["card_uid"]

        if emoji == "🤝":
            async with db_pool.acquire() as conn:

                    # transfer ownership
                    await conn.execute("""
                        UPDATE user_cards 
                        SET user_id = $1, date_obtained = $2 
                        WHERE card_uid = $3
                    """, user.id, datetime.now(timezone.utc), card_uid)

                    await message.channel.send(f"✅ Trade successful! [**{trade['rarity']}**] **{trade['member_name']}** photocard is now added to your collection!")
                    del pending_trades[sender_id]
                    
        elif emoji == "❌":
            await message.channel.send(f"❌ Trade was declined.")
            del pending_trades[sender_id]

# TAG COMMAND !tag                
@bot.command()
async def tag(ctx, emoji):
    user_id = ctx.author.id

    if not emoji.startswith("<") and len(emoji) > 2:
        await ctx.send("❌ Please use a valid emoji or Discord emote!")
        return

    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, emoji)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET emoji = EXCLUDED.emoji
        """, user_id, emoji)

    await ctx.send(f"✅ Your collection is now tagged with {emoji}!")

    

@bot.command()
async def mycards(ctx, *, card_name: str):
    user_id = ctx.author.id
    card_name = card_name.upper()

    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT card_uid, group_name, member_name, rarity, concept
            FROM user_cards
            WHERE user_id = $1 AND member_name ILIKE $2
            ORDER BY date_obtained DESC        
        """, user_id, f"%{card_name}%")

    if not rows:
        await ctx.send(f'❌ No cards matching "{card_name}" found in your collection.')
        return

    embed = discord.Embed(
        title=f'📸 Your Cards Matching "{card_name}":',
        description=f"{len(rows)} card(s) found",
        color=discord.Color.blue()
    )

    emoji_map = {
        "Common": "🟩",
        "Rare": "🟦",
        "Epic": "🟪",
        "Legendary": "🟥",
        "Mythic": "🌟"
    }

    for i, row in enumerate(rows, 1):
        uid = row["card_uid"]
        group = row["group_name"] or "Unknown"
        name = row["member_name"] or "Unknown"
        rarity = row["rarity"] or "Unknown"
        concept = row["concept"] or "Idol"
        emoji = emoji_map.get(rarity, "🎴")

        embed.add_field(
            name=f"{i}. {emoji} {group} • {name} • {concept} • ({rarity}) • #{uid}",
            value="",
            inline=False
        )

    embed.set_footer(text='Use "!trade @user <uid>" to trade a specific card.')

    await ctx.send(embed=embed)

@bot.command()
async def cd(ctx):
    user_id = ctx.author.id
    now = time.time()

    # get timestamp
    last_drop = drop_cooldowns.get(user_id)
    last_claim = user_cooldowns.get(user_id)

    drop_remaining = max(0, int(DROP_COOLDOWN_DURATION - (now - last_drop))) if last_drop else 0
    claim_remaining = max(0, int(COOLDOWN_DURATION - (now - last_claim))) if last_claim else 0

    def format_time(seconds):
        minutes, sec = divmod(seconds, 60)
        return f"{minutes}m {sec}s" if seconds > 0 else "Ready ✅"
    
    embed = discord.Embed(title="⏳ Your Cooldowns", color=discord.Color.orange())
    embed.add_field(name="Drop Cooldown", value=format_time(drop_remaining), inline=False)
    embed.add_field(name="Claim Cooldown", value=format_time(claim_remaining), inline=False)

    await ctx.send(embed=embed)

# SORT !sort
@bot.command()
async def sort(ctx, criterion: str = 'group'):
    criterion = criterion.lower()
    valid_criteria = ['group', 'member', 'rarity', 'edition']
    if criterion not in valid_criteria:
        await ctx.send(f"❌ Invalid sort option. Choose one of: {', '.join(valid_criteria)}")
        return

    user_id = int(ctx.author.id)

    # 🔍 Fetch cards from PostgreSQL
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT uc.card_uid, uc.user_id, c.group_name, c.member_name, c.rarity, c.edition
            FROM user_cards uc
            JOIN cards c ON uc.card_uid = c.card_uid
            WHERE uc.user_id = $1
        """, user_id)

    if not rows:
        await ctx.send("You don't have any cards yet.")
        return

    # 🧹 Convert rows into list of dicts
    cards = [dict(row) for row in rows]

    # 🔃 Sorting logic
    if criterion == 'group':
        cards.sort(key=lambda c: c['group_name'].lower())
    elif criterion == 'member':
        cards.sort(key=lambda c: c['member_name'].lower())
    elif criterion == 'rarity':
        rarity_order = {'Common': 1, 'Rare': 2, 'Epic': 3, 'Legendary': 4}
        cards.sort(key=lambda c: rarity_order.get(c['rarity'], 0))
    elif criterion == 'edition':
        cards.sort(key=lambda c: int(c['edition']))

    # 📄 Pagination
    pages = [cards[i:i + 5] for i in range(0, len(cards), 5)]

    # 📦 Load CollectionView
    view = CollectionView(ctx, pages, emoji="📸", target=ctx.author)
    embed = view.generate_embed()
    view.message = await ctx.send(embed=embed, view=view)

@bot.command()
async def recycle(ctx, card_uid: str):
    user_id = int(ctx.author.id)
    card_uid = card_uid.upper().strip()

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM user_cards WHERE user_id = $1 AND card_uid = $2",
            user_id,
            card_uid
        )

        if not row:
            await ctx.send(f"❌ You don’t own a card with ID `{card_uid}`.")
            return

        rarity = row['rarity']
        member_name = row['member_name']

        rarity_coin_values = {
            'Common': 5,
            'Rare': 10,
            'Epic': 20,
            'Legendary': 50,
            'Mythic': 150
        }
        coins_earned = rarity_coin_values.get(rarity, 1)

        async with conn.transaction():
            # Delete the card
            await conn.execute("""
                DELETE FROM user_cards 
                WHERE user_id = $1 AND card_uid = $2
            """, user_id, card_uid)

            # Add coins (insert if new, else update)
            await conn.execute("""
                INSERT INTO users (user_id, coins)
                VALUES ($1, $2)
                ON CONFLICT (user_id)
                DO UPDATE SET coins = users.coins + $2
            """, user_id, coins_earned)

        await ctx.send(
            f"♻️ You recycled a [{rarity}] **{member_name}** card (`#{card_uid}`) for **{coins_earned}** coins!"
        )

@bot.command()
async def coins(ctx):
    user_id = int(ctx.author.id)

    async with db_pool.acquire() as conn:
        coins = await conn.fetchval("""
            SELECT coins FROM users WHERE user_id = $1
        """, user_id)

    await ctx.send(f"💰 You have **{coins or 0} coins**.")

@bot.command()
async def shop(ctx):
    # EMBED FOR !shop COMMAND
    embed = discord.Embed(
        title="✨ SHOP COMING SOON!!!! ✨",
        description=f"{ctx.author.mention}, the shop will be opening soon. Please wait!",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="COMING SOON",
        value="`BUY ANOTHER CLAIM` Buy another claim that you can use.",
        inline=False
    )

    embed.add_field(
        name="COMING SOON",
        value="`BUY A REROLL` Buy another drop.",
        inline=False
    )

    await ctx.send(embed=embed)

# !view command
@bot.command()
async def view(ctx, card_uid: str):
    user_id = ctx.author.id
    card_uid = card_uid.upper()

    async with db_pool.acquire() as conn:
        # Check if the card is in user's collection
        card = await conn.fetchrow("""
            SELECT uc.card_uid, uc.edition, uc.rarity, uc.group_name, uc.member_name,
                   c.image_path
            FROM user_cards uc
            JOIN cards c ON uc.card_uid = c.card_uid
            WHERE uc.user_id = $1 AND uc.card_uid = $2
        """, user_id, card_uid)

        if not card:
            await ctx.send("❌ Card not in your collection.")
            return

        # Load image and apply frame
        image_path = card["image_path"]
        full_path = os.path.join("card_images", image_path)

        if not os.path.exists(full_path):
            await ctx.send("❌ Card image not found.")
            return

        framed_image = apply_frame(full_path, card["rarity"])
        image_bytes = io.BytesIO()
        framed_image.save(image_bytes, format="PNG")
        image_bytes.seek(0)

        # Send embed with framed image
        file = discord.File(fp=image_bytes, filename="card.png")
        embed = discord.Embed(
            title=f"{card['group_name']} {card['member_name']} [{card['rarity']}]",
            description=f"Edition: {card['edition']}",
            color=discord.Color.purple()
        )
        embed.set_image(url="attachment://card.png")

        await ctx.send(file=file, embed=embed)
    
@bot.command()
async def bothelp(ctx):
    # EMBED FOR HELP COMMAND
    embed = discord.Embed(
        title="✨ COMMANDS YOU CAN USE! ✨",
        description=f"{ctx.author.mention}, here are the commands you can use:",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="🃏 Drop Cards",
        value="`!drop` — Drop a set of cards that anyone can claim.",
        inline=False
    )

    embed.add_field(
        name="📁 View Collection",
        value="`!collection` — View your collection of claimed cards. You can also view someone else's collection @user. Can view your collection by rarity or member_name. !collection rarity, !collection member_name",
        inline=False
    )

    embed.add_field(
        name="🔁 Trade Cards",
        value="`!trade @user <card_uid>` — Propose a trade with someone!",
        inline=False
    )

    embed.add_field(
        name="🎴 My Cards",
        value="`!mycards <name>` — See a list of the cards you currently own by name.",
        inline=False
    )

    embed.add_field(
        name="📷 Tag",
        value="`!tag <emoji>` — Change the tag of your collection! -*More tag variants to come*-",
        inline=False
    )

    embed.add_field(
        name="♻️ Recycle",
        value="`!recycle <card_uid>` — Discard a card that you don't want for coins!",
    )

    embed.add_field(
        name="💰 Coins",
        value="`!coins — See how much coins you have.",
    )
    
    embed.add_field(
        name="💰 Shop",
        value="`!shop — SHOP COMING SOON!!",
    )

    embed.add_field(
        name="🤓",
        value="`!another command` — -*MORE FEATURES AND COMMANDS COMING!!*-",
        inline=False
    )

    await ctx.send(embed=embed)

@bot.command()
async def suggestion(ctx, *, message):
    entry = {
        "user": str(ctx.author),
        "suggestion": message,
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    }
    add_entry(SUGGESTIONS_FILE, entry)
    await ctx.send("✅ Suggestion recorded!")

@bot.command()
async def bugfix(ctx, *, message):
    entry = {
        "user": str(ctx.author),
        "bug": message,
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    }
    add_entry(BUGFIXES_FILE, entry)
    await ctx.send("🛠️ Bug report recorded!")

@bot.command()
async def viewsuggestions(ctx):
    with open(SUGGESTIONS_FILE, "r") as f:
        data = json.load(f)

    if not data:
        await ctx.send("📭 No suggestions yet!")
        return

    embed = discord.Embed(title="📢 Suggestions", color=discord.Color.green())
    for entry in data[:5]:  # show latest 5
        embed.add_field(
            name=f"{entry['user']} ({entry['timestamp']})",
            value=entry["suggestion"],
            inline=False
        )
    await ctx.send(embed=embed)

@bot.command()
async def viewbugs(ctx):
    from json_data_helpers import read_entries  # Or wherever it's stored

    bug_reports = read_entries(BUGFIXES_FILE)

    if not bug_reports:
        await ctx.send("🛠️ No bug reports yet!")
        return

    embed = discord.Embed(title="Bug Reports", color=discord.Color.red())

    for entry in bug_reports[:5]:  # Show most recent 5
        embed.add_field(
            name=f"{entry['user']} ({entry['timestamp']})",
            value=entry["bug"],
            inline=False
        )

    await ctx.send(embed=embed)

bot.run(TOKEN)
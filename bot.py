from discord.ext import commands
import discord
import os
import io
from dotenv import load_dotenv
from json_data_helpers import card_collection, load_collections, save_collections, ensure_card_ids
import random
from image_helpers import apply_frame, merge_cards_horizontally, resize_image
import asyncio
import time
from collections import defaultdict
from utils.paginator import CollectionView
from utils.shop import ShopView
import asyncpg
from datetime import datetime, timezone, timedelta
from data_helpers import add_entry, read_entries
import json
from utils.pagination import HelpPaginator

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

RARITY_POINTS = {
    "Common": 1,
    "Rare": 5,
    "Epic": 15,
    "Legendary": 75,
    "Mythic": 200
}

leaderboard_cache = {}
last_cache_update = 0
CACHE_DURATION = 300  # 5 minutes

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

            # Claimed card into user_cards table
                await conn.execute("""
                    INSERT INTO user_cards(user_id, card_uid, short_id, date_obtained, rarity, edition, member_name, group_name, concept, image_path)
                    VALUES($1, $2, $3, CURRENT_TIMESTAMP, $4, $5, $6, $7, $8, $9)
                """, int(user.id), card['card_uid'], card['short_id'],
                    card['rarity'], edition, card['name'], card['group'], card.get('concept', 'Base'), card['image'])
                
                points = RARITY_POINTS.get(card['rarity'], 0)
                leaderboard_cache[user.id] = leaderboard_cache.get(user.id, 0) + points


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

# !collection command
@bot.command(name="collection", aliases=["pc"])
async def collection(ctx, *args):
    target = None
    sort_key = "date_obtained"
    filter_value = None

    # Parse arguments
    for arg in args:
        if isinstance(arg, discord.Member):
            target = arg
        elif arg.lower() in ["group", "member", "rarity", "date"]:
            sort_key = arg.lower()
        else:
            filter_value = arg.lower()  # treat anything else as a filter (rarity or group name)

    target = target or ctx.author
    user_id = target.id

    # Map user-friendly input to database column
    sort_aliases = {
        "group": "group_name",
        "member": "member_name",
        "rarity": "rarity",
        "date": "date_obtained"
    }
    sort_key = sort_aliases.get(sort_key, sort_key)

    # Get user's tag emoji
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT emoji FROM users WHERE user_id = $1", user_id)
        emoji = row["emoji"] if row and row["emoji"] else "📸"

    # Validate sort key
    valid_sorts = {
        "date_obtained": "date_obtained DESC",
        "rarity": """
            CASE rarity
                WHEN 'Common' THEN 1
                WHEN 'Rare' THEN 2
                WHEN 'Epic' THEN 3
                WHEN 'Legendary' THEN 4
                WHEN 'Mythic' THEN 5
                ELSE 6
            END
        """,
        "member_name": "member_name ASC",
        "group_name": "group_name ASC"
    }
    order_by = valid_sorts.get(sort_key, "date_obtained DESC")

    # Build filtering condition
    filter_clause = ""
    params = [user_id]
    if filter_value:
        if filter_value in ["common", "rare", "epic", "legendary", "mythic"]:
            filter_clause = "AND LOWER(rarity) = $2"
        else:
            filter_clause = "AND (LOWER(group_name) = $2 OR LOWER(member_name) = $2)"
        params.append(filter_value)

    # Get cards
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT * FROM user_cards
            WHERE user_id = $1
            {filter_clause}
            ORDER BY {order_by};
        """, *params)

    if not rows:
        await ctx.send(f"{target.display_name} doesn't have any matching photocards. 😢")
        return

    # PAGINATION
    page_size = 10
    pages = [rows[i:i + page_size] for i in range(0, len(rows), page_size)]
    view = CollectionView(ctx, pages, emoji, target, sort_key)
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
            WHERE user_id = $1 AND LOWER(card_uid) = LOWER($2)
        """, sender_id, card_uid)

        if not card:
            await ctx.send("❌ You don't own a card with that UID.")
            return

        # Save pending trade
        pending_trades[sender_id] = {
            "recipient_id": recipient_id,
            "card_uid": card_uid,
            "member_name": card['member_name'],
            "rarity": card['rarity'],
            "message_id": None
        }

        # ✅ Create framed card preview
        image_path = card["image_path"]
        if image_path and os.path.exists(image_path):
            framed = apply_frame(image_path, FRAME_PATH)
            buffer = io.BytesIO()
            framed.save(buffer, format="PNG")
            buffer.seek(0)
            file = discord.File(buffer, filename="trade_card.png")
            image_url = "attachment://trade_card.png"
        else:
            file = None
            image_url = None

        # Create embed (no mention here)
        embed = discord.Embed(
            title="📸 Photocard Offer",
            color=discord.Color.gold()
        )
        embed.add_field(name="Card", value=f"[{card['rarity']}] **{card['member_name']}**", inline=False)
        embed.add_field(name="UID", value=f"`{card_uid}`", inline=False)
        embed.set_footer(text="React with 🤝 to accept or ❌ to decline.")

        if image_url:
            embed.set_image(url=image_url)
        else:
            embed.add_field(name="⚠️ Note", value="Image preview not available.", inline=False)

        # ✅ Send the text message separately
        message = await ctx.send(
            f"{partner.mention}, **{ctx.author.display_name}** wants to trade you this photocard!", 
            embed=embed, 
            file=file if file else None
        )

        await message.add_reaction("🤝")
        await message.add_reaction("❌")

        pending_trades[sender_id]["message_id"] = message.id

        # Timeout auto-cancel (5 min)
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
async def tag(ctx, *args):
    user_id = ctx.author.id

    if len(args) == 1:
        # ✅ Global tag for entire collection
        emoji = args[0]
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO users (user_id, emoji)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE SET emoji = EXCLUDED.emoji
            """, user_id, emoji)
        await ctx.send(f"✅ Your entire collection is now tagged with {emoji}!")

    elif len(args) == 2:
        # ✅ Per-card tag using custom_tag column
        card_uid, emoji = args
        async with db_pool.acquire() as conn:
            # Check that user owns the card
            card = await conn.fetchrow("""
                SELECT card_uid FROM user_cards
                WHERE user_id = $1 AND card_uid = $2
            """, user_id, card_uid)

            if not card:
                await ctx.send("❌ You don't own this card.")
                return

            await conn.execute("""
                UPDATE user_cards
                SET custom_tag = $1
                WHERE user_id = $2 AND card_uid = $3
            """, emoji, user_id, card_uid)

        await ctx.send(f"✅ Tagged card `#{card_uid}` with {emoji}!")

    else:
        await ctx.send("❌ Usage:\n"
                       "`!tag 😎` → Tag entire collection\n"
                       "`!tag CARD_UID 😎` → Tag a specific card")

    
# !c your cards <card_uid>
@bot.command()
async def c(ctx, *, card_name: str):
    user_id = ctx.author.id
    card_name = card_name.upper()  # Keep uppercase for display

    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT card_uid, group_name, member_name, rarity, concept, edition
            FROM user_cards
            WHERE user_id = $1
              AND LOWER(member_name) ~* ('\\m' || LOWER($2) || '\\M')  -- word boundary search
            ORDER BY date_obtained DESC        
        """, user_id, card_name)  # ✅ Removed the extra % symbols

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
        edition = row["edition"] or "Unknown"
        emoji = emoji_map.get(rarity, "🎴")

        embed.add_field(
            name=f"{i}. {emoji} {group} • {name} • {concept} • [{rarity}] • Edition {edition} `#{uid}`",
            value="",
            inline=False
        )

    embed.set_footer(text='Use "!trade @user <uid>" to trade a specific card.')

    await ctx.send(embed=embed)

# !cd command
@bot.command()
async def cd(ctx):
    user_id = ctx.author.id
    now = datetime.now(timezone.utc)

    # --- DROP & CLAIM COOLDOWNS ---
    current_time = time.time()
    last_drop = drop_cooldowns.get(user_id)
    last_claim = user_cooldowns.get(user_id)

    drop_remaining = max(0, int(DROP_COOLDOWN_DURATION - (current_time - last_drop))) if last_drop else 0
    claim_remaining = max(0, int(COOLDOWN_DURATION - (current_time - last_claim))) if last_claim else 0

    # --- DAILY COOLDOWN (midnight reset) ---
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT last_daily FROM users WHERE user_id = $1", user_id)
        if row and row["last_daily"]:
            last_daily = row["last_daily"].replace(tzinfo=timezone.utc)
            today_reset = now.replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow_reset = today_reset + timedelta(days=1)

            if last_daily >= today_reset:
                # User has already claimed today, show time until next midnight
                remaining_daily = int((tomorrow_reset - now).total_seconds())
            else:
                # User has not claimed today
                remaining_daily = 0
        else:
            remaining_daily = 0

    def format_time(seconds):
        hours, remainder = divmod(seconds, 3600)
        minutes, sec = divmod(remainder, 60)
        if seconds > 0:
            return f"{hours}h {minutes}m"
        return "Ready ✅"
    
    embed = discord.Embed(title="⏳ Your Cooldowns", color=discord.Color.orange())
    embed.add_field(name="Drop Cooldown", value=format_time(drop_remaining), inline=False)
    embed.add_field(name="Claim Cooldown", value=format_time(claim_remaining), inline=False)
    embed.add_field(name="Daily Reset", value=format_time(remaining_daily), inline=False)

    await ctx.send(embed=embed)

# VIEW COMMAND !view
@bot.command()
async def view(ctx, card_uid: str):
    """View a specific photocard by its unique card_uid."""
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        card = await conn.fetchrow("""
            SELECT *
            FROM user_cards
            WHERE user_id = $1 AND card_uid = $2
        """, int(user_id), card_uid.upper())  # Force uppercase for consistency

    if not card:
        await ctx.send(f"⚠️ You don't own a card with UID `{card_uid}`.")
        return

    # Build framed image directly from the saved path
    image_path = card["image_path"]
    if not image_path or not os.path.exists(image_path):
        await ctx.send("⚠️ The image file for this card couldn't be found.")
        return

    framed = apply_frame(image_path, FRAME_PATH)
    buffer = io.BytesIO()
    framed.save(buffer, format="PNG")
    buffer.seek(0)
    file = discord.File(fp=buffer, filename="card.png")

    # Embed card details
    embed = discord.Embed(
        title=f"{card['member_name']} ({card['group_name']})",
        description=(
            f"🆔 **{card['card_uid']}**\n"
            f"⭐ **Rarity:** {card['rarity']}\n"
            f"📀 **Version:** {card['concept']}\n"
            f"🔢 **Edition:** #{card['edition']}\n"
            f"📅 **Obtained:** {card['date_obtained'].strftime('%Y-%m-%d')}"
        ),
        color=discord.Color.blue()
    )
    embed.set_image(url="attachment://card.png")

    await ctx.send(file=file, embed=embed)

# !daily
@bot.command()
async def daily(ctx):
    user_id = int(ctx.author.id)
    reward = random.randint(1, 10)
    now = datetime.now(timezone.utc)  # Current UTC time

    # Get today's reset time (midnight UTC)
    today_reset = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    async with db_pool.acquire() as conn:
        # Ensure user row exists
        await conn.execute("""
            INSERT INTO users (user_id, coins, last_daily)
            VALUES ($1, 0, NULL)
            ON CONFLICT (user_id) DO NOTHING;
        """, user_id)

        row = await conn.fetchrow("SELECT coins, last_daily FROM users WHERE user_id = $1", user_id)
        current_coins = row["coins"]
        last_daily = row["last_daily"]

        # Handle timezone awareness
        if last_daily is not None and last_daily.tzinfo is None:
            last_daily = last_daily.replace(tzinfo=timezone.utc)

        # ✅ If they've already claimed today
        if last_daily is not None and last_daily >= today_reset:
            # Calculate time until next reset
            tomorrow_reset = today_reset + timedelta(days=1)
            remaining = tomorrow_reset - now
            hours, remainder = divmod(remaining.seconds, 3600)
            minutes = remainder // 60
            await ctx.send(
                f"🕒 You've already claimed your daily coins! "
                f"Come back in {remaining.days * 24 + hours}h {minutes}m (midnight UTC reset)."
            )
            return

        # ✅ Otherwise, award the daily reward
        new_total = current_coins + reward
        await conn.execute(
            "UPDATE users SET coins = $1, last_daily = $2 WHERE user_id = $3",
            new_total, now, user_id
        )

        await ctx.send(f"✅ You received {reward} aura points 🌟 for your daily check-in! You now have 🌟 {new_total} aura.")

# !r RECYCLE COMMAND
@bot.command(name="r", aliases=["recycle"])
async def recycle(ctx, *args):
    """Recycle cards by UID, rarity, or emoji tag for coins."""
    user_id = int(ctx.author.id)

    if not args:
        await ctx.send("❌ You must specify at least one `card_uid`, a rarity, or an emoji tag.")
        return

    # Define rarity values
    rarity_coin_values = {
        'COMMON': 5,
        'RARE': 10,
        'EPIC': 20,
        'LEGENDARY': 50,
        'MYTHIC': 150
    }

    total_earned = 0
    recycled_cards = []

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            for arg in args:
                arg_clean = arg.strip().upper()

                # ✅ CASE 1: Rarity filter
                if arg_clean in rarity_coin_values:
                    rows = await conn.fetch("""
                        SELECT card_uid, member_name, rarity FROM user_cards
                        WHERE user_id = $1 AND UPPER(rarity) = $2
                    """, user_id, arg_clean)

                # ✅ CASE 2: Emoji filter (tagged cards)
                elif len(arg) <= 4 and not arg_clean.isalnum():
                    rows = await conn.fetch("""
                        SELECT card_uid, member_name, rarity FROM user_cards
                        WHERE user_id = $1 AND custom_tag = $2
                    """, user_id, arg)

                # ✅ CASE 3: Specific UID
                else:
                    row = await conn.fetchrow("""
                        SELECT card_uid, member_name, rarity FROM user_cards
                        WHERE user_id = $1 AND UPPER(card_uid) = $2
                    """, user_id, arg_clean)
                    rows = [row] if row else []

                # ♻️ Process cards
                if not rows:
                    await ctx.send(f"⚠️ No matching cards found for `{arg}`.")
                    continue

                for row in rows:
                    await conn.execute("""
                        DELETE FROM user_cards
                        WHERE user_id = $1 AND card_uid = $2
                    """, user_id, row['card_uid'])

                    total_earned += rarity_coin_values.get(row['rarity'].upper(), 1)
                    recycled_cards.append(f"[{row['rarity']}] **{row['member_name']}** (`{row['card_uid']}`)")

            # ✅ Add coins
            if total_earned > 0:
                await conn.execute("""
                    INSERT INTO users (user_id, coins)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id)
                    DO UPDATE SET coins = users.coins + $2
                """, user_id, total_earned)

    # ✅ Final response
    if recycled_cards:
        recycled_list = "\n".join(recycled_cards)
        await ctx.send(
            f"♻️ You recycled the following cards:\n{recycled_list}\n\n"
            f"💰 Total earned: **{total_earned} aura 🌟!**"
        )
    else:
        await ctx.send("⚠️ No valid cards were recycled.")

# !aura
@bot.command()
async def aura(ctx):
    user_id = int(ctx.author.id)

    async with db_pool.acquire() as conn:
        coins = await conn.fetchval("""
            SELECT coins FROM users WHERE user_id = $1
        """, user_id)

    await ctx.send(f"🌟 You have **{coins or 0} aura**.")

async def update_leaderboard_cache(force=False):
    global leaderboard_cache, last_cache_update
    now = time.time()

    # Only reload DB if forced or cache is empty
    if not force and leaderboard_cache:
        return leaderboard_cache

    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id, rarity
            FROM user_cards
        """)

    scores = {}
    for row in rows:
        uid = row["user_id"]
        rarity = row["rarity"]
        points = RARITY_POINTS.get(rarity, 0)
        scores[uid] = scores.get(uid, 0) + points

    leaderboard_cache = dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))
    last_cache_update = now
    return leaderboard_cache

# !rank command
@bot.command()
async def rank(ctx):
    user_id = ctx.author.id
    async with db_pool.acquire() as conn:
        # Get total points for this user but only Legendary and Mythic cards
        user_row = await conn.fetchrow("""
            SELECT SUM(
                       CASE rarity
                           WHEN 'Legendary' THEN 75
                           WHEN 'Mythic' THEN 200
                           ELSE 0
                       END
                   ) AS total_points
            FROM user_cards
            WHERE user_id = $1 AND rarity IN ('Legendary', 'Mythic')
        """, user_id)
        user_points = user_row['total_points'] or 0

        # Get rank based on Legendary and Mythic only
        rank_row = await conn.fetchrow("""
            SELECT COUNT(*) + 1 AS rank
            FROM (
                SELECT user_id, SUM(
                    CASE rarity
                        WHEN 'Legendary' THEN 75
                        WHEN 'Mythic' THEN 200
                        ELSE 0
                    END
                ) AS total_points
                FROM user_cards
                WHERE rarity IN ('Legendary', 'Mythic')
                GROUP BY user_id
            ) AS leaderboard
            WHERE total_points > (
                SELECT SUM(
                    CASE rarity
                        WHEN 'Legendary' THEN 75
                        WHEN 'Mythic' THEN 200
                        ELSE 0
                    END
                )
                FROM user_cards
                WHERE user_id = $1 AND rarity IN ('Legendary', 'Mythic')
            )
        """, user_id)
        rank_position = rank_row['rank'] if rank_row else 1

    embed = discord.Embed(title=f"📊 {ctx.author.display_name}'s Rank", color=discord.Color.blue())
    embed.add_field(name="Total Points", value=f"**{user_points}**", inline=False)
    embed.add_field(name="Leaderboard Position", value=f"#{rank_position}", inline=False)

    await ctx.send(embed=embed)



# !leaderboard command
@bot.command()
async def leaderboard(ctx):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id,
                   SUM(
                       CASE rarity
                           WHEN 'Legendary' THEN 75
                           WHEN 'Mythic' THEN 200
                           ELSE 0
                       END
                   ) AS total_points
            FROM user_cards
            WHERE rarity IN ('Legendary', 'Mythic')
            GROUP BY user_id
            ORDER BY total_points DESC
            LIMIT 15;
        """)

    if not rows:
        await ctx.send("📊 No leaderboard data yet.")
        return

    embed = discord.Embed(title="🏆 _WORLD Leaderboard", color=discord.Color.gold())

    for i, row in enumerate(rows, 1):
        member = ctx.guild.get_member(row['user_id'])
        if member:
            display_name = member.nick or member.display_name
        else:
            user = await bot.fetch_user(row['user_id'])
            display_name = user.display_name

        # points = row['total_points'] or 0
        embed.add_field(
            name=f"#{i} {display_name}",
            value="",
            inline=False
        )

    await ctx.send(embed=embed)

# !shop
@bot.command()
async def shop(ctx):
    view = ShopView(ctx.author.id, db_pool)  # ✅ Pass db_pool to handle purchases

    embed = discord.Embed(
        title="💎🌟 Mingyu's LOVE.MONEY.FAME Shop",
        description="What are you buying?",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="🎴 Extra Drop — 100 coins",
        value="Use this to drop a new set of cards.",
        inline=False
    )

    embed.add_field(
        name="📥 Extra Claim — 75 coins",
        value="Use this to claim another card even after you've hit the limit.",
        inline=False
    )

    embed.set_footer(text="Click a button below to purchase.")

    await ctx.send(embed=embed, view=view)

# !reroll
@bot.command()
async def reroll(ctx):
    user_id = ctx.author.id
    reroll_cost = 50  # cost of reroll in coins

    async with db_pool.acquire() as conn:
        # Check if user exists
        user = await conn.fetchrow("SELECT coins FROM users WHERE user_id = $1", user_id)
        if not user:
            await ctx.send("❌ You don't have an account yet. Use `!drop` first to start collecting!")
            return

        coins = user["coins"]

        # Check if user has enough coins
        if coins < reroll_cost:
            await ctx.send(f"❌ You need **{reroll_cost} 🌟 aura points** to buy a reroll pack. You only have {coins}.")
            return

        # Deduct coins
        await conn.execute("UPDATE users SET coins = coins - $1 WHERE user_id = $2", reroll_cost, user_id)

    # ✅ Remove cooldown for this reroll
    if user_id in drop_cooldowns:
        del drop_cooldowns[user_id]

    # ✅ Confirm purchase
    embed = discord.Embed(
        title="🎴 Reroll Pack Purchased!",
        description=f"{ctx.author.mention} spent **{reroll_cost} 🌟 aura points** for a new pack drop! 🎉",
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed)

    # ✅ Trigger a "drop" immediately
    await drop(ctx)
    
@bot.command()
async def comms(ctx):
    # EMBED FOR HELP COMMAND
    pages = []

    # Page 1
    embed1 = discord.Embed(title="✨ Mingyu Bot Help (1/3) ✨",
                           description="Here are the commands you can use:",
                           color=discord.Color.blue())
    embed1.add_field(name="🃏 Drop Cards", value="`!drop` — Drop a set of cards that anyone can claim.", inline=False)
    embed1.add_field(name="📁 View Collection", value="`!collection` — View your card collection.", inline=False)
    embed1.add_field(name="🎴 Cards", value="`!c <name>` — View your owned cards by an idol's name.", inline=False)
    embed1.add_field(name="🔁 Trade Cards", value="`!trade @user <card_uid>` — Propose a trade.", inline=False)
    embed1.add_field(name="♻️ Recycle", value="`!r <card_uid>` — Discard a card for coins.", inline=False)
    embed1.add_field(name="📷 Tag", value="`!tag <emoji>` — Customize your collection tag.", inline=False)
    pages.append(embed1)

    # Page 2
    embed2 = discord.Embed(title="✨ Mingyu Bot Help (2/3) ✨",
                           description="Trading and managing cards:",
                           color=discord.Color.blue())
    embed2.add_field(name="💰 Aura Points", value="`!aura` — Check your balance.", inline=False)
    embed2.add_field(name="💰 Shop", value="`!shop` — Shop (coming soon!).", inline=False)
    pages.append(embed2)

    # Page 3
    embed3 = discord.Embed(title="✨ Mingyu Bot Help (3/3) ✨",
                           description="Coins and shop:",
                           color=discord.Color.blue())
    embed3.add_field(name="🤓", value="More features coming soon!", inline=False)
    pages.append(embed3)

    view = HelpPaginator(pages, ctx)
    view.message = await ctx.send(embed=pages[0], view=view)

bot.run(TOKEN)
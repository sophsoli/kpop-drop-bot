from discord.ext import commands
import discord
import os
import io
from dotenv import load_dotenv
from json_data_helpers import card_collection, load_collections, ensure_card_ids
import random
from image_helpers import apply_frame, merge_cards_horizontally, resize_image
import asyncio
import time
from collections import defaultdict
from utils.paginator import CollectionView
from utils.shop import ShopView
import asyncpg
from datetime import datetime, timezone, timedelta
from utils.pagination import HelpPaginator
from utils.recycle import ConfirmRecycleView

current_time = datetime.now(timezone.utc)

FRAME_PATH = "./images/frame.png"
MYTHIC_FRAME_PATH = "./images/scuff_frame.png"

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = 1397431382741090314

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all(), case_insensitive=True)
bot.remove_command('help')

# Load cards database at startup
cards = card_collection()

user_collections = defaultdict(list, ensure_card_ids(load_collections()))

PRIORITY_WINDOW = 10  # Seconds only the dropper can claim


# RARITY TIER AND POINTS
RARITY_TIERS = {
    "Common": {"chance": 54},
    "Rare": {"chance": 25},
    "Epic": {"chance": 15},
    "Legendary": {"chance": 5},
    "Mythic": {"chance": 1}
}

RARITY_POINTS = {
    "Common": 1,
    "Rare": 5,
    "Epic": 20,
    "Legendary": 100,
    "Mythic": 150
}

# leaderboard
leaderboard_cache = {}
last_cache_update = 0
CACHE_DURATION = 300  # 5 minutes

# INITIALIZE COOLDOWNS
user_cooldowns = {}
drop_cooldowns = {}
claim_cooldowns = {}

# COOLDOWN TIMERS
DROP_COOLDOWN_DURATION = 1800 # 30 MINS
COOLDOWN_DURATION = 900 # 15 MINS


db_pool = None

# bot connect
async def get_db_pool():
    return await asyncpg.create_pool(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        ssl="require"
    )

def assign_rarity():
    roll = random.randint(1, 100)
    total = 0
    for rarity, data in RARITY_TIERS.items():
        total += data["chance"]
        if roll <= total:
            return rarity
    return "Common"  # Fallback

# choose rarity
async def choose_rarity_for_card(card, conn):
    rarity = assign_rarity()

    if rarity == "Mythic" and card.get("limit_mythic") == 1:
        existing = await conn.fetchval("""
            SELECT COUNT(*)
            FROM user_cards
            WHERE member_name = $1
                AND concept = $2
                AND rarity = 'Mythic'
        """, card['name'], card.get("concept", "Base"))
        if existing >= 1:
            while rarity == "Mythic":
                rarity = assign_rarity()
    return rarity

def get_card_by_emoji(emoji, dropped_cards):
    for card in dropped_cards:
        if card['reaction'] == emoji:
            return card
    return None

def generate_card_uid(name, short_id, edition):
    name_code = ''.join(filter(str.isalpha, name.upper()))[:4]
    return f"{name_code}{short_id:02}{edition:02}"

@bot.event
async def on_ready():
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(
            host=os.getenv("DB_HOST"),
            port = int(os.getenv("DB_PORT", 5432)),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME"),
            ssl="require"
        )
        await bot.change_presence(activity=discord.Game(name="!drop to play"))
    print(f"Mingyu Bot ready and connected to DB!")

# drop command !drop
@bot.command()
@commands.cooldown(1, 5, commands.BucketType.user)  # 1 use per 5 seconds per user
async def drop(ctx):
    user_id = ctx.author.id
    channel = bot.get_channel(CHANNEL_ID)
    now = time.time()
    used_extra_drop = False
    used_extra_claim = False

    # Send a message if !drop is used in the wrong channel
    if ctx.channel.id != CHANNEL_ID:
        await ctx.send(f"Hey! The photocards are not in this area.")
        return
    
    # Check dropper cooldown
    if user_id in drop_cooldowns:
        elapsed = now - drop_cooldowns[user_id]
        if elapsed < DROP_COOLDOWN_DURATION:
            async with db_pool.acquire() as conn:
                item = await conn.fetchrow("""
                    SELECT quantity FROM user_items
                    WHERE user_id = $1 AND item = 'extra_drop'
                """, user_id)

            async with db_pool.acquire() as conn:
                async with conn.transaction():
                    item = await conn.fetchrow("""
                        SELECT quantity FROM user_items
                        WHERE user_id = $1 AND item = 'extra_drop' FOR UPDATE
                    """, user_id)

                    if item and item["quantity"] > 0:
                        await conn.execute("""
                            UPDATE user_items
                            SET quantity = quantity - 1
                            WHERE user_id = $1 AND item = 'extra_drop'
                        """, user_id)
                        used_extra_drop = True
                        await ctx.send(f"🎴 {ctx.author.mention}, you used an **Extra Drop**! No cooldown applied.")
                    
                    else:
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

    async with db_pool.acquire() as conn:
        for i, card in enumerate(selected_cards):
            rarity = await choose_rarity_for_card(card, conn)
            card_copy = card.copy()
            card_copy['rarity'] = rarity
            card_copy['reaction'] = reactions[i]
            dropped_cards.append(card_copy)
    
    # await notify_wishlist_users(ctx, dropped_cards, db_pool)

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

    embed.set_image(url="attachment://drop.png")
    message = await ctx.send(file=file, embed=embed)

    # Add reactions to drop message
    for card in dropped_cards:
        await message.add_reaction(card['reaction'])

    dropped_idols = {card["name"].title() for card in dropped_cards}

    # remove if this doesn't work
    if not used_extra_drop:
        drop_cooldowns[user_id] = now
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id, card_name
            FROM wishlists
            WHERE LOWER(card_name) = ANY($1::text[])
        """, [idol.lower() for idol in dropped_idols])

    if rows:
        user_alerts = {}
        for row in rows:
            uid = row["user_id"]
            idol = row["card_name"].title()  # ✅ FIXED this line
            user_alerts.setdefault(uid, []).append(idol)

        alert_lines = []
        for uid, idols in user_alerts.items():
            mention = f"<@{uid}>"
            idol_list = ", ".join(idols)
            alert_lines.append(f"{mention} wished for: {idol_list}!")

        await ctx.send("🌟 **Wishlist Alert!**\n" + "\n".join(alert_lines))

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

            # log challengers
            if user.id not in claim_challengers[emoji]:
                claim_challengers[emoji].append(user.id)

            # priority window without message
            if user.id != dropper_id and (now - drop_time) < PRIORITY_WINDOW:
                continue

            # cooldown check
            if user.id in user_cooldowns:
                elapsed = now - user_cooldowns[user.id]
                if elapsed < COOLDOWN_DURATION:
                    # Check if user has Extra Claim item
                    async with db_pool.acquire() as conn:
                        item = await conn.fetchrow("""
                            SELECT quantity FROM user_items
                            WHERE user_id = $1 AND item = 'extra_claim'
                        """, user.id)
                    
                    async with db_pool.acquire() as conn:
                        async with conn.transaction():
                            item = await conn.fetchrow("""
                                SELECT quantity FROM user_items
                                WHERE user_id = $1 AND item = 'extra_claim' FOR UPDATE
                            """, user.id)

                            if item and item["quantity"] > 0:
                                await conn.execute("""
                                    UPDATE user_items
                                    SET quantity = quantity - 1
                                    WHERE user_id = $1 AND item = 'extra_claim'
                                """, user.id)

                                used_extra_claim = True
                                await ctx.send(f"📥 {user.mention}, you used an **Extra Claim**! No cooldown applied.")
                            else:
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
                await ctx.send(f"{user.mention} gained a {card['rarity']}-Tier **{card['name']}** `{card['card_uid']}` photocard! 🤩")

            claimed[emoji] = user.id
            already_claimed_users.add(user.id)

            # remove if this doesn't work
            if not used_extra_claim:
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
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return
    
    sender_id = next(
        (sid for sid, t in pending_trades.items() if t["message_id"] == payload.message_id),
        None
    )

    if sender_id is None:
        return
    
    trade = pending_trades[sender_id]
    if payload.user_id != trade["recipient_id"]:
        return
    
    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)
    user = bot.get_user(payload.user_id)

    emoji = str(payload.emoji)

    if emoji == "🤝":
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE user_cards
                SET user_id = $1,
                    date_obtained = $2,
                    custom_tag = NULL
                WHERE LOWER(card_uid) = LOWER($3) AND user_id = $4
                """,
                user.id, datetime.now(timezone.utc),
                trade["card_uid"], sender_id
            )
        await channel.send(f"✅ Trade successful! [**{trade['rarity']}**] **{trade['member_name']}** photocard is now added to your collection!")
        del pending_trades[sender_id]
    elif emoji == "❌":
        await channel.send("❌ Trade was declined.")
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

    elif len(args) >= 2:
        # ✅ Multi-card tagging
        *card_uids, emoji = args  # All args except the last one are UIDs
        card_uids = [uid.upper().strip() for uid in card_uids]

        async with db_pool.acquire() as conn:
            # Check which cards user owns
            owned_cards = await conn.fetch("""
                SELECT card_uid FROM user_cards
                WHERE user_id = $1 AND card_uid = ANY($2::text[])
            """, user_id, card_uids)

            owned_uids = [row["card_uid"] for row in owned_cards]
            missing_uids = [uid for uid in card_uids if uid not in owned_uids]

            if missing_uids:
                await ctx.send(f"⚠️ You don't own these cards: {', '.join(missing_uids)}")
                return

            # Update all tagged cards
            await conn.execute("""
                UPDATE user_cards
                SET custom_tag = $1
                WHERE user_id = $2 AND card_uid = ANY($3::text[])
            """, emoji, user_id, owned_uids)

        if len(card_uids) == 1:
            await ctx.send(f"✅ Tagged card `#{card_uids[0]}` with {emoji}!")
        else:
            await ctx.send(f"✅ Tagged {len(card_uids)} cards with {emoji}!")

    else:
        await ctx.send("❌ Usage:\n"
                       "`!tag 😎` → Tag entire collection\n"
                       "`!tag CARD_UID 😎` → Tag a specific card\n"
                       "`!tag CARD1 CARD2 CARD3 😎` → Tag multiple cards")

# customize card_uid 
@bot.command()
async def customize(ctx, old_uid: str, new_uid: str):
    user_id = ctx.author.id
    cost = 500  # aura cost for customization

    # Enforce formatting (optional)
    new_uid = new_uid.upper().strip()

    if not new_uid.isalnum() or len(new_uid) > 10:
        await ctx.send("❌ UID must be alphanumeric and less than 10 characters.")
        return

    async with db_pool.acquire() as conn:
        # 1️⃣ Check if user owns the card
        card = await conn.fetchrow("""
            SELECT * FROM user_cards
            WHERE user_id = $1 AND LOWER(card_uid) = LOWER($2)
        """, user_id, old_uid)

        if not card:
            await ctx.send("❌ You don't own a card with that UID.")
            return

        # 2️⃣ Check if new UID is already taken
        exists = await conn.fetchval("""
            SELECT 1 FROM user_cards WHERE LOWER(card_uid) = LOWER($1)
        """, new_uid)

        if exists:
            await ctx.send("❌ That UID is already in use! Please choose a different one.")
            return

        # 3️⃣ Check aura balance
        balance = await conn.fetchval("""
            SELECT COALESCE(coins, 0) FROM users WHERE user_id = $1
        """, user_id)

        if balance < cost:
            await ctx.send(f"❌ You need {cost} aura to customize a card UID. You currently have {balance}.")
            return

        # 4️⃣ Deduct aura and update UID
        async with conn.transaction():
            await conn.execute("""
                UPDATE users
                SET coins = coins - $1
                WHERE user_id = $2
            """, cost, user_id)

            await conn.execute("""
                UPDATE user_cards
                SET card_uid = $1
                WHERE user_id = $2 AND LOWER(card_uid) = LOWER($3)
            """, new_uid, user_id, old_uid)

        # 5️⃣ Confirm success
        embed = discord.Embed(
            title="✨ UID Customized!",
            description=f"Your card **{card['member_name']}** has been updated:\n`{old_uid}` → `{new_uid}`",
            color=discord.Color.gold()
        )
        embed.set_footer(text=f"-{cost} aura spent • Remaining: {balance - cost}")
        await ctx.send(embed=embed)

# !cd command COOLDOWN COMMAND
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

    def format_time(seconds, show_hours=False):
        seconds = max(0, seconds)
        hours, remainder = divmod(seconds, 3600)
        minutes, sec = divmod(remainder, 60)
        if seconds > 0:
            if show_hours:
                return f"{hours}h {minutes}m {sec}s" # for daily
            else:
                return f"{minutes}m {sec}s" # for drop and claim
        return "Ready ✅"
    
    embed = discord.Embed(title="⏳ Your Cooldowns", color=discord.Color.orange())
    embed.add_field(name="Drop Cooldown", value=format_time(drop_remaining), inline=False)
    embed.add_field(name="Claim Cooldown", value=format_time(claim_remaining), inline=False)
    embed.add_field(name="Daily Reset", value=format_time(remaining_daily, show_hours=True), inline=False)

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

    # ✅ Pick correct frame based on rarity
    rarity = card["rarity"]
    if rarity.lower() == "mythic":
        frame_path = MYTHIC_FRAME_PATH
    else:
        frame_path = FRAME_PATH

    # Build framed image directly from the saved path
    image_path = card["image_path"]
    if not image_path or not os.path.exists(image_path):
        await ctx.send("⚠️ The image file for this card couldn't be found.")
        return

    framed = apply_frame(image_path, frame_path)
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
        color=discord.Color.gold() if rarity.lower() == "mythic" else discord.Color.blue()
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
            matched_rows = []

            # Collect matching cards first
            for arg in args:
                arg_clean = arg.strip().upper()

                if arg_clean in rarity_coin_values:
                    rows = await conn.fetch("""
                        SELECT card_uid, member_name, rarity FROM user_cards
                        WHERE user_id = $1 AND UPPER(rarity) = $2
                    """, user_id, arg_clean)
                elif len(arg) <= 4 and not arg_clean.isalnum():
                    rows = await conn.fetch("""
                        SELECT card_uid, member_name, rarity FROM user_cards
                        WHERE user_id = $1 AND custom_tag = $2
                    """, user_id, arg)
                else:
                    row = await conn.fetchrow("""
                        SELECT card_uid, member_name, rarity FROM user_cards
                        WHERE user_id = $1 AND UPPER(card_uid) = $2
                    """, user_id, arg_clean)
                    rows = [row] if row else []

                if rows:
                    matched_rows.extend(rows)

            if not matched_rows:
                await ctx.send("⚠️ No matching cards found.")
                return

            # Ask for confirmation if 5 or more cards
            if len(matched_rows) >= 5:
                embed = discord.Embed(
                    title="♻️ Confirm Recycling",
                    description=f"You are about to recycle **{len(matched_rows)} cards**.\nDo you want to proceed?",
                    color=discord.Color.orange()
                )
                embed.set_footer(text="This will earn you aura based on rarity.")
                view = ConfirmRecycleView(ctx)
                msg = await ctx.send(embed=embed, view=view)
                view.message = msg
                await view.wait()

                if view.value is None:
                    await msg.edit(content="⌛ Confirmation timed out.", embed=None, view=None)
                    return
                elif view.value is False:
                    await msg.edit(content="❌ Recycling cancelled.", embed=None, view=None)
                    return
                else:
                    await msg.edit(content="✅ Recycling Confirmed", embed=None, view=None)

            # Delete cards and calculate coins
            for row in matched_rows:
                await conn.execute("""
                    DELETE FROM user_cards
                    WHERE user_id = $1 AND card_uid = $2
                """, user_id, row['card_uid'])
                total_earned += rarity_coin_values.get(row['rarity'].upper(), 1)
                recycled_cards.append(f"[{row['rarity']}] **{row['member_name']}** (`{row['card_uid']}`)")

            if total_earned > 0:
                await conn.execute("""
                    INSERT INTO users (user_id, coins)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id)
                    DO UPDATE SET coins = users.coins + $2
                """, user_id, total_earned)

    recycled_list = "\n".join(recycled_cards)
    embed = discord.Embed(
        title="♻️ Cards Recycled",
        description=f"You recycled the following cards:\n{recycled_list}",
        color=discord.Color.green()
    )

    embed.add_field(
        name="",
        value=f"💰 Total earned: **{total_earned} aura 🌟!**",
        inline=False
    )

    await ctx.send(embed=embed)

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

# give aura command !give
@bot.command()
async def give(ctx, member: discord.Member, amount: int):
    sender_id = ctx.author.id
    recipient_id = member.id

    if amount <= 0:
        await ctx.send("❌ Please enter a valid amount of aura to give.")
        return
    
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # check sender balance
            sender_balance = await conn.fetchval(
                "SELECT coins FROM users WHERE user_id = $1",
                sender_id
            ) or 0

            if sender_balance < amount:
                await ctx.send("❌ You don't have enough aura.")
                return
            
            if sender_id == recipient_id:
                await ctx.send("❌ Hey! You can't give aura to yourself.")
                return
            
            # minus from sender
            await conn.execute(
                "UPDATE users SET coins = coins - $1 WHERE user_id = $2",
                amount, sender_id
            )

            # add to recipient
            await conn.execute("""
                INSERT into users (user_id, coins)
                VALUES ($1, $2)
                ON CONFLICT (user_id)
                DO UPDATE SET coins = users.coins + EXCLUDED.coins
            """, recipient_id, amount)

    await ctx.send(
        f"🤑 {ctx.author.display_name} gave {amount} aura 🌟 to {member.display_name}!"
    )

# !rank command
@bot.command()
async def rank(ctx):
    user = ctx.author
    user_id = ctx.author.id
    async with db_pool.acquire() as conn:
        # Get total points for this user but only Legendary and Mythic cards
        user_row = await conn.fetchrow("""
            SELECT SUM(
                       CASE rarity
                           WHEN 'Common' THEN 1
                           WHEN 'Rare' THEN 5
                           WHEN 'Epic' THEN 20
                           WHEN 'Legendary' THEN 100
                           WHEN 'Mythic' THEN 150
                           ELSE 0
                       END
                   ) AS total_points
            FROM user_cards
            WHERE user_id = $1 AND rarity IN ('Common', 'Rare', 'Epic', 'Legendary', 'Mythic')
        """, user_id)
        user_points = user_row['total_points'] or 0

        # Get rank based on Legendary and Mythic only
        rank_row = await conn.fetchrow("""
            SELECT COUNT(*) + 1 AS rank
            FROM (
                SELECT user_id, SUM(
                    CASE rarity
                        WHEN 'Common' THEN 1
                        WHEN 'Rare' THEN 5
                        WHEN 'Epic' THEN 20
                        WHEN 'Legendary' THEN 100
                        WHEN 'Mythic' THEN 150
                        ELSE 0
                    END
                ) AS total_points
                FROM user_cards
                WHERE rarity IN ('Common', 'Rare', 'Epic', 'Legendary', 'Mythic')
                GROUP BY user_id
            ) AS leaderboard
            WHERE total_points > (
                SELECT SUM(
                    CASE rarity
                        WHEN 'Common' THEN 1
                        WHEN 'Rare' THEN 5
                        WHEN 'Epic' THEN 20
                        WHEN 'Legendary' THEN 100
                        WHEN 'Mythic' THEN 150
                        ELSE 0
                    END
                )
                FROM user_cards
                WHERE user_id = $1 AND rarity IN ('Common', 'Rare', 'Epic', 'Legendary', 'Mythic')
            )
        """, user_id)
        rank_position = rank_row['rank'] if rank_row else 1

    embed = discord.Embed(title=f"📊 {ctx.author.display_name}'s Rank", color=discord.Color.blue())
    embed.add_field(name="Total Points", value=f"**{user_points}**", inline=False)
    embed.add_field(name="Leaderboard Position", value=f"#{rank_position}", inline=False)

    embed.set_thumbnail(url=user.display_avatar.url)

    await ctx.send(embed=embed)



# !leaderboard command
@bot.command()
async def leaderboard(ctx):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id,
                   SUM(
                       CASE rarity
                           WHEN 'Common' THEN 1
                           WHEN 'Rare' THEN 5
                           WHEN 'Epic' THEN 20
                           WHEN 'Legendary' THEN 100
                           WHEN 'Mythic' THEN 150
                           ELSE 0
                       END
                   ) AS total_points
            FROM user_cards
            WHERE rarity IN ('Common', 'Rare', 'Epic', 'Legendary', 'Mythic')
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
        name="🎴 Extra Drop — 100 aura",
        value="Use this to drop a new set of cards.",
        inline=False
    )

    embed.add_field(
        name="📥 Extra Claim — 75 aura",
        value="Use this to claim another card even after you've hit the limit.",
        inline=False
    )

    embed.add_field(
        name="🆔 Customize Card UID — 500 aura",
        value="Replace your card_uid with a customized one! (Up to 10 characters)",
        inline=False
    )

    embed.set_footer(text="Click a button below to purchase.")

    message = await ctx.send(embed=embed, view=view)
    view.message = message  # 👈 assign after sending


# !items or !i ITEMS COMMAND
@bot.command(name="items", aliases=["i"])
async def items(ctx):
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT item, quantity FROM user_items
            WHERE user_id = $1
        """, user_id)

    embed = discord.Embed(title="💼 Your Items", color=discord.Color.green())

    if not rows:
        embed.description = "📦 You don't have any items."
    else:
        for row in rows:
            item = row["item"]
            quantity = row["quantity"]

            if item == "drops_left":
                name = "🎴 Extra Drops"
            elif item == "claims_left":
                name = "📥 Extra Claims"
            else:
                name = item.replace("_", " ").title()

            embed.add_field(name=name, value=f"Quantity: {quantity}", inline=False)

    await ctx.send(embed=embed)

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

# !wl wishlist command
@bot.command(name="wishlist", aliases=["wl"])
async def wishlist(ctx, action=None, *, card_name=None):
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        # ✅ View Wishlist
        if action is None:
            rows = await conn.fetch("SELECT card_name FROM wishlists WHERE user_id = $1", user_id)
            if not rows:
                await ctx.send(f"📜 {ctx.author.mention}, your wishlist is empty!")
                return

            wishlist = "\n".join([f"• {row['card_name']}" for row in rows])
            embed = discord.Embed(title=f"💖 {ctx.author.display_name}'s Wishlist", description=wishlist, color=discord.Color.pink())
            await ctx.send(embed=embed)
            return

        # Normalize card name regardless of case
        if card_name:
            titleized_name = card_name.strip().title()
        else:
            titleized_name = None

        # ✅ Add to Wishlist
        if action.lower() == "add":
            if not titleized_name:
                await ctx.send("⚠️ Please specify the card name to add!")
                return

            try:
                await conn.execute(
                    "INSERT INTO wishlists (user_id, card_name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    user_id, titleized_name
                )
                await ctx.send(f"⭐ Added **{titleized_name}** to your wishlist!")
            except Exception as e:
                await ctx.send("❌ Error adding to wishlist.")

        # ✅ Remove from Wishlist
        elif action.lower() == "remove":
            if not titleized_name:
                await ctx.send("⚠️ Please specify the card name to remove!")
                return

            deleted = await conn.execute("DELETE FROM wishlists WHERE user_id = $1 AND card_name = $2", user_id, titleized_name)
            if deleted.endswith("0"):
                await ctx.send(f"⚠️ **{titleized_name}** wasn't on your wishlist.")
            else:
                await ctx.send(f"🗑️ Removed **{titleized_name}** from your wishlist!")

        else:
            await ctx.send("⚠️ Invalid option! Use `!wl`, `!wl add <card>`, or `!wl remove <card>`.")
    
@bot.command()
async def help(ctx):
    # EMBED FOR HELP COMMAND
    pages = []

    # Page 1
    embed1 = discord.Embed(title="✨ Mingyu Bot Help (1/3) ✨",
                           description="Here are the commands you can use:",
                           color=discord.Color.blue())
    embed1.add_field(name="🃏 Drop Cards", value="`!drop` — Drop a set of cards that anyone can claim.", inline=False)
    embed1.add_field(name="📁 View Collection", value="`!collection` or `!pc` — View your card collection. Can sort by group, member, or rarity.", inline=False)
    embed1.add_field(name="🎴 Cards", value="`!c <name>` — View your owned cards by an idol's name.", inline=False)
    embed1.add_field(name="🔁 Trade Cards", value="`!trade @user <card_uid>` — Propose a trade.", inline=False)
    embed1.add_field(name="♻️ Recycle", value="`!r <card_uid>` — Discard a card for coins. Can multi-recycle. Can also recycle by a tag.", inline=False)
    embed1.add_field(name="📷 Tag", value="`!tag <emoji>` or `!tag <card_uid> emoji`  — Customize your collection tag. Can add different tags for cards.", inline=False)
    embed1.add_field(name="✅ Daily", value="`!daily` — Random daily check-in!", inline=False)
    pages.append(embed1)

    # Page 2
    embed2 = discord.Embed(title="✨ Mingyu Bot Help (2/3) ✨",
                           description="Shop and Points:",
                           color=discord.Color.blue())
    embed2.add_field(name="🌟 Aura Points", value="`!aura` — Check your balance.", inline=False)
    embed2.add_field(name="💰 Shop", value="`!shop` — Shop (coming soon!).", inline=False)
    embed2.add_field(name="🏆 Leaderboard", value="`!leaderboard` — Check leaderboard.", inline=False)
    embed2.add_field(name="🏆 Rank", value="`!rank` — Check your rank.", inline=False)
    embed1.add_field(name="🔁 Reroll", value="`!reroll` — Didn't like your drop? Reroll for 50 aura points!", inline=False)
    pages.append(embed2)

    # Page 3
    embed3 = discord.Embed(title="✨ Mingyu Bot Help (3/3) ✨",
                           description="More coming soon:",
                           color=discord.Color.blue())
    embed3.add_field(name="🤓", value="More features coming soon!", inline=False)
    pages.append(embed3)

    view = HelpPaginator(pages, ctx)
    view.message = await ctx.send(embed=pages[0], view=view)

bot.run(TOKEN)
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

current_time = datetime.now(timezone.utc)


FRAME_PATH = "./images/frame.png"

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = 1336418461240528931

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

# Load cards database at startup
cards = card_collection()

user_collections = defaultdict(list, ensure_card_ids(load_collections()))


RARITY_TIERS = {
    "Common": {"color": 0xAAAAAA, "chance": 60},
    "Rare": {"color": 0x3498DB, "chance": 25},
    "Epic": {"color": 0x9B59B6, "chance": 10},
    "Legendary": {"color": 0xFFD700, "chance": 5},
}

user_cooldowns = {}
drop_cooldowns = {}

COOLDOWN_DURATION = 3600
DROP_COOLDOWN_DURATION = 7200 # 2 hours

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


    embed.set_image(url="attachment://drop.png")
    message = await ctx.send(file=file, embed=embed)


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
            reaction, user = await bot.wait_for("reaction_add", timeout=30.0, check=check)
            emoji = str(reaction.emoji)

            # cooldown check
            now = time.time()

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
            # if user.id in already_claimed_users:
            #     await ctx.send(f"{user.mention}, you've already claimed a card!")
            #     continue

            if emoji in claimed:
                await ctx.send(f"⚠️ Sorry {user.mention} that card is out of stock.")
                continue

            og_card = get_card_by_emoji(emoji, dropped_cards)
            card = og_card.copy()
            card.pop("reaction", None)

            # # Assign unique identifiers before DB Query
            # user_id_str = str(user.id)
            # user_cards = user_collections[user_id_str]

            # def get_next_short_id(collection):
            #     if not collection:
            #             return 1
            #     else:
            #         max_id = max((c.get("short_id", 0) for c in collection), default=0)
            #         return max_id + 1
            # short_id = get_next_short_id(user_cards)

            # same_cards = [c for c in user_cards if c["name"] == card["name"] and c["rarity"] == card["rarity"]]
            # edition = len(same_cards) + 1
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT MAX(short_id::int) AS max_short_id
                    FROM user_cards
                    WHERE user_id = $1
                """, user.id)
            short_id = (row['max_short_id'] or 0) + 1

            card["short_id"] = short_id
            card["edition"] = edition
            card["uid"] = generate_card_uid(card["name"], short_id, edition)

            # DATABASE -- AFTER UID IS SET
            async with db_pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT COUNT(*) FROM user_cards
                    WHERE user_id = $1 AND card_uid = $2
                """, user.id, card['uid'])
                count = rows[0]['count'] if rows else 0
                edition = count + 1


            # Claimed card into user_cards table
                await conn.execute("""
                    INSERT INTO user_cards(user_id, card_uid, short_id, date_obtained, rarity, edition, member_name, group_name)
                    VALUES($1, $2, $3, CURRENT_TIMESTAMP, $4, $5, $6, $7)
                """, int(user.id), card['uid'], str(card['short_id']),
                    card['rarity'], edition, card['name'], card['group'])


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
async def collection(ctx, member: discord.Member = None):
    target = member or ctx.author
    user_id = target.id

    # get user's tag emoji from postgressql
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT emoji FROM users WHERE user_id = $1", user_id)
        emoji = row["emoji"] if row and row["emoji"] else "📸"

    # Get user's card
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM user_cards
            WHERE user_id = $1
            ORDER BY date_obtained DESC;
        """, int(user_id))
    
    if not rows:
        await ctx.send(f"{target.display_name} doesn't have any photocards yet. 😢")
        return
    
    # PAGINATION SETUP
    page_size = 5
    pages = [rows[i:i + page_size] for i in range(0, len(rows), page_size)]
    view = CollectionView(ctx, pages, emoji, target)
    embed = view.generate_embed()
    view.message = await ctx.send(embed=embed, view=view)

    # # EMBED to show collection
    # embed = discord.Embed(
    #     title=f"📸 {target.display_name}'s Photocard Collection 📚\n\n",
    #     color=discord.Color.blue(),
    # )


    # for row in rows[:5]:
    #     embed.add_field(
    #         name=f"{emoji} {row['group_name']} • {row['member_name']} • {row['rarity']} • Edition {row['edition']}",
    #         value="",
    #         inline=False
    #     )

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
            SELECT card_uid, group_name, member_name, rarity
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
        "Ultra Rare": "🟥",
        "Legendary": "🌟"
    }

    for i, row in enumerate(rows, 1):
        uid = row["card_uid"]
        group = row["group_name"] or "Unknown"
        name = row["member_name"] or "Unknown"
        rarity = row["rarity"] or "Unknown"
        emoji = emoji_map.get(rarity, "🎴")

        embed.add_field(
            name=f"{i}. {emoji} {group} • {name} • ({rarity}) • #{uid}",
            value="",
            inline=False
        )

    embed.set_footer(text='Use "!trade @user <uid>" to trade a specific card.')

    await ctx.send(embed=embed)


bot.run(TOKEN)
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


FRAME_PATH = "./images/frame.png"

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = 1336418461240528931

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

# Load cards database at startup
cards = card_collection()

user_collections = defaultdict(list, ensure_card_ids(load_collections()))

user_emojis = defaultdict(lambda: "🔥", load_user_emojis())  # Default to fire emoji


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

            # Assign unique identifiers before DB Query
            user_id_str = str(user.id)
            user_cards = user_collections[user_id_str]

            def get_next_short_id(collection):
                if not collection:
                        return 1
                else:
                    max_id = max((c.get("short_id", 0) for c in collection), default=0)
                    return max_id + 1
            short_id = get_next_short_id(user_cards)

            same_cards = [c for c in user_cards if c["name"] == card["name"] and c["rarity"] == card["rarity"]]
            edition = len(same_cards) + 1

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
                    INSERT INTO user_cards(user_id, card_uid, short_id, date_obtained)
                    VALUES($1, $2, $3, CURRENT_TIMESTAMP)
                """, int(user.id), card['uid'], str(card['short_id']))


            challengers = [cid for cid in claim_challengers[emoji] if cid != user.id]
            if challengers:
                fought_off_mentions = ", ".join(f"<@{cid}>" for cid in challengers)
                await ctx.send(f"{user.mention} fought off {fought_off_mentions} and gained a {card['rarity']}-Tier **{card['name']}** photocard! 🤩")
            else:
                await ctx.send(f"{user.mention} gained a {card['rarity']}-Tier **{card['name']}** photocard! 🤩")

            # user_cards.append(card)
            # save_collections(user_collections)

            claimed[emoji] = user.id
            already_claimed_users.add(user.id)
            user_cooldowns[user.id] = now

        except asyncio.TimeoutError:
            break

# COLLECTION COMMAND !collection        
# @bot.command()
# async def collection(ctx, member: discord.Member = None):
#     # If no member is specified, default to the command author
#     user = member or ctx.author
#     user_id = str(user.id)

#     # reload the latest collections from file, ensuring IDs
#     all_collections = defaultdict(list, ensure_card_ids(load_collections()))
#     cards = all_collections.get(user_id, [])

#     if not cards:
#         await ctx.send(f"{user.display_name} doesn't have any photocards yet. 😢")
#         return

@bot.command()
async def collection(ctx):
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.*, uc.date_obtained, uc.short_id
            FROM user_cards uc
            JOIN cards c ON uc.card_uid = c.card_uid
            WHERE uc.user_id = $1
            ORDER BY uc.date_obtained DESC;
        """, int(user_id))
    
    if not rows:
        await ctx.send(f"{ctx.author.display_name} doesn't have any photocards yet. 😢")
        return

    # EMBED to show collection
    embed = discord.Embed(
        title=f"📸 {ctx.author.display_name}'s Photocard Collection 📚\n\n",
        color=discord.Color.blue(),
        inline=False
    )

    emoji = user_emojis.get(user_id, "🔥")

    print(f"Rows fetched for user {user_id}: {rows}")

    for row in rows[:5]:
        embed.add_field(
            name=f"{row['member_name']} ({row['rarity']})",
            value=f"ID: `{row['short_id']}` • Edition: {row['edition']}\nObtained: {row['date_obtained'].strftime('%Y-%m-%d')}",
            inline=False
        )

    await ctx.send(embed=embed)
    

    # view = CollectionView(ctx, user, cards, emoji)

    # # for card in cards:
    # #     short_id = card.get("short_id", 0)
    # #     edition = card.get("edition", 1)
    # #     name = card.get("name", "Unknown")
    # #     group = card.get("group", "Unknown")
    # #     rarity = card.get("rarity", "Unknown")

    # #     uid = card.get("uid")
    # #     if not uid:
    # #         name_code = ''.join(filter(str.isalpha, name.upper()))[:4]
    # #         uid = f"{name_code}{short_id:02}{edition:02}"

    #     # embed.add_field(
    #     #     name="",
    #     #     value=f"{emoji} {card['group']} • {card['name']} • {card['rarity']} • Edition {edition}",
    #     #     inline=False
    #     # )

    # await view.send()

pending_trades = {}

# COMMAND TRADE !trade
@bot.command()
async def trade(ctx, member: discord.Member, uid: str):
    sender_id = str(ctx.author.id)
    recipient_id = str(member.id)

    # Reload Collection
    user_collections = defaultdict(list, ensure_card_ids(load_collections()))

    # Get Sender's Cards
    sender_cards = user_collections.get(sender_id, [])

    if sender_id not in user_collections:
        await ctx.send("You don't have any cards to trade.")
        return
    
    card = next((c for c in sender_cards if c.get("uid") == uid), None)
    if not card:
        await ctx.send(f"❌ You don't have a card with UID `{uid}` in your collection.")
        return
    
    if sender_id not in pending_trades:
        pending_trades[sender_id] = {}

    pending_trades[sender_id][recipient_id] = {
        "uid": uid,
        "status": "pending"
    }

    message = await ctx.send(f"{member.mention}, {ctx.author.display_name} wants to trade you a [**{card['rarity']}**] **{card['name']}** photocard. Accept?")
    await message.add_reaction("🤝")
    await message.add_reaction("❌")

    pending_trades[sender_id][recipient_id]["message_id"] = message.id

@bot.event
async def on_reaction_add(reaction, user):
    message = reaction.message
    emoji = str(reaction.emoji)

    for sender_id in list(pending_trades):
        for recipient_id in list(pending_trades[sender_id]):
            trade = pending_trades[sender_id][recipient_id]

            if trade.get("message_id") == message.id and str(user.id) == recipient_id:
                # get card by ID
                uid = trade["uid"]

                if emoji == "🤝":
                    user_collections = defaultdict(list, ensure_card_ids(load_collections()))
                    
                    sender_cards = user_collections.get(sender_id, [])
                    card = next((c for c in sender_cards if c.get("uid") == uid), None)
                    if not card:
                        await message.channel.send("Trade failed. Card no longer exists.")
                        return
                    
                    # remove from sender
                    user_collections[sender_id] = [c for c in sender_cards if c.get("uid") != uid]

                    # add card to recipient
                    user_collections[recipient_id].append(card)

                    save_collections(user_collections)
                    await message.channel.send(f"✅ Trade accepted! **{card['name']}** photocard is now added to your collection!")

                    del pending_trades[sender_id][recipient_id]
                    if not pending_trades[sender_id]:
                        del pending_trades[sender_id]

                elif emoji == "❌":
                    await message.channel.send(f"❌ Trade was declined.")

                    # clean up trade
                    del pending_trades[sender_id][recipient_id]
                    if not pending_trades[sender_id]:
                        del pending_trades[sender_id]
                    return
# TAG COMMAND !tag                
@bot.command()
async def tag(ctx, emoji):
    user_id = str(ctx.author.id)

    if len(emoji) > 2:
        await ctx.send("Invalid!")
        return

    user_emojis[user_id] = emoji
    save_user_emojis(user_emojis)
    await ctx.send(f"Tagged your collection as {emoji}!")

@bot.command()
async def mycards(ctx, *, card_name: str):
    user_id = str(ctx.author.id)
    card_name = card_name.title()
    cards = user_collections.get(user_id, [])

    matching_cards = [
        card for card in cards
        if card_name.lower() in card["name"].lower()
    ]

    if not matching_cards:
        await ctx.send(f'No cards matching "{card_name}" found in your collection.')
        return
    

    emoji = user_emojis.get(user_id, "🔥")

    embed = discord.Embed(
        title=f'📸 Your Cards Matching "{card_name}":',
        description=f"{len(matching_cards)} card(s)",
        color=discord.Color.blue()
    )

    for i, card in enumerate(matching_cards, 1):
        uid = card.get("uid", "N/A")
        name = card.get("name", "Unknown")
        group = card.get("group", "Unknown")
        rarity = card.get("rarity", "Unknown")

        embed.add_field(
            name=f"{i}. {emoji} {group} • {name} • ({rarity}) • #{uid}",
            value="",
            inline=False
        )

    embed.set_footer(text='Use "!trade @user <name> <uid>" to trade a specific card.')

    await ctx.send(embed=embed)


bot.run(TOKEN)
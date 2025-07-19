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
import uuid

FRAME_PATH = "./images/frame.png"

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = 1336418461240528931

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

# Load cards database at startup
cards = card_collection()

user_collections = defaultdict(list, ensure_card_ids(load_collections()))

user_emojis = defaultdict(lambda: "🔥", load_user_emojis())  # Default to fire emoji

# Rarities
# rarities = {
#     "Common": 50,
#     "Rare": 25,
#     "Epic": 15,
#     "Legendary": 10
# }


# # Randomize rarity to each card
# def assign_random_rarity(card):
#     rarity = random.choices(list(rarities.keys()), list(rarities.values()), k=1)[0]
#     card['rarity'] = rarity
#     return card

RARITY_TIERS = {
    "Common": {"color": 0xAAAAAA, "chance": 60},
    "Rare": {"color": 0x3498DB, "chance": 25},
    "Epic": {"color": 0x9B59B6, "chance": 10},
    "Legendary": {"color": 0xFFD700, "chance": 5},
}

user_cooldowns = {}
drop_cooldowns = {}

COOLDOWN_DURATION = 120
DROP_COOLDOWN_DURATION = 7200 # 2 hours

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

@bot.event
async def on_ready():
    # start up message
    print(f"Yo! Mingyu bot ({bot.user}) has logged in.")
    channel = bot.get_channel(CHANNEL_ID)
    # send message to channel
    await channel.send(f"Yo, Mingyu is here! Let's party!!")

# drop command
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

            # already claimed
            if user.id in already_claimed_users:
                await ctx.send(f"{user.mention}, you've already claimed a card!")
                continue

            if emoji in claimed:
                await ctx.send(f"⚠️ Sorry {user.mention} that card is out of stock.")
                continue

            if user.id == user_id:
                challengers = [cid for cid in claim_challengers[emoji] if cid != user.id]

                og_card = get_card_by_emoji(emoji, dropped_cards)
                card = og_card.copy()
                card.pop("reaction", None)
                card["id"] = str(uuid.uuid4())

                if challengers:
                    fought_off_mentions = ", ".join(f"<@{cid}>" for cid in challengers)
                    await ctx.send(f"{user.mention} fought off {fought_off_mentions} and gained a {card['rarity']}-Tier **{card['name']}** photocard! 🤩")
                else:
                    await ctx.send(f"{user.mention} gained a {card['rarity']}-Tier **{card['name']}** photocard! 🤩")
            else:
                og_card = get_card_by_emoji(emoji, dropped_cards)
                card = og_card.copy()
                card.pop("reaction", None)
                card["id"] = str(uuid.uuid4())
                await ctx.send(f"{user.mention} gained a {card['rarity']}-Tier **{card['name']}** photocard! 🤩")

            user_collections[str(user.id)].append(card)
            save_collections(user_collections)

            claimed[emoji] = user.id
            already_claimed_users.add(user.id)
            user_cooldowns[user.id] = now

        except asyncio.TimeoutError:
            break

# COLLECTION COMMAND !collection        
@bot.command()
async def collection(ctx, member: discord.Member = None):
    # If no member is specified, default to the command author
    user = member or ctx.author
    user_id = str(user.id)

    # reload the latest collections from file, ensuring IDs
    all_collections = defaultdict(list, ensure_card_ids(load_collections()))
    cards = all_collections.get(user_id, [])

    if not cards:
        await ctx.send(f"{user.display_name} doesn't have any photocards yet. 😢")
        return
    
    emoji = user_emojis.get(user_id, "🔥")
    
    embed = discord.Embed(
        title=f"📸 {user.display_name}'s Collection",
        color=discord.Color.blue()
    )

    for card in cards:
        embed.add_field(
            name="",
            value=f"{emoji} {card['group']} • {card['name']} • {card['rarity']}",
            inline=False
        )

    await ctx.send(embed=embed)

pending_trades = {}

# COMMAND TRADE !trade
@bot.command()
async def trade(ctx, member: discord.Member, card_id: str):
    sender_id = str(ctx.author.id)
    recipient_id = str(member.id)

    user_collections = defaultdict(list, load_collections())

    if sender_id not in user_collections:
        await ctx.send("You don't have any cards to trade.")
        return
    
    card = next((c for c in user_collections[sender_id] if c.get("id") == card_id), None)
    if not card:
        await ctx.send("Card not found in your inventory.")
        return
    
    if sender_id not in pending_trades:
        pending_trades[sender_id] = {}

    pending_trades[sender_id][recipient_id] = {
        "card_id": card["id"],
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
                card_id = trade["card_id"]

                if emoji == "🤝":
                    user_collections = defaultdict(list, ensure_card_ids(load_collections()))
                    
                    card = next((c for c in user_collections[sender_id] if c["id"] == card_id), None)
                    if not card:
                        await message.channel.send("Trade failed. Card no longer exists.")
                        return
                    
                    # remove from sender
                    user_collections[sender_id] = [c for c in user_collections[sender_id] if c["id"] != card_id]

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
async def mycards(ctx, *, card_name:str):
    user_id = str(ctx.author.id)
    cards = user_collections.get(user_id, [])

    matching_cards = [
        card for card in cards
        if card_name.lower() in card["name"].lower()
    ]

    if not matching_cards:
        await ctx.send(f'No cards matching "{card_name}" found in your collection.')
        return
    
    response = f'You have {len(matching_cards)} card(s) matching "{card_name}":\n'

    for i, card in enumerate(matching_cards, 1):
        emoji = user_emojis.get(user_id, "🔥")
        response += f"{i}. {emoji} {card['group']} {card['name']} {card['rarity']}\n"

    response += "\nUse `!trade @user <name> <number>` to trade a specific card."

    await ctx.send(response)


bot.run(TOKEN)
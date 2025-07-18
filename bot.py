from discord.ext import commands
import discord
import os
import io
from dotenv import load_dotenv
from json_data_helpers import card_collection
import random
from image_helpers import apply_frame, merge_cards_horizontally, resize_image
import asyncio
import time
from collections import defaultdict

FRAME_PATH = "./images/frame.png"

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = 1336418461240528931

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

# Load cards database at startup
cards = card_collection()

user_collections = defaultdict(list)

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
                await ctx.send(f"⚠️ {user.mention}, that card was already claimed.")
                continue

            if user.id == user_id:
                challengers = [cid for cid in claim_challengers[emoji] if cid != user.id]

                card = get_card_by_emoji(emoji, dropped_cards)

                if challengers:
                    fought_off_mentions = ", ".join(f"<@{cid}>" for cid in challengers)
                    await ctx.send(f"{user.mention} fought off {fought_off_mentions} and gained a {card['rarity']}-Tier **{card['name']}** photocard! 🤩")
                else:
                    await ctx.send(f"{user.mention} gained a {card['rarity']}-Tier **{card['name']}** photocard! 🤩")
            else:
                card = get_card_by_emoji(emoji, dropped_cards)
                await ctx.send(f"{user.mention} gained a {card['rarity']}-Tier **{card['name']}** photocard! 🤩")

            user_collections[user.id].append(card)

            claimed[emoji] = user.id
            already_claimed_users.add(user.id)
            user_cooldowns[user.id] = now

        except asyncio.TimeoutError:
            break
@bot.command()
async def collection(ctx, member: discord.Member = None):
    # If no member is specified, default to the command author
    user = member or ctx.author
    user_id = user.id
    cards = user_collections.get(user_id, [])

    if not cards:
        await ctx.send(f"{user.display_name} doesn't have any photocards yet. 😢")
        return
    
    embed = discord.Embed(
        title=f"📸 {user.display_name}'s Collection",
        color=discord.Color.blue()
    )

    for card in cards:
        embed.add_field(
            name=f"Sniping {user.display_name}'s collection",
            value=f"🔥 {card['group']} • {card['name']} • {card['rarity']}",
            inline=False
        )

    await ctx.send(embed=embed)

bot.run(TOKEN)
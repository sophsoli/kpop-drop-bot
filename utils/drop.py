# drop.py
import discord
from bot import generate_card_uid  # Adjust if you store this elsewhere
from collections import defaultdict

async def notify_wishlist_users(ctx, dropped_cards, db_pool):
    user_mentions = defaultdict(list)

    async with db_pool.acquire() as conn:
        for card in dropped_cards:
            card_uid_prefix = generate_card_uid(card["name"], 0, 0)[:4]
            rows = await conn.fetch(
                "SELECT user_id FROM wishlist WHERE card_uid LIKE $1", f"{card_uid_prefix}%"
            )
            for row in rows:
                user_id = row["user_id"]
                user = ctx.guild.get_member(user_id)
                if user:
                    user_mentions[card["name"]].append(user.mention)

    for card_name, mentions in user_mentions.items():
        if mentions:
            await ctx.send(
                f"🎯 {' '.join(mentions)}, your wishlist card **{card_name}** just dropped!"
            )
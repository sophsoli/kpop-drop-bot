import discord
from discord.ui import View, Button
import asyncio

class ShopView(View):
    def __init__(self, user_id, db_pool):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.db_pool = db_pool
        self.message = None

    @discord.ui.button(label="🎴 Extra Drop — 100🌟", style=discord.ButtonStyle.green)
    async def buy_extra_drop(self, interaction: discord.Interaction, button: Button):
        await self.handle_purchase(interaction, "drops_left", 100, "Extra Drop")

    @discord.ui.button(label="📥 Extra Claim — 75🌟", style=discord.ButtonStyle.blurple)
    async def buy_extra_claim(self, interaction: discord.Interaction, button: Button):
        await self.handle_purchase(interaction, "claims_left", 75, "Extra Claim")

    @discord.ui.button(label="🆔 Customize UID — 500🌟", style=discord.ButtonStyle.gray)
    async def customize_uid(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            "✏️ Please type your **current card UID** in the chat (you have 30 seconds):",
            ephemeral=True
        )

        def check(m):
            return m.author.id == self.user_id and m.channel == interaction.channel

        try:
            old_msg = await interaction.client.wait_for("message", check=check, timeout=30)
            old_uid = old_msg.content.strip()

            await interaction.channel.send("✅ Got it! Now type your **new desired UID** (≤10 characters):")
            new_msg = await interaction.client.wait_for("message", check=check, timeout=30)
            new_uid = new_msg.content.strip().upper()

            # Run customization
            await self.customize_card(interaction.channel, old_uid, new_uid)

        except asyncio.TimeoutError:
            await interaction.channel.send("⌛ Timed out! Please try again later.")

    async def handle_purchase(self, interaction, column, cost, item_name):
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT coins FROM users WHERE user_id = $1", self.user_id)
            if not row:
                await interaction.response.send_message("❌ You don't have a profile yet.", ephemeral=True)
                return

            if row["coins"] < cost:
                await interaction.response.send_message("❌ Not enough coins!", ephemeral=True)
                return

            # Deduct coins
            await conn.execute("""
                UPDATE users
                SET coins = coins - $1
                WHERE user_id = $2
            """, cost, self.user_id)

            # Add item to user_items table
            await conn.execute("""
                INSERT INTO user_items (user_id, item, quantity)
                VALUES ($1, $2, 1)
                ON CONFLICT (user_id, item)
                DO UPDATE SET quantity = user_items.quantity + 1
            """, self.user_id, column)

            await interaction.response.send_message(f"✅ You bought **1x {item_name}**!", ephemeral=True)

    async def customize_card(self, channel, old_uid, new_uid):
        cost = 500
        user_id = self.user_id

        if not new_uid.isalnum() or len(new_uid) > 10:
            await channel.send("❌ UID must be alphanumeric and ≤10 characters.")
            return

        async with self.db_pool.acquire() as conn:
            # Check ownership
            card = await conn.fetchrow("""
                SELECT * FROM user_cards
                WHERE user_id = $1 AND LOWER(card_uid) = LOWER($2)
            """, user_id, old_uid)

            if not card:
                await channel.send("❌ You don't own a card with that UID.")
                return

            # Check new UID availability
            exists = await conn.fetchval("""
                SELECT 1 FROM user_cards WHERE LOWER(card_uid) = LOWER($1)
            """, new_uid)
            if exists:
                await channel.send("❌ That UID is already taken. Try a different one.")
                return

            # Check balance
            balance = await conn.fetchval("""
                SELECT COALESCE(coins, 0) FROM users WHERE user_id = $1
            """, user_id)
            if balance < cost:
                await channel.send(f"❌ You need {cost} aura, but you only have {balance}.")
                return

            # Deduct aura and update
            async with conn.transaction():
                await conn.execute("""
                    UPDATE users SET coins = coins - $1 WHERE user_id = $2
                """, cost, user_id)
                await conn.execute("""
                    UPDATE user_cards SET card_uid = $1
                    WHERE user_id = $2 AND LOWER(card_uid) = LOWER($3)
                """, new_uid, user_id, old_uid)

        embed = discord.Embed(
            title="✨ Card UID Customized!",
            description=f"Your **{card['member_name']}** card UID has been updated:\n`{old_uid}` → `{new_uid}`",
            color=discord.Color.gold()
        )
        embed.set_footer(text=f"-{cost} aura spent • Remaining: {balance - cost}")
        await channel.send(embed=embed)
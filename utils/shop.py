import discord
from discord.ui import View, Button

class CustomizeUIDModal(discord.ui.Modal, title="Customize Card UID"):
    old_uid = discord.ui.TextInput(
        label="Old UID",
        placeholder="Enter your current card UID",
        required=True,
        max_length=20
    )
    new_uid = discord.ui.TextInput(
        label="New UID",
        placeholder="Enter your new desired UID",
        required=True,
        max_length=10
    )

    def __init__(self, user_id, db_pool):
        super().__init__()
        self.user_id = user_id
        self.db_pool = db_pool

    async def on_submit(self, interaction: discord.Interaction):
        user_id = self.user_id
        old_uid = self.old_uid.value.strip()
        new_uid = self.new_uid.value.upper().strip()
        cost = 500

        # ✅ Validate UID format
        if not new_uid.isalnum() or len(new_uid) > 10:
            await interaction.response.send_message(
                "❌ UID must be alphanumeric and ≤10 characters.",
                ephemeral=True
            )
            return

        async with self.db_pool.acquire() as conn:
            # Check ownership
            card = await conn.fetchrow("""
                SELECT * FROM user_cards
                WHERE user_id = $1 AND LOWER(card_uid) = LOWER($2)
            """, user_id, old_uid)

            if not card:
                await interaction.response.send_message(
                    "❌ You don't own a card with that UID.",
                    ephemeral=True
                )
                return

            # Check if new UID is taken
            exists = await conn.fetchval("""
                SELECT 1 FROM user_cards WHERE LOWER(card_uid) = LOWER($1)
            """, new_uid)

            if exists:
                await interaction.response.send_message(
                    "❌ That UID is already taken. Choose another.",
                    ephemeral=True
                )
                return

            # Check aura balance
            balance = await conn.fetchval("""
                SELECT COALESCE(coins, 0) FROM users WHERE user_id = $1
            """, user_id)

            if balance < cost:
                await interaction.response.send_message(
                    f"❌ You need {cost} aura. You only have {balance}.",
                    ephemeral=True
                )
                return

            # Deduct aura and update card UID
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

        # ✅ Confirmation Embed
        embed = discord.Embed(
            title="✨ Card UID Customized!",
            description=f"`{old_uid}` → `{new_uid}` for **{card['member_name']}**",
            color=discord.Color.gold()
        )
        embed.set_footer(text=f"-{cost} aura spent • Remaining: {balance - cost}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ShopView(View):
    def __init__(self, user_id, db_pool):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.db_pool = db_pool
        self.message = None  # Add this here so it's properly scoped

    @discord.ui.button(label="🎴 Buy Extra Drop (100 aura)", style=discord.ButtonStyle.green)
    async def buy_extra_drop(self, interaction: discord.Interaction, button: Button):
        await self.handle_purchase(interaction, "drops_left", 100, "Extra Drop")

    @discord.ui.button(label="📥 Buy Extra Claim (75 aura)", style=discord.ButtonStyle.blurple)
    async def buy_extra_claim(self, interaction: discord.Interaction, button: Button):
        await self.handle_purchase(interaction, "claims_left", 75, "Extra Claim")
    
    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass

    async def handle_purchase(self, interaction, column, cost, item_name):
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT coins FROM users WHERE user_id = $1", self.user_id)
            if not row:
                await interaction.response.send_message("❌ You don't have a profile yet.", ephemeral=True)
                return

            if row["coins"] < cost:
                await interaction.response.send_message("❌ Not enough coins!", ephemeral=True)
                return

            # Validate column and map it to item name
            column_to_item = {
                "drops_left": "extra_drop",
                "claims_left": "extra_claim"
            }

            if column not in column_to_item:
                await interaction.response.send_message("❌ Invalid item.", ephemeral=True)
                return

            item_key = column_to_item[column]

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
            """, self.user_id, item_key)

            await interaction.response.send_message(f"✅ You bought **1x {item_name}**!", ephemeral=True)
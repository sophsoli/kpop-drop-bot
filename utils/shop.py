import discord
from discord.ui import View, Button

class ShopView(View):
    def __init__(self, user_id, db_pool):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.db_pool = db_pool
        self.message = None  # Add this here so it's properly scoped

    @discord.ui.button(label="🎴 Buy Extra Drop (100 coins)", style=discord.ButtonStyle.green)
    async def buy_extra_drop(self, interaction: discord.Interaction, button: Button):
        await self.handle_purchase(interaction, "drops_left", 100, "Extra Drop")

    @discord.ui.button(label="📥 Buy Extra Claim (75 coins)", style=discord.ButtonStyle.blurple)
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
import discord
from discord.ui import View, Button

class ShopView(View):
    def __init__(self, user_id, db_pool):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.db_pool = db_pool  # store pool in the instance

    @discord.ui.button(label="🎴 Extra Drop (100 coins)", style=discord.ButtonStyle.green)
    async def buy_extra_drop(self, interaction: discord.Interaction, button: Button):
        await self.handle_purchase(interaction, "drops_left", 100, "drop")

    @discord.ui.button(label="📥 Extra Claim (75 coins)", style=discord.ButtonStyle.blurple)
    async def buy_extra_claim(self, interaction: discord.Interaction, button: Button):
        await self.handle_purchase(interaction, "claims_left", 75, "claim")

    async def handle_purchase(self, interaction, column, cost, item_name):
        async with self.db_pool.acquire() as conn:  # <-- fixed this line
            row = await conn.fetchrow("SELECT coins FROM users WHERE user_id = $1", self.user_id)
            if not row:
                await interaction.response.send_message("❌ You don't have a profile yet.", ephemeral=True)
                return
            if row["coins"] < cost:
                await interaction.response.send_message("❌ Not enough coins!", ephemeral=True)
                return

            await conn.execute(f"""
                UPDATE users 
                SET coins = coins - $1, {column} = {column} + 1
                WHERE user_id = $2
            """, cost, self.user_id)

            await interaction.response.send_message(f"✅ Bought 1 extra {item_name}!", ephemeral=True)
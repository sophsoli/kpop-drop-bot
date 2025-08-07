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
            
            valid_columns = {"drops_left", "claims_left"}
            if column not in valid_columns:
                await interaction.response.send_message("❌ Invalid item.", ephemeral=True)
                return
            
            query = f"""
                UPDATE users
                SET coins = coins - $1,
                    {column} = {column} + 1
                WHERE user_id = $2
            """

            await conn.execute(query, cost, self.user_id)

            await interaction.response.send_message(f"✅ You bought **1x {item_name}**!", ephemeral=True)
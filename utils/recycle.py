import discord
from discord.ui import View, Button

class ConfirmRecycleView(View):
    def __init__(self, ctx):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.value = None

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ This confirmation isn’t for you.", ephemeral=True)
            return
        self.value = True
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ This confirmation isn’t for you.", ephemeral=True)
            return
        self.value = False
        self.stop()
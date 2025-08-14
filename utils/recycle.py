import discord
from discord.ui import View, Button

class ConfirmRecycleView(View):
    def __init__(self, ctx):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.value = None  # True if confirmed, False if cancelled
    
    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(content="⌛ Confirmation timed out.", view=self)
        except:
            pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "❌ This confirmation isn’t for you.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        self.value = True
        # Edit original message instead of sending a new one
        await interaction.response.edit_message(
            content="✅ Recycling confirmed!", embed=None, view=None
        )
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        self.value = False
        await interaction.response.edit_message(
            content="❌ Recycling cancelled.", embed=None, view=None
        )
        self.stop()
import discord
from discord.ui import View, button

class HelpPaginator(View):
    def __init__(self, pages, ctx):
        super().__init__(timeout=60)
        self.pages = pages
        self.ctx = ctx
        self.index = 0
        self.message = None

    async def update_page(self):
        await self.message.edit(embed=self.pages[self.index], view=self)

    @button(label="⬅️ Prev", style=discord.ButtonStyle.blurple)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("This isn't your help menu!", ephemeral=True)
            return
        if self.index > 0:
            self.index -= 1
            await self.update_page()
        await interaction.response.defer()

    @button(label="➡️ Next", style=discord.ButtonStyle.blurple)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("This isn't your help menu!", ephemeral=True)
            return
        if self.index < len(self.pages) - 1:
            self.index += 1
            await self.update_page()
        await interaction.response.defer()
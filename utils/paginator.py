import discord
from discord.ui import View, Button
from discord import Interaction, Embed

class CollectionView(View):
    def __init__(self, ctx, pages, emoji, target):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.pages = pages
        self.emoji = emoji
        self.target = target
        self.current_page = 0
        self.message = None

    def generate_embed(self):
        embed = Embed(
            title=f"📸 {self.target.display_name}'s Photocard Collection 📚",
            description=f"Page {self.current_page + 1} of {len(self.pages)}",
            color=discord.Color.blue()
        )
        for row in self.pages[self.current_page]:
            embed.add_field(
                name=f"{self.emoji} {row['group_name']} • {row['member_name']} • {row['rarity']} • Edition {row['edition']}",
                value="",
                inline=False
            )
        return embed

    async def update_message(self, interaction: Interaction):
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

    @Button(label="⬅️", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: Interaction, button: Button):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("You can't control this menu!", ephemeral=True)
            return
        self.current_page = (self.current_page - 1) % len(self.pages)
        await self.update_message(interaction)

    @Button(label="➡️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: Interaction, button: Button):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("You can't control this menu!", ephemeral=True)
            return
        self.current_page = (self.current_page + 1) % len(self.pages)
        await self.update_message(interaction)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            await self.message.edit(view=self)
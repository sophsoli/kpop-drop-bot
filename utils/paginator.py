import discord
from discord.ui import View, Button
from discord import Interaction, Embed

class CollectionView(View):
    def __init__(self, ctx, pages, emoji, target):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.pages = pages  # A list of list-of-cards per page
        self.emoji = emoji
        self.target = target  # discord.Member
        self.current_page = 0
        self.message = None

        # Buttons
        self.prev_button = Button(label="⬅️", style=discord.ButtonStyle.secondary)
        self.next_button = Button(label="➡️", style=discord.ButtonStyle.secondary)

        self.prev_button.callback = self.prev_page
        self.next_button.callback = self.next_page

        self.add_item(self.prev_button)
        self.add_item(self.next_button)

    def generate_embed(self):
        embed = Embed(
            title=f"📸 {self.target.display_name}'s Photocard Collection 📚",
            description=f"Page {self.current_page + 1} of {len(self.pages)}",
            color=discord.Color.blue()
        )
        for row in self.pages[self.current_page]:
            embed.add_field(
                name=f"{self.emoji} {row['group_name']} • {row['member_name']} • {row['rarity']} • Edition {row['edition']}",
                value="UID: `" + row['card_uid'] + "`",
                inline=False
            )
        return embed

    async def prev_page(self, interaction: Interaction):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("This paginator isn't for you!", ephemeral=True)
            return

        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)

    async def next_page(self, interaction: Interaction):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("This paginator isn't for you!", ephemeral=True)
            return

        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            await self.update_message(interaction)

    async def update_message(self, interaction: Interaction):
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            await self.message.edit(view=self)
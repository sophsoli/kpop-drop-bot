from discord.ui import View, Button
import discord

CARDS_PER_PAGE = 5

class CollectionView(View):
    def __init__(self, ctx, target_user, cards, emoji):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.target_user = target_user
        self.cards = cards
        self.emoji = emoji
        self.current_page = 0
        self.message = None

        self.previous_button.disabled = True
        if len(cards) <= CARDS_PER_PAGE:
            self.next_button.disabled = True
        
    async def send(self):
        embed = self.create_embed()
        self.message = await self.ctx.send(embed=embed, view=self)

    def create_embed(self):
        start = self.current_page * CARDS_PER_PAGE
        end = start + CARDS_PER_PAGE
        embed = discord.Embed(
            title=f"{self.target_user.display_name}'s Collection (Page {self.current_page + 1})",
            color=discord.Color.blue()
        )
        for card in self.cards[start:end]:
            name = card.get("name", "Unknown")
            group = card.get("group", "Unknown")
            rarity = card.get("rarity", "Unknown")
            edition = card.get("edition", "N/A")
            uid = card.get("uid", "❓")
            embed.add_field(
                name="",
                value=f"{self.emoji} {group} • {name} • {rarity} • Edition {edition}",
                inline=False
            )
        return embed
    @discord.ui.button(label="Previous", style=discord.ButtonStyle.gray, disabled=True)
    async def previous_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("You're not allowed to interact with this.", ephemeral=True)
        
        self.current_page -= 1
        self.next_button.disabled = False
        self.previous_button.disabled = self.current_page == 0
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("You're not allowed to interact with this.", ephemeral=True)

        self.current_page += 1
        max_page = (len(self.cards) - 1) // CARDS_PER_PAGE
        self.previous_button.disabled = False
        self.next_button.disabled = self.current_page == max_page
        await interaction.response.edit_message(embed=self.create_embed(), view=self)
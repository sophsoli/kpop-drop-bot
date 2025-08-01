import discord
from discord.ui import View, Button
from discord import Interaction, Embed

RARITY_ORDER = {
    "Common": 1,
    "Rare": 2,
    "Epic": 3,
    "Legendary": 4,
    "Mythic": 5
}

class CollectionView(View):
    def __init__(self, ctx, pages, emoji, target, sort_key="date_obtained"):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.pages = pages
        self.emoji = emoji
        self.target = target
        self.current_page = 0
        self.message = None
        self.author = ctx.author
        self.sort_key = sort_key

        self.prev_button = Button(label="⬅️", style=discord.ButtonStyle.secondary)
        self.next_button = Button(label="➡️", style=discord.ButtonStyle.secondary)

        self.prev_button.callback = self.prev_page
        self.next_button.callback = self.next_page

        self.add_item(self.prev_button)
        self.add_item(self.next_button)

    def generate_embed(self):
        embed = Embed(
            title=f"📸 {self.target.display_name}'s Photocard Collection",
            description=f"Page {self.current_page + 1}/{len(self.pages)} • Sorted by **{self.sort_key}**",
            color=discord.Color.blue()
        )

        cards = self.pages[self.current_page]

        # Apply sorting
        if self.sort_key == "rarity":
            cards = sorted(cards, key=lambda card: RARITY_ORDER.get(card['rarity'], 99))
        elif self.sort_key == "member_name":
            cards = sorted(cards, key=lambda card: card['member_name'])
        elif self.sort_key == "group_name":
            cards = sorted(cards, key=lambda card: (card['group_name'], card['member_name']))

        # Grouped view
        if self.sort_key == "group_name":
            grouped = {}
            for card in cards:
                grouped.setdefault(card['group_name'], []).append(card)
            for group_name, group_cards in grouped.items():
                member_lines = [
                    f"{self.emoji} {c['member_name']} • [{c['rarity']}] • Edition {c['edition']}"
                    for c in group_cards
                ]
                embed.add_field(name=f"**{group_name}**", value="\n".join(member_lines), inline=False)
        else:
            for card in cards:
                line = f"{self.emoji} {card['group_name']} • {card['member_name']} • [{card['rarity']}] • Edition {card['edition']}"
                embed.add_field(name=line, value="\u200b", inline=False)

        return embed

    async def prev_page(self, interaction: Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("This paginator isn't for you!", ephemeral=True)

        self.current_page = (self.current_page - 1) % len(self.pages)
        await self.update_message(interaction)

    async def next_page(self, interaction: Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("This paginator isn't for you!", ephemeral=True)

        self.current_page = (self.current_page + 1) % len(self.pages)
        await self.update_message(interaction)

    async def update_message(self, interaction: Interaction):
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            await self.message.edit(content="⏳ This collection session has expired.", view=self)
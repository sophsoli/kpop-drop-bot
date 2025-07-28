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
        super().__init__(timeout=60)
        self.ctx = ctx
        self.pages = pages  # A list of list-of-cards per page
        self.emoji = emoji
        self.target = target  # discord.Member
        self.current_page = 0
        self.message = None
        self.author = ctx.author
        self.sort_key = sort_key  # NEW

        # Buttons
        self.prev_button = Button(label="👈", style=discord.ButtonStyle.secondary)
        self.next_button = Button(label="👉", style=discord.ButtonStyle.secondary)

        self.prev_button.callback = self.prev_page
        self.next_button.callback = self.next_page

        self.add_item(self.prev_button)
        self.add_item(self.next_button)

    def generate_embed(self):
        embed = Embed(
            title=f"📸 {self.target.display_name}'s Photocard Collection 📚",
            description=f"Page {self.current_page + 1} of {len(self.pages)} — Sorted by `{self.sort_key}`",
            color=discord.Color.blue()
        )

        cards = self.pages[self.current_page]

        if self.sort_key == "rarity":
            cards = sorted(cards, key=lambda card: RARITY_ORDER.get(card['rarity'], 0))

        if self.sort_key == "group_name":
            grouped = {}
            for card in cards:
                grouped.setdefault(card['group_name'], []).append(card)
            
            for group_name, group_cards in grouped.items():
                group_title = f"**{group_name}**"
                member_lines = []
                for card in group_cards:
                    member_lines.append(f"- {card['member_name']} • [{card['rarity']}] • Edition {card['edition']}")
                group_desc = "\n".join(member_lines)

                embed.add_field(name=group_title, value=group_desc, inline=False)

        else:
            # Sort by member_name or date_obtained if needed
            if self.sort_key == "member_name":
                cards = sorted(cards, key=lambda card: card['member_name'])
            elif self.sort_key == "date_obtained":
                if self.sort_key == "rarity":
                    cards = sorted(cards, key=lambda card: RARITY_ORDER.get(card['rarity'], 0))
                    line = f"**[{card['rarity']}]** {card['member_name']} ({card['group_name']}) — Edition {card['edition']}"
                elif self.sort_key == "member_name":
                    line = f"{card['member_name']} • {card['group_name']} • [{card['rarity']}] • Edition {card['edition']}"
                else: # default (date_obtained)
                    line = f"{card['group_name']} • {card['member_name']} • {card['rarity']} • Edition {card['edition']}"
                embed.add_field(name=f"{self.emoji} {line}", value="", inline=False)
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
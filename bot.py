from discord.ext import commands
import discord
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = 1339716688748216392

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

@bot.event
async def on_ready():
    # start up message
    print(f"Yo! Mingyu bot ({bot.user}) has logged in.")
    channel = bot.get_channel(CHANNEL_ID)
    # send message to channel
    await channel.send(f"Yo, Mingyu is here! Let's party!!")

# drop command
@bot.command()
async def drop(ctx):
    user_id = ctx.author.id
    channel = bot.get_channel(CHANNEL_ID)

    # Send a message if !drop is used in the wrong channel
    if ctx.channel.id != CHANNEL_ID:
        await ctx.send(f"Hey! The photocards are not in this area.")
        return
    
    # Announce user is dropping cards
    drop_message = await channel.send(f"🚨 {ctx.author.mention} came to drop some photocards! 🚨")
    print("Cards available for dropping: ")
bot.run(TOKEN)
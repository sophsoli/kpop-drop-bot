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

bot.run(TOKEN)
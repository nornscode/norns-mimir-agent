import asyncio
import logging
import re

import discord

from norns import NornsClient

from mimir import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("mimir.discord")

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

norns_client = NornsClient(config.NORNS_URL, api_key=config.NORNS_API_KEY)


@bot.event
async def on_ready():
    logger.info(f"Connected to Discord as {bot.user}")


@bot.event
async def on_message(message: discord.Message):
    # Skip own messages
    if message.author == bot.user:
        return

    # Respond to DMs or @mentions
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mention = bot.user in message.mentions if bot.user else False

    if not is_dm and not is_mention:
        return

    # Strip bot mention from text
    text = message.content
    if bot.user:
        text = re.sub(rf"<@!?{bot.user.id}>", "", text).strip()
    if not text:
        return

    # Map channel + thread to conversation key
    thread_id = message.channel.id
    if isinstance(message.channel, discord.Thread):
        thread_id = message.channel.id
    conversation_key = f"discord:{thread_id}"

    # Add thinking reaction
    try:
        await message.add_reaction("\U0001f914")  # thinking face
    except Exception:
        pass

    try:
        # Run in thread to avoid blocking the asyncio event loop
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: norns_client.send_message(
                "mimir",
                text,
                conversation_key=conversation_key,
                wait=True,
                timeout=120,
            ),
        )

        # Remove thinking reaction
        try:
            await message.remove_reaction("\U0001f914", bot.user)
        except Exception:
            pass

        if result.status == "completed" and result.output:
            # Discord has a 2000 char limit per message
            output = result.output
            while output:
                chunk = output[:2000]
                output = output[2000:]
                await message.reply(chunk)
        else:
            await message.reply(f"Sorry, something went wrong (status: {result.status}).")

    except TimeoutError:
        try:
            await message.remove_reaction("\U0001f914", bot.user)
        except Exception:
            pass
        await message.reply("Sorry, that took too long. Please try again.")

    except Exception as e:
        logger.error(f"Error handling message: {e}")
        try:
            await message.remove_reaction("\U0001f914", bot.user)
        except Exception:
            pass
        await message.reply("Sorry, something went wrong.")


def main():
    if not config.DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set")
    bot.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()

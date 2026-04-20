import asyncio
import json
import random
import re
import signal
import sys
from loguru import logger
from telethon import TelegramClient, events
from telethon.sessions import StringSession

with open('config.json', 'r') as f:
    config = json.load(f)

if not config['session_string']:
    print("Session string is empty. Run this script locally to generate the session string.")
    sys.exit(1)

client = TelegramClient(StringSession(config['session_string']), config['api_id'], config['api_hash'])

@client.on(events.NewMessage(from_users=[config['bot_username']]))
async def handler(event):
    try:
        text = event.message.text
        for trigger_name, trigger in config['triggers'].items():
            if re.search(trigger['pattern'], text):
                if trigger['action'] == 'send_command':
                    await client.send_message(config['bot_username'], trigger['command'])
                    logger.info(f"Sent {trigger['command']} for trigger {trigger_name}")
                elif trigger['action'] == 'random_response':
                    response = random.choice(config['responses'])
                    delay = random.uniform(config['delay_min'], config['delay_max'])
                    await asyncio.sleep(delay)
                    await event.reply(response)
                    logger.info(f"Replied with {response} after {delay:.2f}s delay for trigger {trigger_name}")
                break
    except Exception as e:
        logger.error(f"Error in handler: {e}")

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully")
    asyncio.create_task(client.disconnect())
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

async def main():
    try:
        await client.start()
        logger.info("Client started successfully")
        await client.run_until_disconnected()
    except Exception as e:
        logger.error(f"Error in main: {e}")

if __name__ == "__main__":
    asyncio.run(main())
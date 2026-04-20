import asyncio
import json
import random
import re
import signal
import sys
from loguru import logger
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

with open('config.json', 'r') as f:
    config = json.load(f)

if not config['session_string']:
    print("Session string is empty. Run this script locally to generate the session string.")
    sys.exit(1)

client = TelegramClient(StringSession(config['session_string']), config['api_id'], config['api_hash'])

@client.on(events.NewMessage(from_users=[config['bot_username']], incoming=True, outgoing=False))
@client.on(events.MessageEdited(from_users=[config['bot_username']], incoming=True, outgoing=False))
@client.on(events.MessageRead(func=lambda e: e.is_private))
async def handler(event):
    try:
        text = event.message.text
        for trigger_name, trigger in config['triggers'].items():
            pattern = re.compile(r'^' + re.escape(trigger['pattern']) + r'$', re.IGNORECASE | re.MULTILINE)
            if pattern.search(text):
                if trigger['action'] == 'send_command':
                    try:
                        await client.send_message(config['bot_username'], trigger['command'])
                        logger.info(f"Sent {trigger['command']} for trigger {trigger_name}")
                    except FloodWaitError as e:
                        logger.warning(f"Flood wait: waiting {e.seconds}s")
                        await asyncio.sleep(e.seconds)
                        await client.send_message(config['bot_username'], trigger['command'])
                elif trigger['action'] == 'random_response':
                    response = random.choice(config['responses'])
                    delay = random.uniform(config['delay_min'], config['delay_max'])
                    await asyncio.sleep(delay)
                    try:
                        await event.reply(response)
                        logger.info(f"Replied with {response} after {delay:.2f}s delay for trigger {trigger_name}")
                    except FloodWaitError as e:
                        logger.warning(f"Flood wait: waiting {e.seconds}s")
                        await asyncio.sleep(e.seconds)
                        await event.reply(response)
                 break
        except AttributeError:
            # Handle messages with no text (media only)
            pass
    except Exception as e:
        logger.error(f"Error in handler: {e}")

shutdown_event = asyncio.Event()

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully")
    shutdown_event.set()

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

async def main():
    retry_delay = 5
    max_retry_delay = 300
    
    while not shutdown_event.is_set():
        try:
            await client.start()
            logger.info("Client started successfully")
            retry_delay = 5  # Reset retry delay on successful connection
            
            # Save session string on first successful login
            if not config.get('session_string') or config['session_string'] != client.session.save():
                config['session_string'] = client.session.save()
                with open('config.json', 'w') as f:
                    json.dump(config, f, indent=2)
                logger.info("Session string saved to config.json")
            
            while not shutdown_event.is_set():
                try:
                    await asyncio.wait_for(client.run_until_disconnected(), timeout=30)
                except asyncio.TimeoutError:
                    continue
                break
                    
        except Exception as e:
            logger.error(f"Error in main: {e}, reconnecting in {retry_delay}s")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)
            # Recreate client on connection failure to avoid session corruption
            client = TelegramClient(StringSession(config['session_string']), config['api_id'], config['api_hash'])
    
    logger.info("Shutting down client")
    await client.disconnect()
    logger.info("Client disconnected successfully")

if __name__ == "__main__":
    asyncio.run(main())
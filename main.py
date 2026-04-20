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

# Precompile all trigger patterns once at startup
for trigger in config['triggers'].values():
    pattern = re.compile(r'\A' + re.escape(trigger['pattern'].replace('\n', ' ')) + r'\Z', re.IGNORECASE | re.DOTALL)
    trigger['_compiled_pattern'] = pattern

def register_handlers(client, track_task=None):
    @client.on(events.NewMessage(from_users=[config['bot_username']], incoming=True, outgoing=False))
    @client.on(events.MessageEdited(from_users=[config['bot_username']], incoming=True, outgoing=False))
    @client.on(events.MessageRead(from_users=[config['bot_username']]))
    async def handler(event):
        try:
            text = event.message.text
            for trigger_name, trigger in config['triggers'].items():
                if trigger['_compiled_pattern'].search(text):
                    async def send_with_backoff(action, max_retries=3):
                        for attempt in range(max_retries):
                            try:
                                await action()
                                return True
                            except FloodWaitError as e:
                                logger.warning(f"Flood wait (attempt {attempt+1}): waiting {e.seconds}s")
                                await asyncio.sleep(e.seconds)
                        logger.error(f"Failed after {max_retries} flood wait attempts")
                        return False
                    
                    if trigger['action'] == 'send_command':
                        task = asyncio.create_task(send_with_backoff(
                            lambda: client.send_message(config['bot_username'], trigger['command'])
                        ))
                        if track_task:
                            track_task(task)
                        logger.info(f"Sent {trigger['command']} for trigger {trigger_name}")
                    elif trigger['action'] == 'random_response':
                        response = random.choice(config['responses'])
                        delay = random.uniform(config['delay_min'], config['delay_max'])
                        await asyncio.sleep(delay)
                        task = asyncio.create_task(send_with_backoff(
                            lambda: event.reply(response)
                        ))
                        if track_task:
                            track_task(task)
                        logger.info(f"Replied with {response} after {delay:.2f}s delay for trigger {trigger_name}")
                    break
            except AttributeError:
                # Handle messages with no text (media only)
                pass
        except asyncio.CancelledError:
            # Task was cancelled intentionally during shutdown/reconnect
            raise
        except Exception as e:
            logger.error(f"Error in handler: {e}")

client = TelegramClient(StringSession(config['session_string']), config['api_id'], config['api_hash'])
register_handlers(client, None)

shutdown_event = asyncio.Event()
loop = None

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully")
    if loop and loop.is_running():
        loop.call_soon_threadsafe(shutdown_event.set)
    else:
        shutdown_event.set()

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

import tempfile
import os

async def main():
    global loop
    loop = asyncio.get_running_loop()
    
    retry_delay = 5
    max_retry_delay = 300
    running_tasks = set()
    
    def track_task(task):
        running_tasks.add(task)
        task.add_done_callback(running_tasks.discard)
    
    while not shutdown_event.is_set():
        try:
            await client.start()
            logger.info("Client started successfully")
            retry_delay = 5  # Reset retry delay on successful connection
            
            # Save session string on first successful login - ATOMIC WRITE
            if not config.get('session_string') or config['session_string'] != client.session.save():
                config['session_string'] = client.session.save()
                # Atomic write pattern to prevent file corruption on termination
                with tempfile.NamedTemporaryFile('w', dir='.', delete=False, encoding='utf-8') as f:
                    json.dump(config, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                # Small forced delay to ensure disk cache is committed
                await asyncio.sleep(0.05)
                os.replace(f.name, 'config.json')
                # Ensure directory entry is persisted
                dir_fd = os.open('.', os.O_RDONLY)
                os.fsync(dir_fd)
                os.close(dir_fd)
                logger.info("Session string saved to config.json")
            
            while not shutdown_event.is_set():
                try:
                    await asyncio.wait_for(client.run_until_disconnected(), timeout=30)
                except asyncio.TimeoutError:
                    continue
                break
                    
        except Exception as e:
            logger.error(f"Error in main: {e}, reconnecting in {retry_delay}s")
            
            # Cleanup old client properly
            for task in running_tasks:
                if not task.done():
                    task.cancel()
            # Allow event loop one tick to process cancellation requests
            await asyncio.sleep(0)
            # Wait for all cancelled tasks to actually complete
            await asyncio.gather(*running_tasks, return_exceptions=True)
            running_tasks.clear()
            try:
                await client.disconnect()
            except:
                pass
                
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)
            
            # Recreate client on connection failure to avoid session corruption
            client = TelegramClient(StringSession(config['session_string']), config['api_id'], config['api_hash'])
            # Clear all existing handlers before registering new ones
            client._event_builders.clear()
            register_handlers(client, track_task)
    
    logger.info("Shutting down client")
    await client.disconnect()
    logger.info("Client disconnected successfully")

if __name__ == "__main__":
    asyncio.run(main())
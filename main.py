import asyncio
import base64
import gc
import ipaddress
import json
import random
import re
import signal
import struct
import sys
import tempfile
import os
from loguru import logger
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from telethon.crypto import AuthKey

# Initialize random seed for unpredictable responses
random.seed()

# Configure logging based on debug setting
logger.remove()
log_level = "DEBUG" if config.get('debug', True) else "INFO"
logger.add(sys.stderr, level=log_level, colorize=True, enqueue=True)

try:
    with open('config.json', 'r') as f:
        config = json.load(f)
except Exception as e:
    logger.critical(f"Failed to load config.json: {e}")
    sys.exit(1)

# Validate all required config keys exist
required_keys = ['session_string', 'api_id', 'api_hash', 'bot_username', 'triggers', 'responses']
for key in required_keys:
    if key not in config:
        logger.critical(f"Missing required config key: {key}")
        sys.exit(1)

if not config['session_string']:
    print("Session string is empty. Run this script locally to generate the session string.")
    sys.exit(1)

# Precompile all trigger patterns once at startup (without re.DOTALL to prevent ReDoS)
# Make triggers immutable after compilation to prevent race conditions
immutable_triggers = {}
for trigger_name, trigger in config['triggers'].items():
    # Log pattern for debugging
    logger.info(f"Compiling trigger {trigger_name}: {repr(trigger['pattern'])}")
    
    # Match pattern anywhere in the text, ignoring markdown formatting and extra text
    # This will match even if there are __ or ** around the pattern
    base_pattern = trigger['pattern'].replace('\n', ' ')
    # Allow any markdown formatting around the pattern
    pattern = re.compile(r'(?:__|\*\*)?\s*' + re.escape(base_pattern) + r'\s*(?:__|\*\*)?', re.IGNORECASE)
    
    logger.info(f"Compiled regex: {pattern.pattern}")
    
    immutable_triggers[trigger_name] = {
        'pattern': trigger['pattern'],
        'action': trigger['action'],
        'command': trigger.get('command'),
        '_compiled_pattern': pattern
    }
config['triggers'] = immutable_triggers

# Define send_with_backoff once globally to avoid closure leaks
async def send_with_backoff(client, action, max_retries=3):
    for attempt in range(max_retries):
        try:
            result = await action()
            # Always return boolean, regardless of what action returns
            return bool(result) is not False
        except FloodWaitError as e:
            logger.warning(f"Flood wait (attempt {attempt+1}): waiting {e.seconds}s")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"Error in action (attempt {attempt+1}): {e}")
            if attempt == max_retries - 1:
                return False
            await asyncio.sleep(1)
    logger.error(f"Failed after {max_retries} attempts")
    return False

def register_handlers(client, track_task=None):
    @client.on(events.NewMessage(incoming=True, outgoing=False))
    @client.on(events.MessageEdited(incoming=True, outgoing=False))
    async def handler(event):
        try:
            # Only process and log messages from target bot
            sender = await event.get_sender()
            target_username = config['bot_username'].lstrip('@')
            
            # Skip if not from target bot
            if not sender or not sender.username or sender.username.lower() != target_username.lower():
                return
                
            # Debug: Log ONLY messages from target bot
            chat = await event.get_chat()
            logger.debug(f"RAW MESSAGE FROM @{target_username}:")
            logger.debug(f"  Chat ID: {event.chat_id}")
            logger.debug(f"  Raw text: {repr(event.message.text)}")
            logger.debug(f"  Message ID: {event.message.id}")
            
            text = event.message.text
        except AttributeError:
            # Handle messages with no text (media only) - catch HERE where the error actually occurs
            logger.debug("Message has no text attribute")
            return
        
        # Explicitly handle empty text / None to prevent regex TypeError
        if not text:
            logger.debug("Message text is empty or None")
            return
            
        try:
            for trigger_name, trigger in config['triggers'].items():
                logger.debug(f"Checking trigger {trigger_name} with pattern: {repr(trigger['pattern'])}")
                logger.debug(f"Pattern regex: {trigger['_compiled_pattern'].pattern}")
                
                if trigger['_compiled_pattern'].search(text):
                    logger.debug(f"✅ Trigger {trigger_name} MATCHED!")
                    
                    if trigger['action'] == 'send_command':
                        task = asyncio.create_task(send_with_backoff(
                            client,
                            # Bind values immediately with default parameter to avoid closure late binding
                            lambda cmd=trigger['command']: client.send_message(config['bot_username'], cmd)
                        ))
                        if track_task:
                            track_task(task)
                        logger.info("Sent {cmd} for trigger {name}", cmd=trigger['command'], name=trigger_name)
                    elif trigger['action'] == 'random_response':
                        if not config['responses']:
                            logger.warning(f"No responses configured for trigger {trigger_name}")
                            break
                        response = random.choice(config['responses'])
                        delay_min = config.get('delay_min', 1)
                        delay_max = config.get('delay_max', 5)
                        # Ensure min is never greater than max to prevent ValueError
                        delay = random.uniform(min(delay_min, delay_max), max(delay_min, delay_max))
                        await asyncio.sleep(delay)
                        task = asyncio.create_task(send_with_backoff(
                            client,
                            # Bind values immediately with default parameter to avoid closure late binding
                            lambda evt=event, resp=response: evt.reply(resp)
                        ))
                        if track_task:
                            track_task(task)
                        logger.info("Replied with {resp} after {delay:.2f}s delay for trigger {name}", resp=response, delay=delay, name=trigger_name)
                    break
                else:
                    logger.debug(f"❌ Trigger {trigger_name} did NOT match")
        except asyncio.CancelledError:
            # Task was cancelled intentionally during shutdown/reconnect
            raise
        except Exception as e:
            logger.error(f"Error in handler: {e}")

# Compatibility fix for old Telethon session string format
def fix_old_session_string(session_string):
    """Fix old session strings that contain plain text IP instead of packed bytes"""
    try:
        # Try normal loading first
        return StringSession(session_string)
    except struct.error:
        # Old format detected - convert it
        if len(session_string) == 369 and session_string[0] == '1':
            # Decode the old format
            data = base64.urlsafe_b64decode(session_string[1:] + '=' * (-len(session_string[1:]) % 4))
            
            # Old format: [dc_id][ip_str][port][auth_key]
            # Find where port starts (first 2 bytes after null terminator)
            null_pos = data.index(b'\x00')
            dc_id = data[0]
            ip_str = data[1:null_pos].decode('ascii')
            port_pos = null_pos + 1
            port = struct.unpack('>H', data[port_pos:port_pos+2])[0]
            auth_key = data[port_pos+2:port_pos+2+256]
            
            # Create new properly formatted session
            session = StringSession()
            session.set_dc(dc_id, ip_str, port)
            session._auth_key = AuthKey(auth_key)
            return session
        else:
            # Not an old format session, let original exception propagate
            pass

client = TelegramClient(fix_old_session_string(config['session_string']), config['api_id'], config['api_hash'])
register_handlers(client, None)

shutdown_event = asyncio.Event()
loop = None

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully")
    if loop and loop.is_running():
        # asyncio.Event.set() is NOT thread-safe - must call from loop thread
        loop.call_soon_threadsafe(lambda: shutdown_event.set())
    else:
        shutdown_event.set()

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

async def main():
    global loop, client
    loop = asyncio.get_running_loop()
    
    retry_delay = 5
    max_retry_delay = 300
    running_tasks = set()
    
    def track_task(task):
        running_tasks.add(task)
        # Use lambda to safely discard - prevents race condition if task completes before callback is added
        task.add_done_callback(lambda t: running_tasks.discard(t))
    
    while not shutdown_event.is_set():
        had_error = False
        try:
            await client.start()
            logger.info("Client started successfully")
            retry_delay = 5  # Reset retry delay on successful connection
            
            # Save session string on first successful login - ATOMIC WRITE
            current_session = client.session.save()
            if not config.get('session_string') or config['session_string'] != current_session:
                config['session_string'] = current_session
                temp_file = None
                try:
                    # Atomic write pattern to prevent file corruption on termination
                    with tempfile.NamedTemporaryFile('w', dir='.', delete=False, encoding='utf-8') as f:
                        temp_file = f.name
                        json.dump(config, f, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                    # Small forced delay to ensure disk cache is committed
                    await asyncio.sleep(0.05)
                    os.replace(temp_file, 'config.json')
                    # Ensure directory entry is persisted
                    dir_fd = None
                    try:
                        dir_fd = os.open('.', os.O_RDONLY)
                        os.fsync(dir_fd)
                    finally:
                        if dir_fd is not None:
                            os.close(dir_fd)
                    logger.info("Session string saved to config.json")
                except Exception as e:
                    # Clean up temp file on any failure
                    if temp_file:
                        try:
                            if os.path.exists(temp_file):
                                os.unlink(temp_file)
                        except:
                            pass
                    logger.error(f"Failed to save session string: {e}")
            
            # Proper keepalive pattern that doesn't loop infinitely
            while not shutdown_event.is_set():
                try:
                    # Wait with timeout to allow checking shutdown_event periodically
                    await asyncio.wait_for(client.run_until_disconnected(), timeout=30)
                    # If we reach here, client actually disconnected - exit loop
                    break
                except asyncio.TimeoutError:
                    # Check shutdown event before continuing
                    if shutdown_event.is_set():
                        break
                    # Keepalive ping successful - continue running
                    continue
                    
        except Exception as e:
            logger.error(f"Error in main: {e}, reconnecting in {retry_delay}s")
            had_error = True
        else:
            had_error = False
        finally:
            # ALWAYS cleanup, regardless of whether there was an error or normal disconnect
            # Cleanup old client properly
            # Convert to list first to avoid concurrent modification while iterating
            for task in list(running_tasks):
                if not task.done():
                    task.cancel()
            # Allow event loop one tick to process cancellation requests
            await asyncio.sleep(0)
            # Wait for all cancelled tasks to actually complete
            await asyncio.gather(*running_tasks, return_exceptions=True)
            running_tasks.clear()
            # Force garbage collection to prevent resource leaks
            gc.collect()
            
            try:
                await asyncio.wait_for(client.disconnect(), timeout=5)
            except Exception:
                pass
                
            # ONLY reconnect and increment retry delay if we are NOT shutting down
            if not shutdown_event.is_set():
                await asyncio.sleep(retry_delay)
                
                # ONLY increment retry delay if there was an actual error
                if had_error:
                    retry_delay = min(max(retry_delay * 2, 5), max_retry_delay)
                
                # Recreate client on connection failure to avoid session corruption
                new_client = TelegramClient(fix_old_session_string(config['session_string']), config['api_id'], config['api_hash'])
                # Register handlers BEFORE assigning to global to prevent race condition
                register_handlers(new_client, track_task)
                # Atomic assignment - client is only visible globally
                client = new_client
    
    logger.info("Shutting down client")
    
    # Cancel all remaining running tasks before shutdown
    # Convert to list first to avoid concurrent modification while iterating
    for task in list(running_tasks):
        if not task.done():
            task.cancel()
    await asyncio.gather(*running_tasks, return_exceptions=True)
    running_tasks.clear()
    
    await client.disconnect()
    logger.info("Client disconnected successfully")

if __name__ == "__main__":
    asyncio.run(main())
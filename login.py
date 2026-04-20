#!/usr/bin/env python3
"""
Simple script to generate a new Telethon session string
"""
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
import json

async def main():
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    print("Generating new session string...")
    print(f"API ID: {config['api_id']}")
    print(f"Phone: {config['phone']}")
    
    async with TelegramClient(StringSession(), config['api_id'], config['api_hash']) as client:
        if not await client.is_user_authorized():
            await client.send_code_request(config['phone'])
            code = input("Enter the code you received: ")
            await client.sign_in(config['phone'], code)
            
            # If you have 2FA enabled
            try:
                await client.sign_in(password=input("Enter 2FA password (if any): "))
            except:
                pass
        
        session_string = client.session.save()
        print(f"\nYour new session string: {session_string}")
        print("\nCopy this into your config.json file!")

if __name__ == '__main__':
    asyncio.run(main())
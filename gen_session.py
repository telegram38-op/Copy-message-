"""Generate a Telethon string session locally (safer than 3rd-party bots)."""
import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("API_ID: "))
api_hash = input("API_HASH: ").strip()
with TelegramClient(StringSession(), api_id, api_hash) as c:
    print("\nSESSION STRING (keep secret!):\n")
    print(c.session.save())

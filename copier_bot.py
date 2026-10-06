"""
Telegram Auto-Copier (Telethon)

pip install telethon

ENV:
  API_ID, API_HASH      -> https://my.telegram.org
  BOT_TOKEN             -> @BotFather
  SESSION_STRINGS       -> comma separated Telethon StringSessions
  OWNER_IDS             -> comma separated Telegram user ids allowed to control bot

Flow:
  1. Userbot sessions listen on SOURCES and copy to DEFAULT destination.
  2. Owner DMs the bot any message -> bot asks which destination
     (buttons) -> message is copied there via the first session.
"""
import os, json, asyncio, tempfile
from telethon import TelegramClient, events, Button, utils
from telethon.sessions import StringSession

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
SESSIONS = [s.strip() for s in os.environ["SESSION_STRINGS"].split(",") if s.strip()]
OWNERS = {int(x) for x in os.environ["OWNER_IDS"].split(",") if x.strip()}
DB = "config.json"

# ---------- storage ----------
def load():
    if os.path.exists(DB):
        return json.load(open(DB))
    return {"sources": {}, "dests": {}, "default": None, "enabled": True, "running": True}

def save():
    json.dump(cfg, open(DB, "w"), indent=2)

cfg = load()  # sources/dests: {str(chat_id): title}

bot = TelegramClient("bot", API_ID, API_HASH)
users = []          # connected user clients
pending = {}        # key -> message waiting for destination choice
seen = set()        # dedupe when multiple sessions see same msg


def owner_only(f):
    async def w(e):
        if e.sender_id in OWNERS:
            return await f(e)
    return w


async def resolve(arg):
    """arg: @username / link / numeric id -> (chat_id, title)"""
    ent = await users[0].get_entity(int(arg) if arg.lstrip("-").isdigit() else arg)
    return utils.get_peer_id(ent), utils.get_display_name(ent)


# ---------- bot commands ----------
HELP = (
    "Commands:\n"
    "/addsource <id|@user|link>\n/removesource <id>\n/sources\n"
    "/adddest <id|@user|link>\n/removedest <id>\n/dests\n/setdest <id>\n"
    "/enable /disable\n/startcopier /stopcopier\n/test\n\n"
    "Mujhe koi bhi message DM karo -> main puchunga kahan bhejna hai."
)

@bot.on(events.NewMessage(pattern=r"/(start|help)$", func=lambda e: e.is_private))
@owner_only
async def _help(e):
    await e.reply(HELP)

async def _add(e, key):
    arg = e.pattern_match.group(1).strip()
    try:
        cid, title = await resolve(arg)
    except Exception as ex:
        return await e.reply(f"❌ Resolve nahi hua: {ex}")
    cfg[key][str(cid)] = title
    if key == "dests" and not cfg["default"]:
        cfg["default"] = str(cid)
    save()
    await e.reply(f"✅ Added: {title} ({cid})")

@bot.on(events.NewMessage(pattern=r"/addsource (.+)"))
@owner_only
async def _(e): await _add(e, "sources")

@bot.on(events.NewMessage(pattern=r"/adddest (.+)"))
@owner_only
async def _(e): await _add(e, "dests")

async def _remove(e, key):
    cid = e.pattern_match.group(1).strip()
    if cfg[key].pop(cid, None) is None:
        return await e.reply("Nahi mila.")
    if cfg["default"] == cid:
        cfg["default"] = None
    save()
    await e.reply("🗑 Removed.")

@bot.on(events.NewMessage(pattern=r"/removesource (.+)"))
@owner_only
async def _(e): await _remove(e, "sources")

@bot.on(events.NewMessage(pattern=r"/removedest (.+)"))
@owner_only
async def _(e): await _remove(e, "dests")

@bot.on(events.NewMessage(pattern=r"/sources$"))
@owner_only
async def _(e):
    await e.reply("\n".join(f"{t} — {i}" for i, t in cfg["sources"].items()) or "Koi source nahi.")

@bot.on(events.NewMessage(pattern=r"/dests$"))
@owner_only
async def _(e):
    lines = [f"{'⭐ ' if i == cfg['default'] else ''}{t} — {i}" for i, t in cfg["dests"].items()]
    await e.reply("\n".join(lines) or "Koi destination nahi.")

@bot.on(events.NewMessage(pattern=r"/setdest (.+)"))
@owner_only
async def _(e):
    cid = e.pattern_match.group(1).strip()
    if cid not in cfg["dests"]:
        return await e.reply("Pehle /adddest karo.")
    cfg["default"] = cid; save()
    await e.reply(f"⭐ Default: {cfg['dests'][cid]}")

def _toggle(key, val, msg):
    async def h(e):
        cfg[key] = val; save(); await e.reply(msg)
    return h

for pat, key, val, msg in [
    ("/enable$", "enabled", True, "✅ Copier enabled"),
    ("/disable$", "enabled", False, "⏸ Copier disabled"),
    ("/startcopier$", "running", True, "▶️ Started"),
    ("/stopcopier$", "running", False, "⏹ Stopped"),
]:
    bot.add_event_handler(owner_only(_toggle(key, val, msg)), events.NewMessage(pattern=pat))

@bot.on(events.NewMessage(pattern=r"/test$"))
@owner_only
async def _(e):
    out = []
    for i, c in enumerate(users, 1):
        try:
            me = await c.get_me()
            out.append(f"✅ Session {i}: {utils.get_display_name(me)}")
        except Exception as ex:
            out.append(f"❌ Session {i}: {ex}")
    out.append(f"Enabled={cfg['enabled']} Running={cfg['running']}")
    await e.reply("\n".join(out))


# ---------- DM -> ask destination ----------
@bot.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
@owner_only
async def dm_handler(e):
    if (e.raw_text or "").startswith("/"):
        return
    if not cfg["dests"]:
        return await e.reply("Pehle /adddest se destination add karo.")
    key = f"{e.chat_id}:{e.id}"
    pending[key] = e.message
    buttons = [[Button.inline(t, f"d|{key}|{cid}".encode())] for cid, t in cfg["dests"].items()]
    buttons.append([Button.inline("❌ Cancel", f"x|{key}".encode())])
    await e.reply("Kis channel/group par bhejna hai?", buttons=buttons)

@bot.on(events.CallbackQuery)
async def choose(e):
    if e.sender_id not in OWNERS:
        return await e.answer("Not allowed", alert=True)
    parts = e.data.decode().split("|")
    key = parts[1]
    msg = pending.pop(key, None)
    if parts[0] == "x" or msg is None:
        return await e.edit("Cancelled." if parts[0] == "x" else "Message expire ho gaya, dobara bhejo.")
    dest = int(parts[2])
    await e.edit("⏳ Bhej raha hu...")
    try:
        await send_via_user(msg, dest)
        await e.edit(f"✅ Bhej diya: {cfg['dests'].get(str(dest), dest)}")
    except Exception as ex:
        await e.edit(f"❌ Failed: {ex}")

async def send_via_user(msg, dest):
    """Message came from bot's DM -> download with bot, upload with user session."""
    c = users[0]
    if msg.media and not getattr(msg.media, "webpage", None):
        with tempfile.TemporaryDirectory() as d:
            path = await msg.download_media(file=d + "/")
            await c.send_file(dest, path, caption=msg.text or "",
                              formatting_entities=msg.entities,
                              voice_note=bool(msg.voice))
    else:
        await c.send_message(dest, msg.text or "", formatting_entities=msg.entities)


# ---------- auto copier (userbot side) ----------
async def on_source(e):
    if not (cfg["enabled"] and cfg["running"] and cfg["default"]):
        return
    if str(e.chat_id) not in cfg["sources"]:
        return
    k = (e.chat_id, e.id)
    if k in seen:
        return
    seen.add(k)
    if len(seen) > 5000:
        seen.clear()
    m, dest = e.message, int(cfg["default"])
    try:
        if m.media and not getattr(m.media, "webpage", None):
            await e.client.send_file(dest, m.media, caption=m.text or "",
                                     formatting_entities=m.entities,
                                     voice_note=bool(m.voice))
        elif m.text:
            await e.client.send_message(dest, m.text, formatting_entities=m.entities)
    except Exception as ex:
        print("copy error:", ex)
        # protected/restricted source -> fallback: download + re-upload
        try:
            with tempfile.TemporaryDirectory() as d:
                p = await m.download_media(file=d + "/")
                if p:
                    await e.client.send_file(dest, p, caption=m.text or "")
        except Exception as ex2:
            print("fallback error:", ex2)


async def main():
    await bot.start(bot_token=BOT_TOKEN)
    for i, s in enumerate(SESSIONS, 1):
        c = TelegramClient(StringSession(s), API_ID, API_HASH)
        try:
            await c.connect()
            if not await c.is_user_authorized():
                raise RuntimeError("session invalid/expired")
            c.add_event_handler(on_source, events.NewMessage())
            users.append(c)
            print(f"session {i} OK")
        except Exception as ex:
            print(f"❌ session {i} error: {ex}")
    if not users:
        raise SystemExit("Koi valid session nahi mila.")
    print("Running...")
    await asyncio.gather(bot.run_until_disconnected(),
                         *[c.run_until_disconnected() for c in users])

if __name__ == "__main__":
    asyncio.run(main())

"""
📦 **Module Name:** Lookup History
📝 **Commands:** 
  - .lookup <username / id>
"""

import asyncio
from telethon import events
from userbot.utils import register


@register(outgoing=True, pattern=r"^\.lookup(?: |$)(.*)")
async def user_lookup(event):
  query = event.pattern_match.group(1).strip()

  if not query:
    await event.edit(
        "❌ **Usage Error:** Please provide a username or ID!\nExample:"
        " `.lookup @username`"
    )
    return

  await event.edit(
      f"🔍 **Searching data for:** `{query}`\n⏳ Please wait, checking 1st"
      " bot..."
  )
  result_text = None
  client = event.client

  try:
    # --- STEP 1: Try 1st Bot (@SangMata_BOT) ---
    bot_1 = "@SangMata_BOT"
    await client.send_message(bot_1, f"/allhistory {query}")
    await asyncio.sleep(4)  # Bot response ke liye wait time

    async for response in client.iter_messages(bot_1, limit=1):
      if response.text and "not found" not in response.text.lower():
        result_text = (
            f"🟢 **Result Found via SangMata (`{bot_1}`):**\n\n{response.text}"
        )
        break

    # --- STEP 2: If 1st bot fails, Try 2nd Bot (@InvestigatorRobot) ---
    if not result_text:
      bot_2 = "@InvestigatorRobot"
      await event.edit(
          f"🔄 SangMata gave no result.\n⏳ Trying 2nd bot (`{bot_2}`)..."
      )

      await client.send_message(bot_2, f"/checkinfo {query}")
      await asyncio.sleep(4)

      async for response in client.iter_messages(bot_2, limit=1):
        if response.text and "not found" not in response.text.lower():
          result_text = (
              f"🔵 **Result Found via Investigator (`{bot_2}`):**\n\n{response.text}"
          )
          break

    # --- Final Output Send Karna ---
    if result_text:
      await event.edit(result_text)
    else:
      await event.edit(
          f"❌ **Sorry!** No history or details found for `{query}` on both"
          " bots."
      )

  except Exception as e:
    await event.edit(f"⚠️ **An error occurred during lookup:** `{str(e)}`")

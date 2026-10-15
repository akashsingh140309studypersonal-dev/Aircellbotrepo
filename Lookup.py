"""
📦 **Module Name:** Lookup History
📝 **Commands:** 
  - .lookup <username / id>
  - .lookuphelp (To see plugin details)
"""

import asyncio
from pyrogram import Client, filters


@Client.on_message(filters.command("lookuphelp", prefixes=".") & filters.me)
async def lookup_help(client, message):
  help_text = (
      "🔍 **User Lookup Plugin Help**\n\n"
      "Ye plugin do popular lookup bots ka use karke kisi bhi user ki purani history"
      " aur name changes nikalta hai.\n\n"
      "**Flow:**\n"
      "1. Pehle ye `@SangMata_BOT` par `/allhistory` try karega.\n"
      "2. Agar wahan data nahi mila, toh automatic `@InvestigatorRobot` par"
      " `/checkinfo` try karega.\n\n"
      "**Usage:**\n"
      "👉 `.lookup @username` ya `.lookup user_id`"
  )
  await message.edit(help_text)


@Client.on_message(filters.command("lookup", prefixes=".") & filters.me)
async def user_lookup(client, message):
  # Check karein ki query di gayi hai ya nahi
  if len(message.command) < 2:
    await message.edit(
        "❌ **Usage Error:** Please provide a username or ID!\n\n"
        "**Example:** `.lookup @username`\n"
        "**Tip:** Type `.lookuphelp` for more info."
    )
    return

  query = message.text.split(" ", 1)[1]
  await message.edit(
      f"🔍 **Searching data for:** `{query}`\n⏳ Please wait, checking 1st"
      " bot..."
  )

  result_text = None

  try:
    # --- STEP 1: Try 1st Bot (@SangMata_BOT) ---
    bot_1 = "@SangMata_BOT"
    await client.send_message(bot_1, f"/allhistory {query}")
    await asyncio.sleep(4)  # Bot response ke liye wait time

    # Bot 1 ka last message fetch karna
    async for response in client.get_chat_history(bot_1, limit=1):
      if response.text and "not found" not in response.text.lower():
        result_text = (
            f"🟢 **Result Found via SangMata (`{bot_1}`):**\n\n{response.text}"
        )
        break

    # --- STEP 2: If 1st bot fails, Try 2nd Bot (@InvestigatorRobot) ---
    if not result_text:
      bot_2 = "@InvestigatorRobot"
      await message.edit(
          f"🔄 SangMata gave no result.\n⏳ Trying 2nd bot (`{bot_2}`)..."
      )

      await client.send_message(bot_2, f"/checkinfo {query}")
      await asyncio.sleep(4)

      # Bot 2 ka last message fetch karna
      async for response in client.get_chat_history(bot_2, limit=1):
        if response.text and "not found" not in response.text.lower():
          result_text = (
              f"🔵 **Result Found via Investigator (`{bot_2}`):**\n\n{response.text}"
          )
          break

    # --- Final Output Send Karna ---
    if result_text:
      await message.edit(result_text)
    else:
      await message.edit(
          f"❌ **Sorry!** No history or details found for `{query}` on both"
          " bots."
      )

  except Exception as e:
    await message.edit(f"⚠️ **An error occurred during lookup:** `{str(e)}`")

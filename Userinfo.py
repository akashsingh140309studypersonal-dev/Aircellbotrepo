import asyncio
from pyrogram import Client, filters

# --- APNA CONFIGURATION YAHAN DALEIN ---
API_ID = 1234567  # Yahan apna Telegram API_ID dalein (Integer me)
API_HASH = "your_api_hash_here"  # Yahan apna Telegram API_HASH dalein (String me)

# Client initialization (Yahan 'app' properly define kiya gaya hai)
app = Client("my_userbot_session", api_id=API_ID, api_hash=API_HASH)


@app.on_message(filters.command("lookup", prefixes=".") & filters.me)
async def user_lookup(client, message):
  # Check karein ki username/ID di hai ya nahi
  if len(message.command) < 2:
    await message.edit(
        "❌ **Usage Error:** Please provide a username or ID!\nExample:"
        " `\\.lookup @username`"
    )
    return

  query = message.text.split(" ", 1)[1]
  await message.edit(f"🔍 Searching data for `{query}`...")

  result_text = None

  try:
    # --- STEP 1: Try 1st Bot (@SangMata_BOT) ---
    bot_1 = "@SangMata_BOT"
    await client.send_message(bot_1, f"/allhistory {query}")
    await asyncio.sleep(
        4
    )  # Bot ke response aane ke liye 4 seconds ka wait time

    # Bot 1 ka last message fetch karna
    async for response in client.get_chat_history(bot_1, limit=1):
      if response.text and "not found" not in response.text.lower():
        result_text = f"🟢 **Found via SangMata (@SangMata_BOT):**\n\n{response.text}"
        break

    # --- STEP 2: If 1st bot fails, Try 2nd Bot (@InvestigatorRobot) ---
    if not result_text:
      bot_2 = "@InvestigatorRobot"
      await message.edit(
          f"🔄 1st bot gave no result. Trying 2nd bot (`{bot_2}`)..."
      )

      await client.send_message(bot_2, f"/checkinfo {query}")
      await asyncio.sleep(4)

      async for response in client.get_chat_history(bot_2, limit=1):
        if response.text and "not found" not in response.text.lower():
          result_text = (
              f"🔵 **Found via Investigator (@InvestigatorRobot):**\n\n{response.text}"
          )
          break

    # --- Final Output Send Karna ---
    if result_text:
      await message.edit(result_text)
    else:
      await message.edit(
          f"❌ Sorry, no history or info found for `{query}` on both bots."
      )

  except Exception as e:
    await message.edit(f"⚠️ **An error occurred:** `{str(e)}`")


# Userbot ko start karne ke liye
print("Userbot is starting...")
app.run()

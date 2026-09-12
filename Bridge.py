import asyncio
import logging
import random
from telethon import events
from telethon.errors import (
    ChannelInvalidError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    ChatInviteRequiresApprovalError,
    FloodWaitError,
    InputUserDeactivatedError,
    PeerFloodError,
    UserBannedInChannelError,
    UserChannelsTooMuchError,
    UserIsBlockedError,
    UserKickedError,
    UserNotMutualContactError,
    UserPrivacyRestrictedError,
)
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.tl.functions.messages import AddChatUserRequest

from plugins.bot import add_handler
from utils.decorators import rishabh
from utils.utils import CipherElite

logger = logging.getLogger(__name__)

# Configurable Limits
MAX_INVITES = 200
SUCCESS_DELAY_SECONDS = 40
BATCH_SIZE = 20
BATCH_PAUSE_SECONDS = 300
MAX_AUTO_FLOOD_WAIT = 30

# Global state tracker for cancellation
RUNNING_TASKS = {}


def init(client_instance):
    commands = [
        ".bridge <source_chat> <dest_chat> - Safe auto transfer",
        ".stopbridge - Cancel active bridge transfer process",
    ]
    description = "👥 Ultra-Safe Inviter Bridge with Failure Detailed Logs & Stop Command"
    add_handler("bridge", commands, description)
    add_handler("stopbridge", commands, description)


@CipherElite.on(events.NewMessage(pattern=r"\.stopbridge"))
@rishabh()
async def stop_bridge_cmd(event):
    chat_id = event.chat_id
    if chat_id in RUNNING_TASKS:
        RUNNING_TASKS[chat_id] = False
        await event.reply("🛑 **Bridge process stop sequence triggered.** Agla step perform nahi hoga.")
    else:
        await event.reply("⚠️ Koi active `.bridge` task nahi chal raha hai.")


@CipherElite.on(events.NewMessage(pattern=r"\.bridge(?:\s+(\S+)\s+(\S+))?"))
@rishabh()
async def bridge_cmd(event):
    chat_id = event.chat_id
    RUNNING_TASKS[chat_id] = True
    status = None

    failure_reasons = {
        "Privacy Restricted": 0,
        "Admin Approval Needed": 0,
        "Channel/Group Limits Reached": 0,
        "Account Banned/Kicked/Deactivated": 0,
        "Not Mutual Contact": 0,
        "Admin Rights Required": 0,
        "Unknown Errors": 0,
    }

    try:
        source_input = event.pattern_match.group(1)
        dest_input = event.pattern_match.group(2)

        if not source_input or not dest_input:
            await event.reply(
                "❌ **Usage:** `.bridge <source_chat> <dest_chat>`\n"
                "**Example:** `.bridge @SourceGroup @DestChannel`"
            )
            RUNNING_TASKS.pop(chat_id, None)
            return

        status = await event.reply("🔍 Resolving source and destination chats...")

        # Safe Resolution
        try:
            source_entity = await event.client.get_entity(source_input)
            dest_entity = await event.client.get_entity(dest_input)
        except (ChannelInvalidError, ChannelPrivateError, ValueError):
            await status.edit("❌ **Resolve Error:** Access denied ya Username/Link invalid hai.")
            RUNNING_TASKS.pop(chat_id, None)
            return
        except Exception as e:
            await status.edit(f"❌ **Unexpected Entity Error:** {str(e)}")
            RUNNING_TASKS.pop(chat_id, None)
            return

        # Fetch members without strict force-join requirement (Using Message History scan fallback)
        await status.edit(f"📊 Scanning and extracting active members from `{source_input}`...")
        members = []
        members_set = set()

        try:
            async for user in event.client.iter_participants(source_entity):
                if user and not getattr(user, "deleted", False) and not getattr(user, "bot", False):
                    if user.id not in members_set:
                        members.append(user)
                        members_set.add(user.id)
        except Exception:
            await status.edit("⚠️ Participant list inaccessible. Scanning public chat history...")
            try:
                async for message in event.client.iter_messages(source_entity, limit=1500):
                    sender = await message.get_sender()
                    if sender and getattr(sender, "id", None):
                        # Filter Bots and Deleted Accounts strictly
                        is_bot = getattr(sender, "bot", False)
                        is_deleted = getattr(sender, "deleted", False)
                        if not is_bot and not is_deleted and sender.id not in members_set:
                            members.append(sender)
                            members_set.add(sender.id)
            except Exception as ex:
                await status.edit(f"❌ Failed to extract history: {str(ex)}")
                RUNNING_TASKS.pop(chat_id, None)
                return

        total = len(members)
        if total == 0:
            await status.edit("❌ Source chat me koi non-bot valid active members nahi mile.")
            RUNNING_TASKS.pop(chat_id, None)
            return

        await status.edit(
            f"👥 **Extracted Valid Humans:** {total}\n"
            f"🎯 **Target:** Max {MAX_INVITES} successful adds\n"
            f"⏱️ **Delay:** {SUCCESS_DELAY_SECONDS}s per user\n"
            f"🛑 **Control:** Send `.stopbridge` to interrupt anytime.\n\n"
            f"Transfer starting..."
        )

        invited = 0
        failed = 0

        for user in members:
            # Check for manual cancellation command
            if not RUNNING_TASKS.get(chat_id, True):
                await status.edit(f"🛑 **Process Stopped by User!** Total added: **{invited}**")
                break

            if invited >= MAX_INVITES:
                await status.edit(f"🎯 **Limit Reached!** Successfully added {MAX_INVITES} members.")
                break

            try:
                is_megagroup = getattr(dest_entity, "megagroup", False)
                is_broadcast = getattr(dest_entity, "broadcast", False)

                if is_megagroup or is_broadcast:
                    await event.client(
                        InviteToChannelRequest(channel=dest_entity, users=[user])
                    )
                else:
                    await event.client(
                        AddChatUserRequest(
                            chat_id=dest_entity.id,
                            user_id=user.id,
                            fwd_limit=1000000,
                        )
                    )
                
                # Operation succeeded without throwing error
                invited += 1

                await status.edit(
                    f"⏳ **Adding in progress...**\n"
                    f"✅ Added Successfully: **{invited}/{MAX_INVITES}**\n"
                    f"❌ Failed/Skipped: **{failed}**\n"
                    f"⏱️ Waiting {SUCCESS_DELAY_SECONDS}s for safe execution..."
                )

                if invited % BATCH_SIZE == 0 and invited < MAX_INVITES:
                    await status.edit(
                        f"☕ **Safety Break Active!** ({invited} added)\n"
                        f"Telegram Flood Protection Break: 5 Minutes (300s)..."
                    )
                    await asyncio.sleep(BATCH_PAUSE_SECONDS)
                else:
                    await asyncio.sleep(SUCCESS_DELAY_SECONDS)

            except FloodWaitError as e:
                if e.seconds <= MAX_AUTO_FLOOD_WAIT:
                    await status.edit(f"⚠️ Short FloodWait of {e.seconds}s. Waiting automatically...")
                    await asyncio.sleep(e.seconds + 2)
                    continue
                else:
                    await status.edit(
                        f"🛑 **Critical FloodWait Hit!** Telegram blocked actions for {e.seconds}s.\n"
                        f"Process stopped to protect account. Total Added: **{invited}**"
                    )
                    break

            except PeerFloodError:
                await status.edit(
                    f"🛑 **PeerFlood Limit Triggered!** Account hit Telegram mass-action limit.\n"
                    f"Process aborted. Total Added: **{invited}**"
                )
                break

            except ChatAdminRequiredError:
                failure_reasons["Admin Rights Required"] += 1
                await status.edit("❌ **Error:** Destination chat me `Add Users` permission missing hai.")
                break

            except ChatInviteRequiresApprovalError:
                failed += 1
                failure_reasons["Admin Approval Needed"] += 1

            except UserPrivacyRestrictedError:
                failed += 1
                failure_reasons["Privacy Restricted"] += 1

            except UserChannelsTooMuchError:
                failed += 1
                failure_reasons["Channel/Group Limits Reached"] += 1

            except UserNotMutualContactError:
                failed += 1
                failure_reasons["Not Mutual Contact"] += 1

            except (UserIsBlockedError, UserKickedError, UserBannedInChannelError, InputUserDeactivatedError):
                failed += 1
                failure_reasons["Account Banned/Kicked/Deactivated"] += 1

            except Exception as ex:
                logger.error(f"Error adding {getattr(user, 'id', 'Unknown')}: {ex}")
                failed += 1
                failure_reasons["Unknown Errors"] += 1

        # Detailed Breakdown Report Output
        reasons_summary = "\n".join([f"  • {k}: **{v}**" for k, v in failure_reasons.items() if v > 0])
        if not reasons_summary:
            reasons_summary = "  • None"

        await status.edit(
            f"✅ **Bridge Operation Finished!**\n\n"
            f"📍 Source: `{source_input}`\n"
            f"🏠 Destination: `{dest_input}`\n"
            f"📊 Extracted Non-Bot Humans: **{total}**\n"
            f"✅ Successfully Added: **{invited}**\n"
            f"❌ Failed / Skipped: **{failed}**\n\n"
            f"📋 **Detailed Failure Reasons:**\n{reasons_summary}"
        )

    except Exception as e:
        logger.exception("Global Error in Bridge Execution")
        if status:
            await status.edit(f"❌ Global Error: {str(e)}")
        else:
            await event.reply(f"❌ Global Error: {str(e)}")
    finally:
        RUNNING_TASKS.pop(chat_id, None)

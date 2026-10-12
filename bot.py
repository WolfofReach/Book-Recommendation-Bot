"""
Book club Discord bot.

/bookclub  - collect book recommendations, then randomly pick the next read
/checkin   - post a reading-progress tracker people can check in on for weeks
/bookprice - find where to buy a book

Session state is saved to a JSON file so buttons keep working after the bot
restarts. Set BOOKCLUB_DATA_FILE to change where that file lives.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
<<<<<<< HEAD
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus
=======
import aiohttp
import asyncio
from urllib.parse import quote_plus
import re
>>>>>>> efd712bb240f89f5747699690b42ce54344752f3

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown

<<<<<<< HEAD
log = logging.getLogger("bookclub")
=======
# Store active sessions per guild
active_sessions = {}
checkin_sessions = {}  # Store active checkin sessions by message_id: {message_id: CheckinSession}
>>>>>>> efd712bb240f89f5747699690b42ce54344752f3


# ===== Config =====

def _data_file_path() -> Path:
    """Where to save state: BOOKCLUB_DATA_FILE if set, else a Railway volume if attached, else next to this script."""
    if os.getenv("BOOKCLUB_DATA_FILE"):
        return Path(os.environ["BOOKCLUB_DATA_FILE"])
    # Railway sets this automatically when a volume is attached to the service.
    if os.getenv("RAILWAY_VOLUME_MOUNT_PATH"):
        return Path(os.environ["RAILWAY_VOLUME_MOUNT_PATH"]) / "bookclub_data.json"
    return Path(__file__).with_name("bookclub_data.json")


DATA_FILE = _data_file_path()
MAX_BOOKS_PER_USER = 2
EMBED_FIELD_LIMIT = 1024
EMBED_DESCRIPTION_LIMIT = 4096
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Only user mentions ping by default, so a book titled "@everyone" can't ping
# the whole server. Milestone announcements opt in to @everyone explicitly.
SAFE_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)
EVERYONE_MENTIONS = discord.AllowedMentions(everyone=True)


def clip(text: str, limit: int = EMBED_FIELD_LIMIT) -> str:
    """Trim text so it fits Discord's embed limits."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _int_keys(d: dict) -> dict:
    """JSON turns int keys into strings; turn them back."""
    return {int(k): v for k, v in d.items()}


# ===== Book selection session =====

@dataclass
class BookSession:
<<<<<<< HEAD
    """One round of recommendations. Each server has at most one open at a time."""

    starter_id: int
    expected_users: Optional[int] = None
    channel_id: Optional[int] = None
    message_id: Optional[int] = None
    recommendations: dict[int, list[str]] = field(default_factory=dict)  # user_id -> titles
    user_names: dict[int, str] = field(default_factory=dict)
    finished_users: set[int] = field(default_factory=set)  # passed, done, or hit the max
    is_closed: bool = False  # in-memory guard against double-closing; not saved

    def book_count(self, user_id: int) -> int:
        return len(self.recommendations.get(user_id, []))

    def name_of(self, user_id: int) -> str:
        return escape_markdown(self.user_names.get(user_id, f"User {user_id}"))

    def recommend_blocker(self, user_id: int) -> Optional[str]:
        """Why this user can't recommend right now, or None if they can."""
        if self.book_count(user_id) >= MAX_BOOKS_PER_USER:
            return f"You've already recommended {MAX_BOOKS_PER_USER} books (the max)!"
        if user_id in self.finished_users:
            return "You've already passed or marked yourself done this round!"
        return None

    def recommend(self, user_id: int, user_name: str, title: str) -> None:
        self.user_names[user_id] = user_name
        self.recommendations.setdefault(user_id, []).append(title)
        if self.book_count(user_id) >= MAX_BOOKS_PER_USER:
            self.finished_users.add(user_id)

    def finish(self, user_id: int, user_name: str) -> None:
        """Pass (with no books) or say you're done (after one book)."""
        self.user_names[user_id] = user_name
        self.finished_users.add(user_id)
=======
    def __init__(self, starter_id, expected_users=None):
        self.starter_id = starter_id
        self.expected_users = expected_users
        self.recommendations = {}  # {user_id: [book1, book2]}
        self.user_names = {}  # {user_id: display_name}
        self.passed_users = set()
        self.finished_users = set()  # Users who have completed their participation (2 books or passed)
        self.is_closed = False
    
    def add_recommendation(self, user_id, book_title, user_name):
        if user_id not in self.recommendations:
            self.recommendations[user_id] = []
        self.recommendations[user_id].append(book_title)
        self.user_names[user_id] = user_name
        
        # Mark as finished if they've hit 2 books
        if len(self.recommendations[user_id]) >= 2:
            self.finished_users.add(user_id)
    
    def user_book_count(self, user_id):
        return len(self.recommendations.get(user_id, []))
    
    def add_pass(self, user_id, user_name):
        self.passed_users.add(user_id)
        self.finished_users.add(user_id)  # Passing means they're finished
        self.user_names[user_id] = user_name
    
    def has_passed(self, user_id):
        return user_id in self.passed_users
    
    def get_all_books(self):
        all_books = []
        for books in self.recommendations.values():
            all_books.extend(books)
        return all_books
    
    def get_participant_count(self):
        return len(self.recommendations) + len(self.passed_users)
    
    def get_finished_count(self):
        """Count users who have finished participating (2 books or passed)"""
        return len(self.finished_users)
    
    def is_complete(self):
        if self.expected_users is not None:
            return self.get_finished_count() >= self.expected_users
        return False

class CheckinSession:
    def __init__(self, book_title, description, cover_url, expected_readers):
        self.book_title = book_title
        self.description = description
        self.cover_url = cover_url
        self.expected_readers = expected_readers
        self.readers_50 = {}  # {user_id: display_name}
        self.readers_100 = {}  # {user_id: display_name}
        self.pinged_50 = False
        self.pinged_100 = False
    
    def checkin_50(self, user_id, user_name):
        """Check in at 50% progress"""
        self.readers_50[user_id] = user_name
    
    def checkin_100(self, user_id, user_name):
        """Check in at 100% progress"""
        # Also add to 50% if not already there
        if user_id not in self.readers_50:
            self.readers_50[user_id] = user_name
        self.readers_100[user_id] = user_name
    
    def has_checked_50(self, user_id):
        return user_id in self.readers_50
    
    def has_checked_100(self, user_id):
        return user_id in self.readers_100
    
    def should_ping_50(self):
        """Check if we should ping for 50% milestone"""
        return len(self.readers_50) >= self.expected_readers and not self.pinged_50
    
    def should_ping_100(self):
        """Check if we should ping for 100% milestone"""
        return len(self.readers_100) >= self.expected_readers and not self.pinged_100

class RecommendButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="📚 Recommend a Book", style=discord.ButtonStyle.primary, custom_id="bookclub:recommend")
    
    async def callback(self, interaction: discord.Interaction):
        session = active_sessions.get(interaction.guild_id)
        
        if not session:
            await interaction.response.send_message(
                "⚠️ This session is no longer active (bot may have restarted). Please start a new session with `/bookclub`",
                ephemeral=True
            )
            return
        
        if session.is_closed:
            await interaction.response.send_message("This session is closed!", ephemeral=True)
            return
        
        if session.user_book_count(interaction.user.id) >= 2:
            await interaction.response.send_message("You've already recommended 2 books (max limit)!", ephemeral=True)
            return
        
        if session.has_passed(interaction.user.id):
            await interaction.response.send_message("You've already passed on recommending!", ephemeral=True)
            return
        
        # Send modal for book input
        await interaction.response.send_modal(BookModal(session))
>>>>>>> efd712bb240f89f5747699690b42ce54344752f3

    def passed_users(self) -> list[int]:
        return [uid for uid in self.finished_users if uid not in self.recommendations]

    def all_books(self) -> list[tuple[int, str]]:
        return [(uid, book) for uid, books in self.recommendations.items() for book in books]

    def participant_count(self) -> int:
        return len(self.recommendations.keys() | self.finished_users)

    def is_complete(self) -> bool:
        return self.expected_users is not None and len(self.finished_users) >= self.expected_users

    def to_dict(self) -> dict:
        return {
            "starter_id": self.starter_id,
            "expected_users": self.expected_users,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "recommendations": self.recommendations,
            "user_names": self.user_names,
            "finished_users": sorted(self.finished_users),
        }

    @classmethod
    def from_dict(cls, d: dict) -> BookSession:
        return cls(
            starter_id=d["starter_id"],
            expected_users=d.get("expected_users"),
            channel_id=d.get("channel_id"),
            message_id=d.get("message_id"),
            recommendations=_int_keys(d.get("recommendations", {})),
            user_names=_int_keys(d.get("user_names", {})),
            finished_users=set(d.get("finished_users", [])),
        )

<<<<<<< HEAD
=======
class Checkin50Button(discord.ui.Button):
    def __init__(self):
        super().__init__(label="📖 50% Progress", style=discord.ButtonStyle.primary, custom_id="checkin:50")
    
    async def callback(self, interaction: discord.Interaction):
        session = checkin_sessions.get(interaction.message.id)
        
        if not session:
            await interaction.response.send_message(
                "⚠️ This checkin session is no longer active.",
                ephemeral=True
            )
            return
        
        if session.has_checked_50(interaction.user.id):
            await interaction.response.send_message("You've already checked in at 50%!", ephemeral=True)
            return
        
        session.checkin_50(interaction.user.id, interaction.user.display_name)
        
        # Send ephemeral confirmation (no ping)
        await interaction.response.send_message("✅ You've checked in at 50%!", ephemeral=True)
        
        # Update the embed
        embed = create_checkin_embed(session)
        await interaction.message.edit(embed=embed)
        
        # Check if we should ping for 50% milestone
        if session.should_ping_50():
            session.pinged_50 = True
            await interaction.channel.send(f"🎉 @everyone - {session.expected_readers} readers have reached 50% progress!")

class Checkin100Button(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✅ Finished (100%)", style=discord.ButtonStyle.success, custom_id="checkin:100")
    
    async def callback(self, interaction: discord.Interaction):
        session = checkin_sessions.get(interaction.message.id)
        
        if not session:
            await interaction.response.send_message(
                "⚠️ This checkin session is no longer active.",
                ephemeral=True
            )
            return
        
        if session.has_checked_100(interaction.user.id):
            await interaction.response.send_message("You've already checked in at 100%!", ephemeral=True)
            return
        
        session.checkin_100(interaction.user.id, interaction.user.display_name)
        
        # Send ephemeral confirmation (no ping)
        await interaction.response.send_message("✅ You've finished the book! 🎊", ephemeral=True)
        
        # Update the embed
        embed = create_checkin_embed(session)
        await interaction.message.edit(embed=embed)
        
        # Check if we should ping for 100% milestone
        if session.should_ping_100():
            session.pinged_100 = True
            await interaction.channel.send(f"🎉 @everyone - {session.expected_readers} readers have finished the book!")

class CheckinView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(Checkin50Button())
        self.add_item(Checkin100Button())

class BookClubView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RecommendButton())
        self.add_item(PassButton())
        self.add_item(CloseButton())
>>>>>>> efd712bb240f89f5747699690b42ce54344752f3

# ===== Reading check-ins =====

<<<<<<< HEAD
@dataclass(frozen=True)
class Milestone:
    percent: int
    label: str
    style: discord.ButtonStyle
    empty_text: str
    confirmation: str
    announcement: str  # formatted with n=expected_readers

=======
async def update_checkin_message(interaction: discord.Interaction):
    """Update the checkin embed with current progress"""
    session = checkin_sessions.get(interaction.guild_id)
    if not session:
        return
    
    embed = create_checkin_embed(session)
    
    try:
        # Edit the original message
        await interaction.message.edit(embed=embed)
    except:
        pass

async def auto_close_session(interaction: discord.Interaction):
    """Automatically close session if expected users reached"""
    session = active_sessions.get(interaction.guild_id)
    if not session:
        return
    
    # Use followup instead of channel.send to avoid permission issues
    await interaction.followup.send(f"🎉 Expected number of participants reached! Closing session and picking winner...")
    await close_and_pick_winner(interaction, session, auto_close=True)

async def close_and_pick_winner(interaction: discord.Interaction, session, auto_close=False):
    """Close the session and randomly pick a book"""
    session.is_closed = True
    all_books = session.get_all_books()
    
    if not all_books:
        if auto_close:
            await interaction.followup.send("❌ No books were recommended! Session closed with no winner.")
        else:
            await interaction.response.send_message("❌ No books were recommended! Session closed with no winner.", ephemeral=False)
        del active_sessions[interaction.guild_id]
        return
    
    winner = random.choice(all_books)
    
    # Create results embed
    embed = discord.Embed(
        title="🎊 Book Club Selection Results!",
        description=f"## 🏆 Winner: **{winner}**",
        color=discord.Color.gold()
    )
    
    # List all recommendations
    rec_text = ""
    for user_id, books in session.recommendations.items():
        user_name = session.user_names.get(user_id, f"User {user_id}")
        for book in books:
            rec_text += f"• {book} (by {user_name})\n"
    
    if rec_text:
        embed.add_field(name="All Recommendations", value=rec_text, inline=False)
    
    embed.add_field(
        name="Stats",
        value=f"📚 Total books: {len(all_books)}\n👥 Participants: {session.get_participant_count()}",
        inline=False
    )
    
    # Send message based on how the function was called
    if auto_close:
        await interaction.followup.send(embed=embed)
    else:
        await interaction.response.send_message(embed=embed)
    
    # Clear the session
    del active_sessions[interaction.guild_id]
>>>>>>> efd712bb240f89f5747699690b42ce54344752f3

MILESTONES = (
    Milestone(
        50, "📖 50% Progress", discord.ButtonStyle.primary,
        "No one has checked in yet",
        "✅ You've checked in at 50%!",
        "{n} readers have reached 50% progress!",
    ),
    Milestone(
        100, "✅ Finished (100%)", discord.ButtonStyle.success,
        "No one has finished yet",
        "✅ You've finished the book! 🎊",
        "{n} readers have finished the book!",
    ),
)


@dataclass
class CheckinSession:
    """Progress tracker attached to one message. Stays open indefinitely."""

    book_title: str
    description: str
    cover_url: Optional[str]
    expected_readers: int
    readers: dict[int, dict[int, str]] = field(
        default_factory=lambda: {m.percent: {} for m in MILESTONES}
    )  # percent -> {user_id: display_name}
    announced: set[int] = field(default_factory=set)  # milestones already pinged

    def has_reached(self, percent: int, user_id: int) -> bool:
        return user_id in self.readers[percent]

    def check_in(self, percent: int, user_id: int, user_name: str) -> None:
        # Reaching a milestone also counts for every earlier one.
        for m in MILESTONES:
            if m.percent <= percent:
                self.readers[m.percent].setdefault(user_id, user_name)

    def pop_new_milestones(self) -> list[Milestone]:
        """Milestones that just hit the expected reader count and haven't been announced."""
        new = [
            m for m in MILESTONES
            if m.percent not in self.announced
            and len(self.readers[m.percent]) >= self.expected_readers
        ]
        self.announced.update(m.percent for m in new)
        return new

    def to_dict(self) -> dict:
        return {
            "book_title": self.book_title,
            "description": self.description,
            "cover_url": self.cover_url,
            "expected_readers": self.expected_readers,
            "readers": self.readers,
            "announced": sorted(self.announced),
        }

    @classmethod
    def from_dict(cls, d: dict) -> CheckinSession:
        readers = {m.percent: {} for m in MILESTONES}
        for percent, names in _int_keys(d.get("readers", {})).items():
            readers[percent] = _int_keys(names)
        return cls(
            book_title=d["book_title"],
            description=d.get("description", ""),
            cover_url=d.get("cover_url"),
            expected_readers=d["expected_readers"],
            readers=readers,
            announced=set(d.get("announced", [])),
        )


# ===== Persistence =====

class Store:
    """Keeps sessions in memory and mirrors them to a JSON file."""

    def __init__(self, path: Path):
        self.path = path
        self.book_sessions: dict[int, BookSession] = {}  # guild_id -> session
        self.checkins: dict[int, CheckinSession] = {}  # message_id -> session

    def load(self) -> None:
        if not self.path.exists():
            log.info("No data file at %s yet; starting fresh", self.path)
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.book_sessions = {
                int(gid): BookSession.from_dict(s) for gid, s in data.get("book_sessions", {}).items()
            }
            self.checkins = {
                int(mid): CheckinSession.from_dict(s) for mid, s in data.get("checkins", {}).items()
            }
            log.info(
                "Loaded %d book session(s) and %d check-in(s) from %s",
                len(self.book_sessions), len(self.checkins), self.path,
            )
        except (OSError, ValueError, KeyError, TypeError):
            log.exception("Couldn't read %s; starting with empty state", self.path)

    def save(self) -> None:
        data = {
            "book_sessions": {gid: s.to_dict() for gid, s in self.book_sessions.items()},
            "checkins": {mid: s.to_dict() for mid, s in self.checkins.items()},
        }
        # Write to a temp file then swap it in, so a crash mid-write can't corrupt the data.
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            log.exception("Couldn't save state to %s", self.path)


store = Store(DATA_FILE)


# ===== Embeds =====

def build_session_embed(session: BookSession) -> discord.Embed:
    embed = discord.Embed(
        title="📚 Book Club Selection Session",
        description="Recommend books for the next book club read!",
        color=discord.Color.blue(),
    )

    if session.recommendations:
        lines = []
        for uid, books in session.recommendations.items():
            done = " ✓" if uid in session.finished_users else ""
            lines.append(f"**{session.name_of(uid)}** ({len(books)}/{MAX_BOOKS_PER_USER}){done}")
            lines.extend(f"• {escape_markdown(book)}" for book in books)
        embed.add_field(name="Current Recommendations", value=clip("\n".join(lines)), inline=False)

    passed = session.passed_users()
    if passed:
        embed.add_field(
            name="Passed", value=clip("\n".join(session.name_of(uid) for uid in passed)), inline=False
        )

    if session.expected_users:
        embed.add_field(
            name="Progress",
<<<<<<< HEAD
            value=f"{len(session.finished_users)}/{session.expected_users} participants finished",
            inline=False,
=======
            value=f"{session.get_finished_count()}/{session.expected_users} participants finished",
            inline=False
>>>>>>> efd712bb240f89f5747699690b42ce54344752f3
        )

    embed.set_footer(
        text=f"📚 Max {MAX_BOOKS_PER_USER} books per person • Hit Pass / Done when you're finished "
             "• Session starter can close anytime"
    )
    return embed

<<<<<<< HEAD

def build_results_message(session: BookSession, note: Optional[str] = None) -> dict:
    """Content/embed kwargs announcing the winner (or that there wasn't one)."""
    books = session.all_books()
    if not books:
        text = "❌ No books were recommended! Session closed with no winner."
        return {"content": f"{note}\n{text}" if note else text}

    winner_uid, winner = random.choice(books)
    embed = discord.Embed(
        title="🎊 Book Club Selection Results!",
        description=f"## 🏆 Winner: **{escape_markdown(winner)}**\nRecommended by {session.name_of(winner_uid)}",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="All Recommendations",
        value=clip("\n".join(f"• {escape_markdown(book)} (by {session.name_of(uid)})" for uid, book in books)),
        inline=False,
    )
    embed.add_field(
        name="Stats",
        value=f"📚 Total books: {len(books)}\n👥 Participants: {session.participant_count()}",
        inline=False,
    )
    message = {"embed": embed}
    if note:
        message["content"] = note
    return message


def build_checkin_embed(session: CheckinSession) -> discord.Embed:
    embed = discord.Embed(
        title=clip(f"📚 {session.book_title}", 256),
        description=clip(session.description, EMBED_DESCRIPTION_LIMIT) or None,
        color=discord.Color.blue(),
    )
    if session.cover_url:
        embed.set_thumbnail(url=session.cover_url)

    for m in MILESTONES:
        readers = session.readers[m.percent]
        names = "\n".join(f"• {escape_markdown(name)}" for name in readers.values())
        embed.add_field(
            name=f"{m.label} ({len(readers)}/{session.expected_readers})",
            value=clip(names) if names else m.empty_text,
            inline=False,
        )

    embed.set_footer(text="Click the buttons below to check in your reading progress!")
    return embed


# ===== Book selection buttons =====
# custom_ids must not change: they're how buttons on existing messages find these handlers.

async def get_open_session(interaction: discord.Interaction) -> Optional[BookSession]:
    """The session this button/form belongs to, or None (after telling the user) if it's over."""
    session = store.book_sessions.get(interaction.guild_id)
    stale = (
        session is None
        or session.is_closed
        or (interaction.message is not None and session.message_id not in (None, interaction.message.id))
    )
    if stale:
        await interaction.response.send_message(
            "⚠️ This session has ended. Start a new one with `/bookclub`.", ephemeral=True
        )
        return None
    return session


def close_session(guild_id: int, session: BookSession) -> None:
    """Mark closed and forget it. Synchronous so two clicks can't both close it."""
    session.is_closed = True
    store.book_sessions.pop(guild_id, None)
    store.save()


async def publish_update(interaction: discord.Interaction, session: BookSession, announcement: str) -> None:
    """Refresh the session message, post the announcement, and auto-close if everyone's in."""
    closing = session.is_complete() and not session.is_closed
    if closing:
        close_session(interaction.guild_id, session)

    edit = {"embed": build_session_embed(session)}
    if closing:
        edit["view"] = BookClubView(disabled=True)
    await interaction.response.edit_message(**edit)
    await interaction.followup.send(announcement)

    if closing:
        await interaction.followup.send(
            **build_results_message(session, note="🎉 Everyone's in! Picking a winner...")
        )


class RecommendButton(discord.ui.Button):
    def __init__(self, disabled: bool = False):
        super().__init__(
            label="📚 Recommend a Book", style=discord.ButtonStyle.primary,
            custom_id="bookclub:recommend", disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        session = await get_open_session(interaction)
        if session is None:
            return
        problem = session.recommend_blocker(interaction.user.id)
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return
        await interaction.response.send_modal(BookModal())


class BookModal(discord.ui.Modal, title="Recommend a Book"):
    book_title = discord.ui.TextInput(
        label="Book Title", placeholder="Enter the book title...", required=True, max_length=200
    )

    async def on_submit(self, interaction: discord.Interaction):
        session = await get_open_session(interaction)
        if session is None:
            return
        # Check again: someone could have had two forms open at once.
        problem = session.recommend_blocker(interaction.user.id)
        book = self.book_title.value.strip()
        if problem or not book:
            await interaction.response.send_message(problem or "The title can't be blank!", ephemeral=True)
            return

        session.recommend(interaction.user.id, interaction.user.display_name, book)
        store.save()
        count = session.book_count(interaction.user.id)
        await publish_update(
            interaction, session,
            f"✅ {interaction.user.mention} recommended: **{escape_markdown(book)}** "
            f"({count}/{MAX_BOOKS_PER_USER} books)",
        )


class PassButton(discord.ui.Button):
    def __init__(self, disabled: bool = False):
        super().__init__(
            label="⏭️ Pass / Done", style=discord.ButtonStyle.secondary,
            custom_id="bookclub:pass", disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        session = await get_open_session(interaction)
        if session is None:
            return
        user = interaction.user
        if user.id in session.finished_users:
            await interaction.response.send_message("You're already done for this round!", ephemeral=True)
            return

        had_books = session.book_count(user.id) > 0
        session.finish(user.id, user.display_name)
        store.save()
        verb = "is done recommending" if had_books else "has passed on recommending"
        await publish_update(interaction, session, f"✅ {user.mention} {verb}.")


class CloseButton(discord.ui.Button):
    def __init__(self, disabled: bool = False):
        super().__init__(
            label="🎲 Close & Pick Winner", style=discord.ButtonStyle.success,
            custom_id="bookclub:close", disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        session = await get_open_session(interaction)
        if session is None:
            return
        if interaction.user.id != session.starter_id and not interaction.permissions.manage_guild:
            await interaction.response.send_message(
                "Only the person who started the session (or a server manager) can close it!", ephemeral=True
            )
            return

        close_session(interaction.guild_id, session)
        await interaction.response.edit_message(view=BookClubView(disabled=True))
        await interaction.followup.send(**build_results_message(session))


class BookClubView(discord.ui.View):
    def __init__(self, disabled: bool = False):
        super().__init__(timeout=None)
        self.add_item(RecommendButton(disabled))
        self.add_item(PassButton(disabled))
        self.add_item(CloseButton(disabled))


# ===== Check-in buttons =====

class CheckinButton(discord.ui.Button):
    def __init__(self, milestone: Milestone):
        super().__init__(
            label=milestone.label, style=milestone.style, custom_id=f"checkin:{milestone.percent}"
        )
        self.milestone = milestone

    async def callback(self, interaction: discord.Interaction):
        session = store.checkins.get(interaction.message.id)
        if session is None:
            await interaction.response.send_message(
                "⚠️ This check-in isn't being tracked anymore. Post a new one with `/checkin`.", ephemeral=True
            )
            return

        m, user = self.milestone, interaction.user
        if session.has_reached(m.percent, user.id):
            await interaction.response.send_message(f"You've already checked in at {m.percent}%!", ephemeral=True)
            return

        session.check_in(m.percent, user.id, user.display_name)
        to_announce = session.pop_new_milestones()
        store.save()

        await interaction.response.edit_message(embed=build_checkin_embed(session))
        await interaction.followup.send(m.confirmation, ephemeral=True)
        for reached in to_announce:
            await interaction.channel.send(
                f"🎉 @everyone - {reached.announcement.format(n=session.expected_readers)}",
                allowed_mentions=EVERYONE_MENTIONS,
            )


class CheckinView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for m in MILESTONES:
            self.add_item(CheckinButton(m))


# ===== Book lookups (Google Books + retailers) =====

@dataclass
class BookInfo:
    title: str
    authors: list[str]
    description: str
    thumbnail: Optional[str]

    def short_description(self, limit: int = 350) -> str:
        text = re.sub(r"<[^>]+>", "", self.description).strip()  # Google sometimes includes HTML
        if len(text) <= limit:
            return text
        return text[:limit].rsplit(" ", 1)[0] + "…"


@dataclass(frozen=True)
class Retailer:
    name: str
    search_url: str  # "{q}" is replaced with the URL-encoded search
    price_patterns: tuple[str, ...] = ()  # regexes with the price in group 1; empty = don't scrape

    def url_for(self, query: str) -> str:
        return self.search_url.format(q=quote_plus(query))

    async def scrape_price(self, query: str) -> Optional[float]:
        """Best effort: retailers often block bots or change their markup."""
        if not self.price_patterns:
            return None
        html = await fetch_text(self.url_for(query), headers=BROWSER_HEADERS)
        if html:
            for pattern in self.price_patterns:
                match = re.search(pattern, html)
                if match:
                    return float(match.group(1).replace(",", ""))
        return None


RETAILERS = (
    Retailer("Amazon", "https://www.amazon.com/s?k={q}&i=stripbooks",
             (r'class="a-offscreen">\$([\d,]+\.\d{2})<',)),
    Retailer("Bookshop.org", "https://bookshop.org/search?keywords={q}", (r"\$(\d+\.\d{2})",)),
    Retailer("Barnes & Noble", "https://www.barnesandnoble.com/s/{q}"),
    Retailer("ThriftBooks", "https://www.thriftbooks.com/browse/?b.search={q}"),
    Retailer("AbeBooks", "https://www.abebooks.com/servlet/SearchResults?kn={q}"),
)


async def fetch_text(url: str, headers: Optional[dict] = None) -> Optional[str]:
=======
def create_checkin_embed(session):
    """Create an embed showing current checkin status"""
    embed = discord.Embed(
        title=f"📚 {session.book_title}",
        description=session.description,
        color=discord.Color.blue()
    )
    
    # Set book cover as thumbnail
    if session.cover_url:
        embed.set_thumbnail(url=session.cover_url)
    
    # Show 50% progress
    if session.readers_50:
        readers_50_text = "\n".join([f"• {name}" for name in session.readers_50.values()])
        embed.add_field(
            name=f"📖 50% Progress ({len(session.readers_50)}/{session.expected_readers})",
            value=readers_50_text,
            inline=False
        )
    else:
        embed.add_field(
            name=f"📖 50% Progress (0/{session.expected_readers})",
            value="No one has checked in yet",
            inline=False
        )
    
    # Show 100% progress
    if session.readers_100:
        readers_100_text = "\n".join([f"• {name}" for name in session.readers_100.values()])
        embed.add_field(
            name=f"✅ Finished - 100% ({len(session.readers_100)}/{session.expected_readers})",
            value=readers_100_text,
            inline=False
        )
    else:
        embed.add_field(
            name=f"✅ Finished - 100% (0/{session.expected_readers})",
            value="No one has finished yet",
            inline=False
        )
    
    embed.set_footer(text="Click the buttons below to check in your reading progress!")
    
    return embed

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    
    # Register persistent views so buttons work after restarts
    bot.add_view(BookClubView())
    bot.add_view(CheckinView())
    
>>>>>>> efd712bb240f89f5747699690b42ce54344752f3
    try:
        async with bot.web.get(url, headers=headers) as response:
            if response.status != 200:
                log.info("GET %s returned %s", url, response.status)
                return None
            return await response.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("GET %s failed: %s", url, e)
        return None


async def fetch_book_info(title: str, author: Optional[str] = None) -> Optional[BookInfo]:
    query = f"{title} {author}" if author else title
    text = await fetch_text(f"https://www.googleapis.com/books/v1/volumes?q={quote_plus(query)}")
    if not text:
        return None
    try:
        volume = json.loads(text)["items"][0]["volumeInfo"]
    except (ValueError, KeyError, IndexError):
        return None

    thumbnail = volume.get("imageLinks", {}).get("thumbnail")
    if thumbnail:
        thumbnail = thumbnail.replace("http://", "https://", 1)
    return BookInfo(
        title=volume.get("title", title),
        authors=volume.get("authors", []),
        description=volume.get("description", ""),
        thumbnail=thumbnail,
    )


def build_price_embed(title: str, info: Optional[BookInfo], prices: list[tuple[Retailer, Optional[float], str]]) -> discord.Embed:
    embed = discord.Embed(
        title=f"💰 {info.title if info else title}",
        description="Here are links to find the best price:",
        color=discord.Color.green(),
    )
    if info and info.authors:
        embed.add_field(name="Author(s)", value=", ".join(info.authors), inline=False)
    if info and info.thumbnail:
        embed.set_thumbnail(url=info.thumbnail)

    # Retailers with a price first (cheapest first), then the rest in their usual order.
    ordered = sorted(prices, key=lambda p: (p[1] is None, p[1] or 0))
    lines = [
        f"{i}. [{retailer.name}]({url}) - " + (f"**${price:.2f}**" if price is not None else "Price not available")
        for i, (retailer, price, url) in enumerate(ordered, 1)
    ]
    embed.add_field(name="🔗 Check Prices", value=clip("\n".join(lines)), inline=False)

    cheapest = ordered[0] if ordered and ordered[0][1] is not None else None
    if cheapest:
        embed.add_field(
            name="💡 Tip", value=f"Lowest found price: **${cheapest[1]:.2f}** at {cheapest[0].name}", inline=False
        )
    else:
        embed.add_field(
            name="💡 Note",
            value="Couldn't fetch live prices. Click the links above to check current prices at each retailer.",
            inline=False,
        )
    embed.set_footer(text="Prices are approximate and may vary. Click links to see current prices.")
    return embed


# ===== Bot & slash commands =====

class BookClubBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default(), allowed_mentions=SAFE_MENTIONS)
        self.web: Optional[aiohttp.ClientSession] = None  # shared HTTP session for lookups

    async def setup_hook(self):
        # Runs once at startup (unlike on_ready, which fires again on every reconnect).
        store.load()
        self.web = aiohttp.ClientSession(timeout=HTTP_TIMEOUT)
        # Re-attach button handlers to messages posted before a restart.
        self.add_view(BookClubView())
        self.add_view(CheckinView())
        try:
            synced = await self.tree.sync()
            log.info("Synced %d command(s)", len(synced))
        except discord.HTTPException:
            log.exception("Failed to sync commands")

    async def on_ready(self):
        log.info("%s has connected to Discord!", self.user)

    async def close(self):
        if self.web:
            await self.web.close()
        await super().close()


bot = BookClubBot()


async def session_message_exists(session: BookSession) -> bool:
    """False if the session's message was deleted, so a lost session doesn't block new ones forever."""
    if not session.channel_id or not session.message_id:
        return False
    try:
        channel = bot.get_channel(session.channel_id) or await bot.fetch_channel(session.channel_id)
        await channel.fetch_message(session.message_id)
        return True
    except discord.NotFound:
        return False
    except discord.HTTPException:
        return True  # can't tell (e.g. missing permissions), so assume it's still there


@bot.tree.command(name="bookclub", description="Start a book club selection session")
@app_commands.guild_only()
@app_commands.describe(expected_participants="Number of expected participants (optional)")
async def bookclub(
    interaction: discord.Interaction,
    expected_participants: Optional[app_commands.Range[int, 1]] = None,
):
    existing = store.book_sessions.get(interaction.guild_id)
    if existing:
        if await session_message_exists(existing):
            link = f"https://discord.com/channels/{interaction.guild_id}/{existing.channel_id}/{existing.message_id}"
            await interaction.response.send_message(
                f"❌ There's already an active session in this server: {link}", ephemeral=True
            )
            return
        store.book_sessions.pop(interaction.guild_id, None)

<<<<<<< HEAD
    session = BookSession(starter_id=interaction.user.id, expected_users=expected_participants)
    await interaction.response.send_message(embed=build_session_embed(session), view=BookClubView())
    message = await interaction.original_response()
    session.channel_id, session.message_id = message.channel.id, message.id
    store.book_sessions[interaction.guild_id] = session
    store.save()

    if expected_participants:
        tip = f"Waiting for **{expected_participants}** participants to recommend or pass. It'll close automatically."
    else:
        tip = "Click 'Close & Pick Winner' when everyone has finished recommending."
    await interaction.followup.send(f"📢 Session started! {tip}", ephemeral=True)


@bot.tree.command(name="checkin", description="Post a reading progress tracker for the current book")
@app_commands.guild_only()
@app_commands.describe(
    book_title="Title of the current book",
    expected_readers="Number of expected readers",
    description="Brief description (optional, fetched from Google Books if left blank)",
    cover_url="URL to the cover image (optional, fetched from Google Books if left blank)",
    author="Author (optional, helps find the right cover and description)",
)
async def checkin(
    interaction: discord.Interaction,
    book_title: str,
    expected_readers: app_commands.Range[int, 1],
    description: Optional[str] = None,
    cover_url: Optional[str] = None,
    author: Optional[str] = None,
):
    if cover_url and not cover_url.startswith(("http://", "https://")):
        await interaction.response.send_message("❌ The cover URL must start with http:// or https://", ephemeral=True)
        return

    await interaction.response.defer()  # a Google Books lookup can take a few seconds

    if not description or not cover_url:
        info = await fetch_book_info(book_title, author)
        if info:
            description = description or info.short_description()
            cover_url = cover_url or info.thumbnail

    session = CheckinSession(
        book_title=book_title,
        description=description or "",
        cover_url=cover_url,
        expected_readers=expected_readers,
    )
    message = await interaction.followup.send(embed=build_checkin_embed(session), view=CheckinView(), wait=True)
    store.checkins[message.id] = session
    store.save()


@bot.tree.command(name="bookprice", description="Find the best prices for a book across multiple retailers")
@app_commands.describe(
    book_title="Title of the book to search for",
    author="Author of the book (optional, helps find more accurate results)",
)
async def bookprice(interaction: discord.Interaction, book_title: str, author: Optional[str] = None):
    await interaction.response.defer()  # this might take a moment

    query = f"{book_title} {author}" if author else book_title
    info, *scraped = await asyncio.gather(
        fetch_book_info(book_title, author),
        *(retailer.scrape_price(query) for retailer in RETAILERS),
    )
    prices = [(retailer, price, retailer.url_for(query)) for retailer, price in zip(RETAILERS, scraped)]
    await interaction.followup.send(embed=build_price_embed(book_title, info, prices))


# ===== Entry point =====

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("Error: DISCORD_BOT_TOKEN environment variable not set! Please set it with your bot token.")
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
=======
# ===== BOOK PRICE LOOKUP FUNCTIONS =====

async def get_book_info_from_google(book_title, author=None):
    """Get book info from Google Books API"""
    try:
        # Build search query with author if provided
        search_query = book_title
        if author:
            search_query = f"{book_title} {author}"
        
        url = f"https://www.googleapis.com/books/v1/volumes?q={quote_plus(search_query)}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('items'):
                        book = data['items'][0]['volumeInfo']
                        isbn_13 = None
                        isbn_10 = None
                        
                        # Get ISBNs
                        for identifier in book.get('industryIdentifiers', []):
                            if identifier['type'] == 'ISBN_13':
                                isbn_13 = identifier['identifier']
                            elif identifier['type'] == 'ISBN_10':
                                isbn_10 = identifier['identifier']
                        
                        return {
                            'title': book.get('title', book_title),
                            'authors': book.get('authors', []),
                            'isbn_13': isbn_13,
                            'isbn_10': isbn_10,
                            'thumbnail': book.get('imageLinks', {}).get('thumbnail')
                        }
    except Exception as e:
        print(f"Error fetching from Google Books: {e}")
    return None

async def scrape_amazon_price(book_title, author=None):
    """Attempt to scrape Amazon price"""
    try:
        search_query = f"{book_title} {author}" if author else book_title
        search_url = f"https://www.amazon.com/s?k={quote_plus(search_query)}&i=stripbooks"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    html = await response.text()
                    # Look for price patterns - try multiple formats
                    price_patterns = [
                        r'<span class="a-price-whole">(\d+)</span><span class="a-price-fraction">(\d+)</span>',
                        r'\$(\d+\.\d{2})',
                        r'price_color[^>]*>[\$]?(\d+\.\d{2})'
                    ]
                    for pattern in price_patterns:
                        match = re.search(pattern, html)
                        if match:
                            if len(match.groups()) == 2:  # whole and fraction
                                return float(f"{match.group(1)}.{match.group(2)}")
                            else:
                                return float(match.group(1))
    except Exception as e:
        print(f"Error scraping Amazon: {e}")
    return None

async def scrape_bookshop_price(book_title, author=None):
    """Attempt to scrape Bookshop.org price"""
    try:
        search_query = f"{book_title} {author}" if author else book_title
        search_url = f"https://bookshop.org/search?keywords={quote_plus(search_query)}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    html = await response.text()
                    # Look for price patterns
                    price_patterns = [
                        r'\$(\d+\.\d{2})',
                        r'price[^>]*>[\$]?(\d+\.\d{2})'
                    ]
                    for pattern in price_patterns:
                        match = re.search(pattern, html)
                        if match:
                            return float(match.group(1))
    except Exception as e:
        print(f"Error scraping Bookshop: {e}")
    return None

async def get_retailer_links(book_title, author=None, isbn_13=None):
    """Generate retailer search links and attempt to get prices"""
    retailers = []
    
    # Build search query with author if provided
    search_query = f"{book_title} {author}" if author else book_title
    
    # Amazon
    amazon_url = f"https://www.amazon.com/s?k={quote_plus(search_query)}&i=stripbooks"
    amazon_price = await scrape_amazon_price(book_title, author)
    retailers.append({
        'name': 'Amazon',
        'url': amazon_url,
        'price': amazon_price
    })
    
    # Bookshop.org (supports indie bookstores)
    bookshop_url = f"https://bookshop.org/search?keywords={quote_plus(search_query)}"
    bookshop_price = await scrape_bookshop_price(book_title, author)
    retailers.append({
        'name': 'Bookshop.org',
        'url': bookshop_url,
        'price': bookshop_price
    })
    
    # Barnes & Noble
    bn_url = f"https://www.barnesandnoble.com/s/{quote_plus(search_query)}"
    retailers.append({
        'name': 'Barnes & Noble',
        'url': bn_url,
        'price': None
    })
    
    # ThriftBooks (used books)
    thrift_url = f"https://www.thriftbooks.com/browse/?b.search={quote_plus(search_query)}"
    retailers.append({
        'name': 'ThriftBooks',
        'url': thrift_url,
        'price': None
    })
    
    # AbeBooks (used/rare books)
    abe_url = f"https://www.abebooks.com/servlet/SearchResults?kn={quote_plus(search_query)}"
    retailers.append({
        'name': 'AbeBooks',
        'url': abe_url,
        'price': None
    })
    
    # Book Depository (free worldwide shipping)
    bookdep_url = f"https://www.bookdepository.com/search?searchTerm={quote_plus(search_query)}"
    retailers.append({
        'name': 'Book Depository',
        'url': bookdep_url,
        'price': None
    })
    
    return retailers

def create_price_embed(book_info, retailers):
    """Create an embed showing book prices and retailer links"""
    title = book_info['title'] if book_info else "Book Price Lookup"
    
    embed = discord.Embed(
        title=f"💰 {title}",
        description="Here are links to find the best price:",
        color=discord.Color.green()
    )
    
    if book_info:
        if book_info.get('authors'):
            embed.add_field(
                name="Author(s)",
                value=", ".join(book_info['authors']),
                inline=False
            )
        
        if book_info.get('thumbnail'):
            embed.set_thumbnail(url=book_info['thumbnail'])
    
    # Sort retailers by price (prices first, then non-priced)
    priced_retailers = [r for r in retailers if r['price'] is not None]
    unpriced_retailers = [r for r in retailers if r['price'] is None]
    
    priced_retailers.sort(key=lambda x: x['price'])
    sorted_retailers = priced_retailers + unpriced_retailers
    
    # Show top 3 with prices if available
    retailer_text = ""
    for i, retailer in enumerate(sorted_retailers[:6], 1):
        price_str = f"**${retailer['price']:.2f}**" if retailer['price'] else "Price not available"
        retailer_text += f"{i}. [{retailer['name']}]({retailer['url']}) - {price_str}\n"
    
    embed.add_field(
        name="🔗 Check Prices",
        value=retailer_text,
        inline=False
    )
    
    if priced_retailers:
        embed.add_field(
            name="💡 Tip",
            value=f"Lowest found price: **${priced_retailers[0]['price']:.2f}** at {priced_retailers[0]['name']}",
            inline=False
        )
    else:
        embed.add_field(
            name="💡 Note",
            value="Couldn't fetch live prices. Click the links above to check current prices at each retailer.",
            inline=False
        )
    
    embed.set_footer(text="Prices are approximate and may vary. Click links to see current prices.")
    
    return embed

@bot.tree.command(name="bookprice", description="Find the best prices for a book across multiple retailers")
@app_commands.describe(
    book_title="Title of the book to search for",
    author="Author of the book (optional, helps find more accurate results)"
)
async def bookprice(interaction: discord.Interaction, book_title: str, author: str = None):
    await interaction.response.defer()  # This might take a moment
    
    # Get book info from Google Books
    book_info = await get_book_info_from_google(book_title, author)
    
    # Get retailer links and attempt price scraping
    retailers = await get_retailer_links(
        book_title,
        author,
        book_info['isbn_13'] if book_info else None
    )
    
    # Create and send embed
    embed = create_price_embed(book_info, retailers)
    await interaction.followup.send(embed=embed)

# ===== END BOOK PRICE LOOKUP FUNCTIONS =====


@bot.tree.command(name="checkin", description="Create a reading progress checkin for the current book")
@app_commands.describe(
    book_title="Title of the current book",
    description="Brief description of the book",
    cover_url="URL to the book cover image",
    expected_readers="Number of expected readers"
)
async def checkin(
    interaction: discord.Interaction,
    book_title: str,
    description: str,
    cover_url: str,
    expected_readers: int
):
    if expected_readers < 1:
        await interaction.response.send_message("❌ Expected readers must be at least 1!", ephemeral=True)
        return
    
    # Create new checkin session
    session = CheckinSession(book_title, description, cover_url, expected_readers)
    
    # Create embed
    embed = create_checkin_embed(session)
    
    # Send message with buttons
    view = CheckinView()
    await interaction.response.send_message(embed=embed, view=view)
    
    # Get the message that was just sent and store session by message_id
    message = await interaction.original_response()
    checkin_sessions[message.id] = session
    
    await interaction.followup.send(
        f"📢 {interaction.user.mention} started a reading progress checkin for **{book_title}**!",
        ephemeral=True
    )

# Run the bot
if __name__ == "__main__":
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set!")
        print("Please set it with your bot token.")
    else:
        bot.run(TOKEN)
>>>>>>> efd712bb240f89f5747699690b42ce54344752f3

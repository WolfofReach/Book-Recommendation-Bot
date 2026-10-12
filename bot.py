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
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown

log = logging.getLogger("bookclub")


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


# ===== Reading check-ins =====

@dataclass(frozen=True)
class Milestone:
    percent: int
    label: str
    style: discord.ButtonStyle
    empty_text: str
    confirmation: str
    announcement: str  # formatted with n=expected_readers


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
            value=f"{len(session.finished_users)}/{session.expected_users} participants finished",
            inline=False,
        )

    embed.set_footer(
        text=f"📚 Max {MAX_BOOKS_PER_USER} books per person • Hit Pass / Done when you're finished "
             "• Session starter can close anytime"
    )
    return embed


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

import discord
from discord.ext import commands
from discord import app_commands
import random
import os
import aiohttp
import asyncio
from urllib.parse import quote_plus
import re

# Bot setup
intents = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents)

# Store active sessions per guild
active_sessions = {}
checkin_sessions = {}  # Store active checkin sessions per guild

class BookSession:
    def __init__(self, starter_id, expected_users=None):
        self.starter_id = starter_id
        self.expected_users = expected_users
        self.recommendations = {}  # {user_id: [book1, book2]}
        self.user_names = {}  # {user_id: display_name}
        self.passed_users = set()
        self.is_closed = False
    
    def add_recommendation(self, user_id, book_title, user_name):
        if user_id not in self.recommendations:
            self.recommendations[user_id] = []
        self.recommendations[user_id].append(book_title)
        self.user_names[user_id] = user_name
    
    def user_book_count(self, user_id):
        return len(self.recommendations.get(user_id, []))
    
    def add_pass(self, user_id, user_name):
        self.passed_users.add(user_id)
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
    
    def is_complete(self):
        if self.expected_users is not None:
            return self.get_participant_count() >= self.expected_users
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

class PassButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="⏭️ Pass", style=discord.ButtonStyle.secondary, custom_id="bookclub:pass")
    
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
        
        if session.user_book_count(interaction.user.id) > 0:
            await interaction.response.send_message("You've already recommended a book!", ephemeral=True)
            return
        
        if session.has_passed(interaction.user.id):
            await interaction.response.send_message("You've already passed!", ephemeral=True)
            return
        
        session.add_pass(interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(f"✅ {interaction.user.mention} has passed on recommending.", ephemeral=False)
        
        # Update the embed
        await update_session_message(interaction)
        
        # Check if session is complete
        if session.is_complete() and not session.is_closed:
            await auto_close_session(interaction)

class CloseButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🎲 Close & Pick Winner", style=discord.ButtonStyle.success, custom_id="bookclub:close")
    
    async def callback(self, interaction: discord.Interaction):
        session = active_sessions.get(interaction.guild_id)
        
        if not session:
            await interaction.response.send_message(
                "⚠️ This session is no longer active (bot may have restarted). Please start a new session with `/bookclub`",
                ephemeral=True
            )
            return
        
        if interaction.user.id != session.starter_id:
            await interaction.response.send_message("Only the person who started the session can close it!", ephemeral=True)
            return
        
        if session.is_closed:
            await interaction.response.send_message("This session is already closed!", ephemeral=True)
            return
        
        await close_and_pick_winner(interaction, session)

class BookModal(discord.ui.Modal, title="Recommend a Book"):
    book_title = discord.ui.TextInput(
        label="Book Title",
        placeholder="Enter the book title...",
        required=True,
        max_length=200
    )
    
    def __init__(self, session):
        super().__init__()
        self.session = session
    
    async def on_submit(self, interaction: discord.Interaction):
        book = self.book_title.value.strip()
        self.session.add_recommendation(interaction.user.id, book, interaction.user.display_name)
        
        count = self.session.user_book_count(interaction.user.id)
        await interaction.response.send_message(
            f"✅ {interaction.user.mention} recommended: **{book}** ({count}/2 books)",
            ephemeral=False
        )
        
        # Update the embed
        await update_session_message(interaction)
        
        # Check if session is complete
        if self.session.is_complete() and not self.session.is_closed:
            await auto_close_session(interaction)

class Checkin50Button(discord.ui.Button):
    def __init__(self):
        super().__init__(label="📖 50% Progress", style=discord.ButtonStyle.primary, custom_id="checkin:50")
    
    async def callback(self, interaction: discord.Interaction):
        session = checkin_sessions.get(interaction.guild_id)
        
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
        session = checkin_sessions.get(interaction.guild_id)
        
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

async def update_session_message(interaction: discord.Interaction):
    """Update the session embed with current recommendations"""
    session = active_sessions.get(interaction.guild_id)
    if not session:
        return
    
    embed = create_session_embed(session, interaction.guild)
    
    try:
        # Edit the original message
        await interaction.message.edit(embed=embed)
    except:
        pass

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
    
    await interaction.channel.send(f"🎉 Expected number of participants reached! Closing session and picking winner...")
    await close_and_pick_winner(interaction, session, auto_close=True)

async def close_and_pick_winner(interaction: discord.Interaction, session, auto_close=False):
    """Close the session and randomly pick a book"""
    session.is_closed = True
    all_books = session.get_all_books()
    
    if not all_books:
        if auto_close:
            await interaction.channel.send("❌ No books were recommended! Session closed with no winner.")
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
        await interaction.channel.send(embed=embed)
    else:
        await interaction.response.send_message(embed=embed)
    
    # Clear the session
    del active_sessions[interaction.guild_id]

def create_session_embed(session, guild):
    """Create an embed showing current session status"""
    embed = discord.Embed(
        title="📚 Book Club Selection Session",
        description="Recommend books for the next book club read!",
        color=discord.Color.blue()
    )
    
    # Show recommendations
    if session.recommendations:
        rec_text = ""
        for user_id, books in session.recommendations.items():
            user_name = session.user_names.get(user_id, f"User {user_id}")
            rec_text += f"**{user_name}** ({len(books)}/2):\n"
            for book in books:
                rec_text += f"• {book}\n"
        embed.add_field(name="Current Recommendations", value=rec_text, inline=False)
    
    # Show passed users
    if session.passed_users:
        passed_text = ""
        for user_id in session.passed_users:
            user_name = session.user_names.get(user_id, f"User {user_id}")
            passed_text += f"{user_name}\n"
        embed.add_field(name="Passed", value=passed_text, inline=False)
    
    # Show progress
    if session.expected_users:
        embed.add_field(
            name="Progress",
            value=f"{session.get_participant_count()}/{session.expected_users} participants",
            inline=False
        )
    
    embed.set_footer(text="📚 Max 2 books per person • Session starter can close anytime")
    
    return embed

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
    
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} command(s)')
    except Exception as e:
        print(f'Failed to sync commands: {e}')

@bot.tree.command(name="bookclub", description="Start a book club selection session")
@app_commands.describe(expected_participants="Number of expected participants (optional)")
async def bookclub(interaction: discord.Interaction, expected_participants: int = None):
    if interaction.guild_id in active_sessions:
        await interaction.response.send_message("❌ There's already an active session in this server!", ephemeral=True)
        return
    
    if expected_participants is not None and expected_participants < 1:
        await interaction.response.send_message("❌ Expected participants must be at least 1!", ephemeral=True)
        return
    
    # Create new session
    session = BookSession(interaction.user.id, expected_participants)
    active_sessions[interaction.guild_id] = session
    
    # Create embed
    embed = create_session_embed(session, interaction.guild)
    
    # Send message with buttons
    view = BookClubView()
    await interaction.response.send_message(embed=embed, view=view)
    
    if expected_participants:
        await interaction.followup.send(
            f"📢 {interaction.user.mention} started a book club session! "
            f"Waiting for **{expected_participants}** participants to recommend or pass.",
            ephemeral=True
        )
    else:
        await interaction.followup.send(
            f"📢 {interaction.user.mention} started a book club session! "
            f"Click 'Close & Pick Winner' when everyone has finished recommending.",
            ephemeral=True
        )

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
    if interaction.guild_id in checkin_sessions:
        await interaction.response.send_message(
            "❌ There's already an active checkin session in this server! "
            "Only one checkin can be active at a time.",
            ephemeral=True
        )
        return
    
    if expected_readers < 1:
        await interaction.response.send_message("❌ Expected readers must be at least 1!", ephemeral=True)
        return
    
    # Create new checkin session
    session = CheckinSession(book_title, description, cover_url, expected_readers)
    checkin_sessions[interaction.guild_id] = session
    
    # Create embed
    embed = create_checkin_embed(session)
    
    # Send message with buttons
    view = CheckinView()
    await interaction.response.send_message(embed=embed, view=view)
    
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

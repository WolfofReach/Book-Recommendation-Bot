import discord
from discord.ext import commands
from discord import app_commands
import random
import os

# Bot setup
intents = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents)

# Store active sessions per guild
active_sessions = {}

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

async def auto_close_session(interaction: discord.Interaction):
    """Automatically close session if expected users reached"""
    session = active_sessions.get(interaction.guild_id)
    if not session:
        return
    
    await interaction.channel.send(f"🎉 Expected number of participants reached! Closing session and picking winner...")
    await close_and_pick_winner(interaction, session)

async def close_and_pick_winner(interaction: discord.Interaction, session):
    """Close the session and randomly pick a book"""
    session.is_closed = True
    all_books = session.get_all_books()
    
    if not all_books:
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

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    
    # Register persistent view so buttons work after restarts
    bot.add_view(BookClubView())
    
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

# Run the bot
if __name__ == "__main__":
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set!")
        print("Please set it with your bot token.")
    else:
        bot.run(TOKEN)
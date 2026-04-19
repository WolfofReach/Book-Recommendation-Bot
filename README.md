# Book Club Discord Bot

A Discord bot to help your book club select the next book to read. Members can recommend up to 2 books each, and the bot randomly picks a winner.

## Features

- Interactive book recommendation system with UI buttons
- Random book selection from all recommendations
- Maximum 2 books per user
- Users can pass if they don't want to recommend a book
- Optional: Set expected number of participants for auto-close once max number of participants have made a suggestion
- Only the session starter can manually close the session
- Reading progress checkin system to track who's reached 50% and 100% of the current book
- Automatic @everyone pings when expected readers reach progress milestones
- Book price lookup across multiple retailers with automatic price scraping when available

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Create a Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" and give it a name
3. Go to "Bot" section and click "Add Bot"
4. Click "Reset Token" and copy your bot token
5. Under "Privileged Gateway Intents", leave all intents disabled (default intents only)

### 3. Invite Bot to Your Server

1. Go to "OAuth2" then "URL Generator"
2. Select scopes: `bot` and `applications.commands`
3. Select bot permissions:
   - Send Messages
   - Embed Links
   - Read Message History
4. Copy the generated URL and open it in your browser
5. Select your server and authorize

### 4. Set Up Environment Variable

**For local testing:**

Create a `.env` file:
```
DISCORD_BOT_TOKEN=your_token_here
```

Then add to the top of `bookclub_bot.py`:
```python
from dotenv import load_dotenv
load_dotenv()
```

### 5. Run the Bot

**Local:**
```bash
python bookclub_bot.py
```

## Usage

### Starting a Session

**Option 1: Manual close (no participant limit)**
```
/bookclub
```
The person who started the session can click "Close & Pick Winner" whenever ready.

**Option 2: Auto-close (set expected participants)**
```
/bookclub expected_participants:5
```
The session will automatically close and pick a winner once 5 people have participated.

### Participating

Once a session is active, use the buttons:

- **Recommend a Book**: Opens a form to enter a book title (max 2 per person)
- **Pass**: Skip recommending without adding a book
- **Close & Pick Winner**: (Session starter only) Ends the session and randomly picks a book

### Rules

- Maximum 2 book recommendations per user
- Once you pass, you cannot recommend
- Once you recommend, you cannot pass
- Only the person who started the session can close it manually
- Sessions auto-close if expected participants count is set and reached

## Example Flow

1. Alice runs `/bookclub expected_participants:4`
2. Alice recommends "Project Hail Mary"
3. Bob recommends "The Martian"
4. Charlie passes
5. Dana recommends "Ender's Game" and "Dune"
6. Session auto-closes (4 participants reached)
7. Bot randomly selects one book as the winner

## Reading Progress Checkin

The `/checkin` command helps track reading progress for your current book club selection.

### Starting a Checkin Session

```
/checkin book_title:"Remarkably Bright Creatures" description:"A heartwarming tale of friendship between a woman and an octopus" cover_url:"https://..." expected_readers:10
```

**Parameters:**
- `book_title`: Title of the current book
- `description`: Brief description or tagline
- `cover_url`: URL to the book cover image
- `expected_readers`: Number of readers you expect to participate

### Checking In

Once a checkin session is active, members can click buttons to log their progress:

- **📖 50% Progress**: Click when you're halfway through the book
- **✅ Finished (100%)**: Click when you've completed the book

### Milestone Notifications

- When the expected number of readers reach 50%, the bot pings @everyone
- When the expected number of readers finish (100%), the bot pings @everyone again
- The embed updates in real-time showing who has checked in at each milestone

### Rules

- You can only check in once at each milestone (50% and 100%)
- Checking in at 100% automatically counts you as having reached 50%
- Only one checkin session can be active per server at a time
- The embed shows your book cover and tracks progress toward your expected reader count

## Book Price Lookup

The `/bookprice` command helps you find the best deals on books across multiple retailers.

### Usage

```
/bookprice book_title:"Remarkably Bright Creatures"
```

**What it does:**
- Searches Google Books API to get book information and ISBN
- Provides direct search links to 6 major book retailers:
  - Amazon
  - Bookshop.org (supports independent bookstores)
  - Barnes & Noble
  - ThriftBooks (used books)
  - AbeBooks (used/rare books)
  - Book Depository (free worldwide shipping)
- Attempts to scrape live prices from retailers when possible
- Displays results sorted by price (lowest first)
- Shows book cover and author information

### How It Works

The bot uses a **hybrid approach**:
1. **Gets book metadata** from Google Books API (title, authors, ISBN, cover image)
2. **Generates direct search links** to all major retailers
3. **Attempts price scraping** for retailers that allow it (Amazon, Bookshop.org)
4. **Sorts results** by price when available, with the cheapest options shown first

### Example Output

The embed will show:
- Book title and author(s)
- Book cover thumbnail
- List of retailer links with prices (when available)
- A tip showing the lowest price found
- Note that prices are approximate and users should click through to confirm

**Note:** Price scraping may not always work due to anti-bot measures, but the direct links will always be provided so members can quickly check all retailers.


## Troubleshooting

If the bot isn't responding to slash commands:
- Wait a few minutes after inviting (commands need to sync)
- Make sure the bot has proper permissions
- Verify the bot token is correctly set in environment variables
- Try kicking and re-inviting the bot

## License

MIT

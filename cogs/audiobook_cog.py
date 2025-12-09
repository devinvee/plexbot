import discord
from discord.ext import commands
import logging
import aiohttp
import os
import asyncio
from mutagen.id3 import ID3, TIT1, ID3NoHeaderError
from mutagen.mp4 import MP4
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# --- UI Components ---


class BookSelect(discord.ui.Select):
    def __init__(self, books: List[Dict]):
        options = []
        # Limit to top 5 results to avoid hitting Discord limits
        for i, book in enumerate(books[:5]):
            vol = book.get('volumeInfo', {})
            title = vol.get('title', 'Unknown')[:90]
            authors = ", ".join(vol.get('authors', []))[:50]
            # Store the index as the value so we can retrieve the full object later
            options.append(discord.SelectOption(
                label=title,
                description=f"by {authors}",
                value=str(i)
            ))

        super().__init__(placeholder="Select the correct book match...",
                         min_values=1, max_values=1)
        self.books = books

    async def callback(self, interaction: discord.Interaction):
        selected_index = int(self.values[0])
        selected_book = self.books[selected_index]

        # Disable the view so they can't click again
        self.view.stop()
        await interaction.response.defer()

        # Call the processing function on the Cog logic
        await self.view.cog.finalize_tagging(
            interaction,
            selected_book,
            self.view.file_path,
            self.view.readarr_metadata
        )


class ManualMatchView(discord.ui.View):
    def __init__(self, books: List[Dict], file_path: str, readarr_metadata: Dict, cog):
        super().__init__(timeout=300)
        self.file_path = file_path
        self.readarr_metadata = readarr_metadata
        self.cog = cog
        self.add_item(BookSelect(books))

# --- Main Cog ---


class AudiobookCog(commands.Cog, name="Audiobook"):
    def __init__(self, bot):
        self.bot = bot
        self.google_api_key = os.getenv("GOOGLE_BOOKS_API_KEY")

    # --- Commands ---

    @commands.hybrid_command(name="absscan", description="Force a scan of the Audiobookshelf library.")
    @commands.is_owner()  # Or @commands.has_permissions(administrator=True)
    async def absscan_command(self, ctx: commands.Context):
        """Manually triggers a library scan."""
        await ctx.defer()

        success = await self.trigger_abs_scan()

        if success:
            await ctx.send("✅ **Audiobookshelf Scan Initiated.**")
        else:
            await ctx.send("❌ **Scan Failed.** Check bot logs and environment variables.")

    # --- Core Logic ---

    async def process_readarr_event(self, payload: Dict):
        """Main entry point from the webhook."""
        event_type = payload.get('eventType')

        if event_type == 'Test':
            logger.info("Readarr Test Event Received.")
            return

        if event_type not in ['Download', 'Upgrade', 'Rename']:
            return

        # 1. Extract Data
        book_info = payload.get('book', {})
        title = book_info.get('title', 'Unknown Title')

        author = payload.get('author', {}).get('name')
        if not author:
            author = book_info.get('authorTitle')

        # 2. Robust Path Extraction
        file_path = None

        # Priority 1: Standard 'bookFile' (Import/Upgrade)
        if 'bookFile' in payload:
            file_path = payload['bookFile'].get('path')

        # Priority 2: 'renamedBookFiles' (Mass Editor / Rename)
        elif 'renamedBookFiles' in payload and len(payload['renamedBookFiles']) > 0:
            file_path = payload['renamedBookFiles'][0].get('path')

        # Priority 3: 'sourcePath' (Fallback)
        elif 'sourcePath' in payload:
            file_path = payload['sourcePath']

        # Debugging Block: If path is missing, log detailed info
        if not file_path:
            logger.error(
                f"❌ Could not find file path for '{title}'. Event: {event_type}")
            logger.error(f"Available Keys in Payload: {list(payload.keys())}")

            if 'bookFile' in payload:
                logger.error(f"Content of 'bookFile': {payload['bookFile']}")
            elif 'renamedBookFiles' in payload:
                logger.error(
                    f"Content of 'renamedBookFiles': {payload['renamedBookFiles']}")

            return

        if not os.path.exists(file_path):
            logger.warning(
                f"⚠️ File path not found on server: {file_path}. Check Docker volumes.")
            return

        logger.info(
            f"Processing Audiobook ({event_type}): {title} by {author}")

        # 3. Search Google Books
        results = await self.search_google_books(title, author)

        if not results:
            logger.warning(
                f"No Google Books results found for {title}. Skipping.")
            return

        # 4. Confidence Check & Tagging
        top_result = results[0]['volumeInfo']
        match_confidence = self.calculate_confidence(title, author, top_result)

        if match_confidence > 0.90:
            logger.info(
                f"High confidence match ({match_confidence}). Tagging automatically.")
            await self.finalize_tagging(None, results[0], file_path, payload)

        elif event_type == 'Rename':
            # SILENT MODE: If this was a Mass Rename, DO NOT spam Discord with questions.
            logger.info(
                f"Low confidence ({match_confidence}) during Rename. Skipping to avoid spam.")

        else:
            # Only ask for manual approval on new Downloads/Upgrades
            logger.info(
                f"Low confidence ({match_confidence}). Requesting user approval.")
            await self.request_manual_approval(results, title, author, file_path, payload)

    async def search_google_books(self, title: str, author: str) -> List[Dict]:
        """Queries Google Books API."""
        query = f"intitle:{title}+inauthor:{author}"
        url = f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=5"
        if self.google_api_key:
            url += f"&key={self.google_api_key}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('items', [])
                    else:
                        logger.error(f"Google Books API Error: {resp.status}")
                        return []
        except Exception as e:
            logger.error(f"Error searching Google Books: {e}")
            return []

    def calculate_confidence(self, r_title, r_author, g_result) -> float:
        """Crude string matching confidence."""
        g_title = g_result.get('title', '').lower()
        r_title = r_title.lower() if r_title else ""

        if r_title == g_title:
            return 1.0
        if r_title in g_title:
            return 0.95

        return 0.5

    async def request_manual_approval(self, results, title, author, file_path, payload):
        """Sends an embed to Discord for the user to pick."""
        channel_id = os.getenv("READARR_CHANNEL_ID")

        if not channel_id:
            logger.warning(
                "READARR_CHANNEL_ID not set in .env. Cannot ask for manual approval.")
            return

        try:
            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                logger.warning(f"Could not find channel with ID {channel_id}")
                return

            embed = discord.Embed(
                title="📚 Metadata Approval Needed",
                description=f"Readarr imported **{title}** by **{author}**.\nI found multiple potential matches. Please select the correct one to apply Series tags.",
                color=discord.Color.orange()
            )

            view = ManualMatchView(results, file_path, payload, self)
            await channel.send(embed=embed, view=view)
        except ValueError:
            logger.error(f"Invalid READARR_CHANNEL_ID: {channel_id}")

    async def finalize_tagging(self, interaction, google_book_data, file_path, readarr_payload):
        """Applies tags and notifies ABS."""
        vol_info = google_book_data.get('volumeInfo', {})

        # --- SERIES LOGIC ---
        series_tags = []

        # 1. Get Primary Series from Readarr
        readarr_series = readarr_payload.get('series', {}).get('title')
        if readarr_series:
            series_tags.append(readarr_series)

        # 2. Check Description/Categories for "Universe" keywords
        description = vol_info.get('description', '').lower()
        categories = str(vol_info.get('categories', [])).lower()

        # Example Universe Logic (Expand this with a dictionary/json map later!)
        if "cosmere" in description or "cosmere" in categories:
            series_tags.append("The Cosmere")
        if "middle-earth" in description or "tolkien" in categories:
            series_tags.append("Legendarium")
        if "grishaverse" in description:
            series_tags.append("Grishaverse")

        # Format for ABS: "Series A; Series B"
        tag_string = "; ".join(series_tags)

        msg = ""
        if not tag_string:
            logger.info("No series data found to tag.")
            msg = "No series data found, skipping retag."
        else:
            self.apply_tags(file_path, tag_string)
            msg = f"Tagged with: `{tag_string}`"

        # --- NOTIFICATION ---
        if interaction:
            await interaction.followup.send(f"✅ **Processed!** {msg}")
            try:
                await interaction.message.delete()
            except:
                pass

        # --- ABS SCAN ---
        await self.trigger_abs_scan()

    def apply_tags(self, filepath, tag_string):
        """Writes the CONTENTGROUP (grouping) tag using mutagen."""
        try:
            ext = os.path.splitext(filepath)[1].lower()
            if ext == ".mp3":
                try:
                    audio = ID3(filepath)
                except ID3NoHeaderError:
                    audio = ID3()
                audio.add(TIT1(encoding=3, text=tag_string))
                audio.save(filepath)
            elif ext in [".m4b", ".m4a"]:
                audio = MP4(filepath)
                audio.tags['\xa9grp'] = tag_string
                audio.save()
            logger.info(f"Successfully wrote tags to {filepath}")
        except Exception as e:
            logger.error(f"Tagging failed for {filepath}: {e}")

    async def trigger_abs_scan(self) -> bool:
        """Hits the Audiobookshelf API to scan the library."""
        abs_url = os.getenv("ABS_URL")
        abs_token = os.getenv("ABS_TOKEN")
        library_id = os.getenv("ABS_LIBRARY_ID")

        if not all([abs_url, abs_token, library_id]):
            logger.warning("ABS config missing. Cannot trigger scan.")
            return False

        abs_url = abs_url.rstrip('/')
        url = f"{abs_url}/api/libraries/{library_id}/scan"
        headers = {"Authorization": f"Bearer {abs_token}"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers) as resp:
                    if resp.status == 200:
                        logger.info("Triggered Audiobookshelf Library Scan.")
                        return True
                    else:
                        text = await resp.text()
                        logger.error(
                            f"Failed to scan ABS: {resp.status} - {text}")
                        return False
        except Exception as e:
            logger.error(f"ABS Scan error: {e}")
            return False


async def setup(bot):
    await bot.add_cog(AudiobookCog(bot))

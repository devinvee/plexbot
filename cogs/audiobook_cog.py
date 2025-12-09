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
            # Store the Google ID as the value
            options.append(discord.SelectOption(
                label=title,
                description=f"by {authors}",
                value=str(i)  # We'll use index to retrieve full object later
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

        # Call the processing function on the View's parent (the Cog logic)
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

    async def process_readarr_event(self, payload: Dict):
        """Main entry point from the webhook."""
        if payload.get('eventType') == 'Test':
            logger.info("Readarr Test Event Received.")
            return

        # 1. Extract Data
        book_info = payload.get('book', {})
        file_info = payload.get('bookFile', {})

        title = book_info.get('title')
        # Readarr sometimes puts author at top level
        author = payload.get('author', {}).get('name')
        # Fallback for author location
        if not author:
            author = book_info.get('authorTitle')

        file_path = file_info.get('path')

        # Ensure path exists (Docker volume mapping check)
        if not os.path.exists(file_path):
            logger.error(
                f"File not found at {file_path}. Check Docker volume mappings.")
            return

        logger.info(f"Processing Audiobook: {title} by {author}")

        # 2. Search Google Books
        results = await self.search_google_books(title, author)

        if not results:
            logger.warning(f"No Google Books results found for {title}.")
            # Fallback: Just tag with Readarr data? Or notify failure?
            return

        # 3. Confidence Check
        # Simple heuristic: If the first result title is a very close match, auto-approve.
        top_result = results[0]['volumeInfo']
        match_confidence = self.calculate_confidence(title, author, top_result)

        if match_confidence > 0.90:
            logger.info(
                f"High confidence match ({match_confidence}). Tagging automatically.")
            await self.finalize_tagging(None, results[0], file_path, payload)
        else:
            logger.info(
                f"Low confidence ({match_confidence}). Requesting user approval.")
            await self.request_manual_approval(results, title, author, file_path, payload)

    async def search_google_books(self, title: str, author: str) -> List[Dict]:
        """Queries Google Books API."""
        query = f"intitle:{title}+inauthor:{author}"
        url = f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=5"
        if self.google_api_key:
            url += f"&key={self.google_api_key}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('items', [])
                return []

    def calculate_confidence(self, r_title, r_author, g_result) -> float:
        """Crude string matching confidence."""
        g_title = g_result.get('title', '').lower()
        r_title = r_title.lower()

        # If exact title match
        if r_title == g_title:
            return 1.0
        # If Readarr title is contained in Google title (e.g. "Mistborn" in "Mistborn: The Final Empire")
        if r_title in g_title:
            return 0.95

        return 0.5

    async def request_manual_approval(self, results, title, author, file_path, payload):
        """Sends an embed to Discord for the user to pick."""
        channel_id = self.bot.config.discord.sonarr_notification_channel_id  # Reusing Sonarr channel or define new one
        if not channel_id:
            logger.warning("No notification channel set for approval.")
            return

        channel = self.bot.get_channel(int(channel_id))

        embed = discord.Embed(
            title="📚 Metadata Approval Needed",
            description=f"Readarr imported **{title}** by **{author}**.\nI found multiple potential matches on Google Books. Please select the correct one to apply Series tags.",
            color=discord.Color.orange()
        )

        view = ManualMatchView(results, file_path, payload, self)
        await channel.send(embed=embed, view=view)

    async def finalize_tagging(self, interaction, google_book_data, file_path, readarr_payload):
        """Applies tags and notifies ABS."""
        vol_info = google_book_data.get('volumeInfo', {})

        # --- SERIES LOGIC ---
        # Google Books API is messy with series.
        # We will try to extract it from 'categories' or 'description' or construct it.
        # Ideally, you combine this with your 'universes.json' map for maximum power.

        series_tags = []

        # 1. Get Primary Series from Readarr (It's usually reliable for the main one)
        readarr_series = readarr_payload.get('series', {}).get('title')
        if readarr_series:
            # We assume Readarr knows the sequence from the filename logic
            # or we just tag the Series Name and let ABS guess the number
            series_tags.append(readarr_series)

        # 2. Check Description/Categories for "Universe" keywords (Cosmere, etc.)
        description = vol_info.get('description', '').lower()
        categories = str(vol_info.get('categories', [])).lower()

        # Example Universe Logic (Expand this!)
        if "cosmere" in description or "cosmere" in categories:
            series_tags.append("The Cosmere")
        if "middle-earth" in description:
            series_tags.append("Legendarium")

        # Format the tag string for ABS: "Series A; Series B"
        tag_string = "; ".join(series_tags)

        if not tag_string:
            logger.info("No series data found to tag.")
            msg = "No series data found, skipping retag."
        else:
            self.apply_tags(file_path, tag_string)
            msg = f"Tagged with: `{tag_string}`"

        # --- NOTIFICATION ---
        if interaction:
            await interaction.followup.send(f"✅ **Processed!** {msg}")
            # Delete the selector to clean up chat
            try:
                await interaction.message.delete()
            except:
                pass

        # --- ABS SCAN ---
        await self.trigger_abs_scan()

    def apply_tags(self, filepath, tag_string):
        """Writes the CONTENTGROUP tag using mutagen."""
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
            logger.error(f"Tagging failed: {e}")

    async def trigger_abs_scan(self):
        """Hits the Audiobookshelf API to scan."""
        # You need to add ABS config to your config.json or .env
        abs_url = os.getenv("ABS_URL")  # e.g. http://bas:13378
        abs_token = os.getenv("ABS_TOKEN")
        library_id = os.getenv("ABS_LIBRARY_ID")

        if not all([abs_url, abs_token, library_id]):
            logger.warning("ABS config missing. Cannot trigger scan.")
            return

        url = f"{abs_url}/api/libraries/{library_id}/scan"
        headers = {"Authorization": f"Bearer {abs_token}"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers) as resp:
                    if resp.status == 200:
                        logger.info("Triggered Audiobookshelf Library Scan.")
                    else:
                        logger.error(f"Failed to scan ABS: {resp.status}")
        except Exception as e:
            logger.error(f"ABS Scan error: {e}")


async def setup(bot):
    await bot.add_cog(AudiobookCog(bot))

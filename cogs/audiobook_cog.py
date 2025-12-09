import discord
from discord.ext import commands
import logging
import aiohttp
import os
import asyncio
import json
import re
from difflib import SequenceMatcher
from mutagen.id3 import ID3, TIT1, ID3NoHeaderError
from mutagen.mp4 import MP4
from typing import List, Dict, Optional, Set

logger = logging.getLogger(__name__)

# --- UI Components ---


class BookSelect(discord.ui.Select):
    def __init__(self, books: List[Dict]):
        options = []
        for i, book in enumerate(books[:5]):
            # Handle both Google Books and Open Library formats
            title = book.get('title') or book.get(
                'volumeInfo', {}).get('title', 'Unknown')

            # Extract authors safely from either format
            authors_list = book.get('author_name') or book.get(
                'volumeInfo', {}).get('authors', [])
            if isinstance(authors_list, list):
                authors = ", ".join(authors_list)[:50]
            else:
                authors = str(authors_list)[:50]

            options.append(discord.SelectOption(
                label=title[:90],
                description=f"by {authors}",
                value=str(i)
            ))

        super().__init__(placeholder="Select the correct book match...",
                         min_values=1, max_values=1)
        self.books = books

    async def callback(self, interaction: discord.Interaction):
        selected_index = int(self.values[0])
        selected_book = self.books[selected_index]
        self.view.stop()
        await interaction.response.defer()

        # We pass the selected book as BOTH gb and ol result to force processing
        # The logic handles the format difference
        await self.view.cog.finalize_tagging(
            interaction,
            [selected_book],  # Treat as GB result list
            [selected_book],  # Treat as OL result list
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
    @commands.is_owner()
    async def absscan_command(self, ctx: commands.Context):
        """Manually triggers a library scan."""
        await ctx.defer()
        if await self.trigger_abs_scan():
            await ctx.send("✅ **Audiobookshelf Scan Initiated.**")
        else:
            await ctx.send("❌ **Scan Failed.** Check bot logs.")

    # --- Core Logic ---

    async def process_readarr_event(self, payload: Dict):
        """Main entry point from the webhook."""

        # Debug Dump (Optional)
        try:
            # logger.info(f"📥 READARR PAYLOAD DUMP:\n{json.dumps(payload, indent=4)}")
            pass
        except:
            pass

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

        if 'bookFile' in payload:
            file_path = payload['bookFile'].get('path')
        elif 'bookFiles' in payload and len(payload['bookFiles']) > 0:
            file_path = payload['bookFiles'][0].get('path')
        elif 'renamedBookFiles' in payload and len(payload['renamedBookFiles']) > 0:
            file_path = payload['renamedBookFiles'][0].get('path')
        elif 'sourcePath' in payload:
            file_path = payload['sourcePath']

        if not file_path:
            logger.error(
                f"❌ Could not find file path for '{title}'. Event: {event_type}")
            return

        if not os.path.exists(file_path):
            logger.warning(
                f"⚠️ File path not found on server: {file_path}. Check Docker volumes.")
            return

        logger.info(
            f"Processing Audiobook ({event_type}): {title} by {author}")

        # 3. Parallel API Search (OpenLibrary + Google Books)
        results_ol, results_gb = await asyncio.gather(
            self.search_openlibrary(title, author),
            self.search_google_books(title, author)
        )

        primary_match = None
        if results_gb:
            primary_match = results_gb[0]
        elif results_ol:
            primary_match = results_ol[0]

        match_confidence = 0.0

        if not primary_match:
            logger.warning(
                f"No API results found for {title}. Proceeding with folder logic only.")
            match_confidence = 1.0  # Trust Readarr/Folder logic if API fails
        else:
            # 4. Confidence Check
            match_title = primary_match.get('volumeInfo', {}).get(
                'title') or primary_match.get('title')
            match_confidence = self.calculate_confidence(
                title, author, match_title)

        if match_confidence > 0.85:
            logger.info(
                f"High confidence match ({match_confidence}). Tagging automatically.")
            await self.finalize_tagging(None, results_gb, results_ol, file_path, payload)

        elif event_type == 'Rename':
            logger.info(
                f"Low confidence ({match_confidence}) during Rename. Skipping.")

        else:
            logger.info(
                f"Low confidence ({match_confidence}). Requesting user approval.")
            # Present Google Books results if available (better covers/titles usually), else OL
            await self.request_manual_approval(results_gb or results_ol, title, author, file_path, payload)

    async def search_google_books(self, title: str, author: str) -> List[Dict]:
        """Queries Google Books API."""
        import urllib.parse
        q_title = urllib.parse.quote(title)
        q_author = urllib.parse.quote(author)
        url = f"https://www.googleapis.com/books/v1/volumes?q=intitle:{q_title}+inauthor:{q_author}&maxResults=5"
        if self.google_api_key:
            url += f"&key={self.google_api_key}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('items', [])
                    return []
        except Exception as e:
            logger.error(f"Error searching Google Books: {e}")
            return []

    async def search_openlibrary(self, title: str, author: str) -> List[Dict]:
        """Queries Open Library API (Excellent for series info)."""
        import urllib.parse
        q_title = urllib.parse.quote(title)
        q_author = urllib.parse.quote(author)
        url = f"https://openlibrary.org/search.json?title={q_title}&author={q_author}&limit=5"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('docs', [])
                    return []
        except Exception as e:
            logger.error(f"Error searching Open Library: {e}")
            return []

    def calculate_confidence(self, r_title, r_author, match_title) -> float:
        if not match_title:
            return 0.0
        r_title = r_title.lower()
        match_title = match_title.lower()

        if r_title == match_title:
            return 1.0
        if r_title in match_title:
            return 0.95

        matcher = SequenceMatcher(None, r_title, match_title)
        return matcher.ratio()

    async def request_manual_approval(self, results, title, author, file_path, payload):
        channel_id = os.getenv("READARR_CHANNEL_ID")
        if not channel_id:
            return

        try:
            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                return

            embed = discord.Embed(
                title="📚 Metadata Approval Needed",
                description=f"Readarr imported **{title}** by **{author}**.\nI found matches. Please select the correct one to apply Series tags.",
                color=discord.Color.orange()
            )
            view = ManualMatchView(results, file_path, payload, self)
            await channel.send(embed=embed, view=view)
        except:
            pass

    async def finalize_tagging(self, interaction, gb_results, ol_results, file_path, readarr_payload):
        found_series: Set[str] = set()

        # --- 1. Readarr Metadata (Primary Source) ---
        readarr_series = readarr_payload.get('series', {}).get('title')
        if readarr_series:
            found_series.add(readarr_series)

        # --- 2. Open Library (Dynamic Multi-Series Source) ---
        # This is where the magic happens for multiple series
        if ol_results:
            # Check the top 2 results to be safe
            for doc in ol_results[:2]:
                if 'series' in doc:  # 'series' is a LIST in Open Library
                    for s in doc['series']:
                        found_series.add(s)

        # --- 3. Google Books (Dynamic Subtitle Source) ---
        if gb_results:
            vol = gb_results[0].get('volumeInfo', {})
            subtitle = vol.get('subtitle', '')
            if subtitle:
                # Remove "Book X" and just keep the series name
                clean_series = re.sub(
                    r'[,:]?\s*(?:Book|Vol\.?|Volume|#)\s*\d+', '', subtitle, flags=re.IGNORECASE).strip()
                if clean_series and len(clean_series) > 2:
                    found_series.add(clean_series)

        # --- 4. Folder Structure (Fallback) ---
        r_author = readarr_payload.get('author', {}).get('name', '').lower()
        r_book_title = readarr_payload.get('book', {}).get('title', '').lower()

        if file_path:
            try:
                path_parts = os.path.normpath(file_path).split(os.sep)
                author_idx = -1
                for i, part in enumerate(path_parts):
                    if r_author in part.lower() or part.lower() in r_author:
                        author_idx = i
                        break

                if author_idx != -1 and author_idx + 1 < len(path_parts):
                    potential_series_folder = path_parts[author_idx + 1]
                    # If the folder name is NOT the book title, it's likely a series folder
                    similarity = SequenceMatcher(
                        None, potential_series_folder.lower(), r_book_title).ratio()
                    if similarity < 0.6:
                        logger.info(
                            f"Detected series from folder path: {potential_series_folder}")
                        found_series.add(potential_series_folder)
            except Exception as e:
                logger.error(f"Path parsing failed: {e}")

        # --- Format and Apply ---
        clean_tags = []
        for s in found_series:
            s = s.strip()
            if len(s) > 2 and len(s) < 100:
                clean_tags.append(s)

        tag_string = "; ".join(clean_tags)

        msg = ""
        if not tag_string:
            logger.info("No series data found dynamically.")
            msg = "No series data found, skipping retag."
        else:
            self.apply_tags(file_path, tag_string)
            msg = f"Tagged with: `{tag_string}`"

        if interaction:
            await interaction.followup.send(f"✅ **Processed!** {msg}")
            try:
                await interaction.message.delete()
            except:
                pass

        await self.trigger_abs_scan()

    def apply_tags(self, filepath, tag_string):
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
            logger.info(f"Successfully wrote tags to {filepath}: {tag_string}")
        except Exception as e:
            logger.error(f"Tagging failed for {filepath}: {e}")

    async def trigger_abs_scan(self) -> bool:
        abs_url = os.getenv("ABS_URL")
        abs_token = os.getenv("ABS_TOKEN")
        library_id = os.getenv("ABS_LIBRARY_ID")

        if not all([abs_url, abs_token, library_id]):
            return False

        abs_url = abs_url.rstrip('/')
        url = f"{abs_url}/api/libraries/{library_id}/scan"
        headers = {"Authorization": f"Bearer {abs_token}"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.error(f"ABS Scan error: {e}")
            return False


async def setup(bot):
    await bot.add_cog(AudiobookCog(bot))

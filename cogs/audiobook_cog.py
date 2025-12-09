import discord
from discord.ext import commands
import logging
import aiohttp
import os
import asyncio
import json
import re
from difflib import SequenceMatcher
from typing import List, Dict, Optional, Set

logger = logging.getLogger(__name__)

# --- UI Components ---


class BookSelect(discord.ui.Select):
    def __init__(self, books: List[Dict]):
        options = []
        for i, book in enumerate(books[:5]):
            title = book.get('title') or book.get(
                'volumeInfo', {}).get('title', 'Unknown')
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

        logger.info(
            f"User {interaction.user} selected book index {selected_index}: {selected_book.get('title', 'Unknown')}")
        await interaction.response.defer()

        await self.view.cog.finalize_tagging(
            interaction,
            [selected_book],
            [selected_book],
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
        if not self.google_api_key:
            logger.warning(
                "GOOGLE_BOOKS_API_KEY is not set. Rate limits will be strict.")

    # --- Commands ---

    @commands.hybrid_command(name="absscan", description="Force a scan of the Audiobookshelf library.")
    @commands.is_owner()
    async def absscan_command(self, ctx: commands.Context):
        """Manually triggers a library scan."""
        logger.info(f"Manual ABS scan triggered by {ctx.author}")
        await ctx.defer()
        if await self.trigger_abs_scan():
            await ctx.send("✅ **Audiobookshelf Scan Initiated.**")
        else:
            await ctx.send("❌ **Scan Failed.** Check bot logs.")

    # --- Core Logic ---

    async def process_readarr_event(self, payload: Dict):
        """Main entry point from the webhook."""

        # DEBUG: Dump payload to trace exact data
        try:
            # Only dumping if log level is debug to avoid clutter
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"📥 READARR PAYLOAD DUMP:\n{json.dumps(payload, indent=4)}")
        except Exception as e:
            logger.error(f"Failed to dump payload: {e}")

        event_type = payload.get('eventType')
        logger.debug(f"Event Type Received: {event_type}")

        if event_type == 'Test':
            logger.info("Readarr Test Event Received. Connection is working.")
            return

        if event_type not in ['Download', 'Upgrade', 'Rename']:
            logger.debug(f"Ignored event type: {event_type}")
            return

        # 1. Extract Data
        book_info = payload.get('book', {})
        title = book_info.get('title', 'Unknown Title')

        author = payload.get('author', {}).get('name')
        if not author:
            author = book_info.get('authorTitle')

        logger.debug(f"Parsed Metadata - Title: '{title}', Author: '{author}'")

        # 2. Robust Path Extraction
        file_path = None
        if 'bookFile' in payload:
            file_path = payload['bookFile'].get('path')
            logger.debug("Found path in 'bookFile'")
        elif 'bookFiles' in payload and len(payload['bookFiles']) > 0:
            file_path = payload['bookFiles'][0].get('path')
            logger.debug("Found path in 'bookFiles' list")
        elif 'renamedBookFiles' in payload and len(payload['renamedBookFiles']) > 0:
            file_path = payload['renamedBookFiles'][0].get('path')
            logger.debug("Found path in 'renamedBookFiles' (Mass Editor)")
        elif 'sourcePath' in payload:
            file_path = payload['sourcePath']
            logger.debug("Found path in 'sourcePath'")

        if not file_path:
            logger.error(
                f"❌ Could not find file path for '{title}'. Event: {event_type}")
            logger.debug(f"Available Payload Keys: {list(payload.keys())}")
            return

        if not os.path.exists(file_path):
            logger.error(
                f"⚠️ File path not found on server: {file_path}. Check Docker volume mappings in docker-compose.yml.")
            return

        logger.info(
            f"Processing Audiobook ({event_type}): {title} by {author}")

        # 3. Parallel API Search
        logger.debug(f"Starting API searches for: {title}")
        results_ol, results_gb = await asyncio.gather(
            self.search_openlibrary(title, author),
            self.search_google_books(title, author)
        )

        logger.debug(
            f"API Results - OpenLibrary: {len(results_ol)}, GoogleBooks: {len(results_gb)}")

        primary_match = None
        if results_gb:
            primary_match = results_gb[0]
        elif results_ol:
            primary_match = results_ol[0]

        match_confidence = 0.0

        if not primary_match:
            logger.warning(
                f"No API results found for {title}. Proceeding with folder logic only.")
            match_confidence = 1.0  # Trust Readarr/Folder logic
        else:
            match_title = primary_match.get('volumeInfo', {}).get(
                'title') or primary_match.get('title')
            match_confidence = self.calculate_confidence(
                title, author, match_title)
            logger.debug(
                f"Confidence Check: '{title}' vs '{match_title}' = {match_confidence}")

        if match_confidence > 0.85:
            logger.info(
                f"High confidence match ({match_confidence}). Updating metadata automatically.")
            await self.finalize_tagging(None, results_gb, results_ol, file_path, payload)

        elif event_type == 'Rename':
            logger.info(
                f"Low confidence ({match_confidence}) during Rename event. Skipping to avoid spam.")

        else:
            logger.info(
                f"Low confidence ({match_confidence}). Requesting user approval via Discord.")
            await self.request_manual_approval(results_gb or results_ol, title, author, file_path, payload)

    async def search_google_books(self, title: str, author: str) -> List[Dict]:
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
                    logger.warning(
                        f"Google Books API returned status {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"Error searching Google Books: {e}")
            return []

    async def search_openlibrary(self, title: str, author: str) -> List[Dict]:
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
                    logger.warning(
                        f"Open Library API returned status {resp.status}")
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
            logger.critical(
                "READARR_CHANNEL_ID is not set in environment variables.")
            return

        try:
            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                logger.error(
                    f"Could not find Discord channel with ID {channel_id}")
                return

            embed = discord.Embed(
                title="📚 Metadata Approval Needed",
                description=f"Readarr imported **{title}** by **{author}**.\nI found matches. Please select the correct one to apply metadata.",
                color=discord.Color.orange()
            )
            view = ManualMatchView(results, file_path, payload, self)
            await channel.send(embed=embed, view=view)
            logger.info(
                f"Sent manual approval request to channel {channel_id}")
        except Exception as e:
            logger.error(f"Failed to send Discord message: {e}")

    def extract_sequence_number(self, text: str) -> str:
        if not text:
            return ""
        match = re.search(
            r'(?:Book|Vol\.?|Volume|#|\[|\()\s*(\d+(\.\d+)?)', text, re.IGNORECASE)
        if match:
            return f" #{match.group(1)}"
        return ""

    async def finalize_tagging(self, interaction, gb_results, ol_results, file_path, readarr_payload):
        logger.info(f"Finalizing metadata for file: {file_path}")
        found_series: Set[str] = set()

        # Metadata dictionary setup
        meta_data = {}

        # 1. Readarr Info
        r_book = readarr_payload.get('book', {})
        r_title = r_book.get('title')
        r_author = readarr_payload.get('author', {}).get(
            'name') or r_book.get('authorTitle')

        meta_data['title'] = r_title
        meta_data['authors'] = [r_author] if r_author else []
        meta_data['publishedYear'] = str(r_book.get('releaseDate', ''))[:4]

        logger.debug(f"Base Metadata from Readarr: {meta_data}")

        # 2. Google Books Extraction
        if gb_results:
            vol = gb_results[0].get('volumeInfo', {})
            meta_data['description'] = vol.get('description', '')
            meta_data['publisher'] = vol.get('publisher', '')
            meta_data['genres'] = vol.get('categories', [])
            meta_data['language'] = vol.get('language', '')

            for ident in vol.get('industryIdentifiers', []):
                if ident.get('type') == 'ISBN_13':
                    meta_data['isbn'] = ident.get('identifier')
                if ident.get('type') == 'ISBN_10':
                    meta_data['asin'] = ident.get('identifier')

            subtitle = vol.get('subtitle', '')
            if subtitle:
                meta_data['subtitle'] = subtitle
                if " of " in subtitle:
                    parts = subtitle.split(" of ")
                    if len(parts) > 1:
                        found_series.add(
                            f"{parts[1].strip()}{self.extract_sequence_number(parts[0])}")

                clean_name = re.sub(
                    r'[,:]?\s*(?:Book|Vol\.?|Volume|#)\s*\d+', '', subtitle, flags=re.IGNORECASE).strip()
                seq_num = self.extract_sequence_number(subtitle)
                if clean_name and len(clean_name) > 2:
                    found_series.add(f"{clean_name}{seq_num}")

            logger.debug(f"Google Books Series extracted: {found_series}")

        # 3. Open Library Extraction
        if ol_results:
            doc = ol_results[0]
            if 'series' in doc:
                for s in doc['series']:
                    found_series.add(s)
            logger.debug(
                f"Open Library Series extracted: {doc.get('series', [])}")

        # 4. Readarr Series
        readarr_series = readarr_payload.get('series', {}).get('title')
        if readarr_series:
            found_series.add(readarr_series)

        # 5. Folder Logic
        if file_path and r_author:
            try:
                path_parts = os.path.normpath(file_path).split(os.sep)
                author_idx = -1
                for i, part in enumerate(path_parts):
                    if part.lower() in ['mnt', 'media', 'audiobooks', 'books']:
                        continue
                    if r_author.lower() in part.lower() or part.lower() in r_author.lower():
                        author_idx = i
                        break

                if author_idx != -1 and author_idx + 1 < len(path_parts):
                    potential_series = path_parts[author_idx + 1]
                    sim = SequenceMatcher(
                        None, potential_series.lower(), str(r_title).lower()).ratio()
                    if sim < 0.6:
                        seq_str = self.extract_sequence_number(
                            path_parts[author_idx + 2] if author_idx + 2 < len(path_parts) else "")
                        found_series.add(f"{potential_series}{seq_str}")
                        logger.debug(
                            f"Folder Logic Series extracted: {potential_series}{seq_str}")
            except Exception as e:
                logger.error(f"Path parsing failed during folder logic: {e}")

        # Format Series Tags
        final_tags = {}
        for tag in found_series:
            match = re.match(r'^(.*?)\s*(#\d+(\.\d+)?)?$', tag)
            if match:
                name = match.group(1).strip()
                seq = match.group(2) or ""
                if name.lower() not in final_tags:
                    final_tags[name.lower()] = f"{name}{seq}"
                elif seq:
                    final_tags[name.lower()] = f"{name}{seq}"

        meta_data['series'] = list(final_tags.values())
        logger.info(f"Final Series List: {meta_data['series']}")

        # Update JSON
        success = self.update_abs_metadata(file_path, meta_data)

        msg = f"Updated metadata.json with {len(meta_data['series'])} series." if success else "Failed to update metadata.json."

        if interaction:
            await interaction.followup.send(f"✅ **Processed!** {msg}")
            try:
                await interaction.message.delete()
            except:
                pass

        await self.trigger_abs_scan()

    def update_abs_metadata(self, file_path: str, new_data: Dict) -> bool:
        """Creates or updates the metadata.json file for Audiobookshelf."""
        try:
            book_dir = os.path.dirname(file_path)
            logger.debug(f"Target Directory: {book_dir}")

            if not os.path.exists(book_dir):
                logger.error(f"Book directory does not exist: {book_dir}")
                return False

            meta_path = os.path.join(book_dir, "metadata.json")
            current_data = {}

            # Read existing
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, 'r', encoding='utf-8') as f:
                        current_data = json.load(f)
                    logger.debug("Existing metadata.json read successfully.")
                except Exception as e:
                    logger.warning(
                        f"Could not read existing metadata.json: {e}")

            # Merge Data
            current_data['series'] = new_data.get('series', [])

            def set_if_missing(key, val):
                if val and (key not in current_data or not current_data[key]):
                    current_data[key] = val

            set_if_missing('title', new_data.get('title'))
            set_if_missing('subtitle', new_data.get('subtitle'))
            set_if_missing('authors', new_data.get('authors'))
            set_if_missing('description', new_data.get('description'))
            set_if_missing('publisher', new_data.get('publisher'))
            set_if_missing('publishedYear', new_data.get('publishedYear'))
            set_if_missing('genres', new_data.get('genres'))
            set_if_missing('isbn', new_data.get('isbn'))
            set_if_missing('asin', new_data.get('asin'))
            set_if_missing('language', new_data.get('language'))

            if 'tags' not in current_data:
                current_data['tags'] = []
            if 'chapters' not in current_data:
                current_data['chapters'] = []

            # Write File
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump(current_data, f, indent=2, ensure_ascii=False)

            # Permissions
            try:
                st = os.stat(book_dir)
                os.chown(meta_path, st.st_uid, st.st_gid)
                os.chmod(meta_path, 0o664)
                logger.debug(
                    f"Set permissions on metadata.json to 664 (Owner: {st.st_uid})")
            except Exception as e:
                logger.debug(f"Permission setting skipped (non-root?): {e}")

            logger.info(f"✅ Successfully wrote metadata.json to {meta_path}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to update metadata.json: {e}")
            return False

    async def trigger_abs_scan(self) -> bool:
        abs_url = os.getenv("ABS_URL")
        abs_token = os.getenv("ABS_TOKEN")
        library_id = os.getenv("ABS_LIBRARY_ID")

        if not all([abs_url, abs_token, library_id]):
            logger.critical(
                "ABS configuration missing (URL, Token, or Library ID).")
            return False

        abs_url = abs_url.rstrip('/')
        url = f"{abs_url}/api/libraries/{library_id}/scan"
        headers = {"Authorization": f"Bearer {abs_token}"}

        logger.debug(f"Triggering ABS scan at: {url}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers) as resp:
                    if resp.status == 200:
                        logger.info(
                            "Triggered Audiobookshelf Library Scan successfully.")
                        return True
                    else:
                        text = await resp.text()
                        logger.error(
                            f"Failed to scan ABS: {resp.status} - {text}")
                        return False
        except Exception as e:
            logger.error(f"ABS Scan connection error: {e}")
            return False


async def setup(bot):
    await bot.add_cog(AudiobookCog(bot))

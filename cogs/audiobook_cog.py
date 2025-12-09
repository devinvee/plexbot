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
                         min_values=1, max_values=1, options=options)
        self.books = books

    async def callback(self, interaction: discord.Interaction):
        selected_index = int(self.values[0])
        selected_book = self.books[selected_index]
        self.view.stop()

        logger.info(
            f"User {interaction.user} selected book index {selected_index}")
        await interaction.response.defer()

        # Pass selected book as both sources to force processing
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
        if books:
            self.add_item(BookSelect(books))

# --- Main Cog ---


class AudiobookCog(commands.Cog, name="Audiobook"):
    def __init__(self, bot):
        self.bot = bot
        self.google_api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
        self.library_root = os.getenv("LIBRARY_ROOT", "/mnt/audiobooks/books")

    # --- Helper: Case-Insensitive Path Finder ---
    def find_case_insensitive_folder(self, root_path: str, target_name: str) -> Optional[str]:
        if not os.path.exists(root_path):
            return None
        try:
            entries = os.listdir(root_path)
            for entry in entries:
                full_path = os.path.join(root_path, entry)
                if os.path.isdir(full_path):
                    if entry.lower() == target_name.lower():
                        return entry
        except Exception:
            pass
        return None

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

    @commands.hybrid_command(name="scanauthor", description="Scan an author's folder and update metadata for all books.")
    @commands.is_owner()
    async def scan_author_command(self, ctx: commands.Context, author_name: str):
        """Recursively scans an author's directory."""
        await ctx.defer()
        actual_author_name = self.find_case_insensitive_folder(
            self.library_root, author_name)

        if not actual_author_name:
            await ctx.send(f"❌ **Author folder not found:** `{author_name}`")
            return

        target_dir = os.path.join(self.library_root, actual_author_name)
        await ctx.send(f"🔍 **Scanning:** `{actual_author_name}`...\nCheck logs for progress.")

        processed_count = 0
        for root, dirs, files in os.walk(target_dir):
            audio_files = [f for f in files if f.lower().endswith(
                ('.m4b', '.mp3', '.m4a', '.flac'))]
            if audio_files:
                target_file = os.path.join(root, audio_files[0])
                folder_name = os.path.basename(root)

                mock_payload = {
                    "eventType": "Rename",
                    "book": {"title": folder_name},
                    "author": {"name": actual_author_name},
                    "sourcePath": target_file
                }

                logger.info(f"Auto-scanning: {folder_name}")
                await self.process_readarr_event(mock_payload)
                processed_count += 1
                await asyncio.sleep(1.5)

        await ctx.send(f"✅ **Scan Complete.** Processed {processed_count} folders for **{actual_author_name}**.")
        await self.trigger_abs_scan()

    @commands.hybrid_command(name="scanbook", description="Scan a specific book folder.")
    @commands.is_owner()
    async def scan_book_command(self, ctx: commands.Context, author: str, book_search: str):
        """Scans a specific book folder."""
        await ctx.defer()
        actual_author_name = self.find_case_insensitive_folder(
            self.library_root, author)
        if not actual_author_name:
            await ctx.send(f"❌ **Author not found:** `{author}`")
            return

        author_dir = os.path.join(self.library_root, actual_author_name)
        target_path = None
        target_file = None

        for root, dirs, files in os.walk(author_dir):
            folder_name = os.path.basename(root)
            if book_search.lower() in folder_name.lower():
                audio_files = [f for f in files if f.lower().endswith(
                    ('.m4b', '.mp3', '.m4a', '.flac'))]
                if audio_files:
                    target_path = root
                    target_file = audio_files[0]
                    break

        if not target_path:
            await ctx.send(f"❌ **Book not found:** `{book_search}`")
            return

        folder_name = os.path.basename(target_path)
        await ctx.send(f"🔍 **Found:** `{folder_name}`\nProcessing...")

        mock_payload = {
            "eventType": "Rename",
            "book": {"title": folder_name},
            "author": {"name": actual_author_name},
            "sourcePath": os.path.join(target_path, target_file)
        }

        await self.process_readarr_event(mock_payload)
        await ctx.send(f"✅ **Metadata Updated** for `{folder_name}`.")

    # --- Core Logic ---

    async def process_readarr_event(self, payload: Dict):
        """Main entry point."""
        try:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"READARR PAYLOAD:\n{json.dumps(payload, indent=4)}")
        except:
            pass

        event_type = payload.get('eventType')
        if event_type == 'Test':
            logger.info("Readarr Test Event.")
            return

        if event_type not in ['Download', 'Upgrade', 'Rename']:
            return

        # 1. Extract Data
        book_info = payload.get('book', {})
        raw_title = book_info.get('title', 'Unknown Title')

        # Cleanup Title: remove [2] or (2021)
        title = re.sub(r'^\[\d+\]\s*', '', raw_title)
        title = re.sub(r'\s*\(\d{4}\).*$', '', title)
        title = title.strip()

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

        if not file_path or not os.path.exists(file_path):
            logger.error(f"❌ File path not found: {file_path}")
            return

        logger.info(f"Processing: {title} by {author}")

        # 3. Parallel API Search
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
            logger.warning(f"No API results for {title}. Using folder logic.")
            match_confidence = 1.0
        else:
            match_title = primary_match.get('volumeInfo', {}).get(
                'title') or primary_match.get('title')
            match_confidence = self.calculate_confidence(
                title, author, match_title)

        # Inject cleaned title
        payload['book']['title'] = title

        if match_confidence > 0.85:
            logger.info(
                f"High confidence ({match_confidence}). Updating metadata.")
            await self.finalize_tagging(None, results_gb, results_ol, file_path, payload)
        elif event_type == 'Rename':
            logger.info(
                f"Low confidence ({match_confidence}) during Rename. Skipping.")
        else:
            logger.info(
                f"Low confidence ({match_confidence}). Requesting approval.")
            await self.request_manual_approval(results_gb or results_ol, title, author, file_path, payload)

    async def search_google_books(self, title: str, author: str) -> List[Dict]:
        import urllib.parse
        q = f"intitle:{urllib.parse.quote(title)}+inauthor:{urllib.parse.quote(author)}"
        url = f"https://www.googleapis.com/books/v1/volumes?q={q}&maxResults=5"
        if self.google_api_key:
            url += f"&key={self.google_api_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return (await resp.json()).get('items', [])
                    return []
        except:
            return []

    async def search_openlibrary(self, title: str, author: str) -> List[Dict]:
        import urllib.parse
        url = f"https://openlibrary.org/search.json?title={urllib.parse.quote(title)}&author={urllib.parse.quote(author)}&limit=5"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return (await resp.json()).get('docs', [])
                    return []
        except:
            return []

    def calculate_confidence(self, r_title, r_author, match_title) -> float:
        if not match_title:
            return 0.0
        r, m = r_title.lower(), match_title.lower()
        if r == m:
            return 1.0
        if r in m or m in r:
            return 0.95
        return SequenceMatcher(None, r, m).ratio()

    async def request_manual_approval(self, results, title, author, file_path, payload):
        channel_id = os.getenv("READARR_CHANNEL_ID")
        if not channel_id:
            return
        try:
            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                return
            view = ManualMatchView(results, file_path, payload, self)
            await channel.send(f"📚 **Metadata Approval Needed:** {title}", view=view)
        except:
            pass

    def extract_sequence_number(self, text: str) -> str:
        """Finds #5, Book 5, [5], etc."""
        if not text:
            return ""
        # Look for [1], (1), #1, Book 1
        match = re.search(
            r'(?:Book|Vol\.?|Volume|#|\[|\()\s*(\d+(\.\d+)?)', text, re.IGNORECASE)
        if match:
            return f" #{match.group(1)}"
        return ""

    async def finalize_tagging(self, interaction, gb_results, ol_results, file_path, readarr_payload):
        logger.info(f"Generating rich metadata for: {file_path}")
        found_series: Set[str] = set()
        meta_data = {}

        # 1. Base Info
        r_book = readarr_payload.get('book', {})
        r_title = r_book.get('title')
        r_author = readarr_payload.get('author', {}).get(
            'name') or r_book.get('authorTitle')

        meta_data['title'] = r_title
        meta_data['authors'] = [r_author] if r_author else []
        meta_data['publishedYear'] = str(r_book.get('releaseDate', ''))[:4]

        # ----------------------------------------------------
        # PRIMARY SEQUENCE LOGIC (From Folder [1])
        # ----------------------------------------------------
        primary_sequence = ""
        readarr_series_name = readarr_payload.get('series', {}).get('title')

        if file_path:
            try:
                # Look specifically for [1] in the folder path
                path_parts = os.path.normpath(file_path).split(os.sep)
                # Reverse iterate to find book folder first (closest to file)
                for part in reversed(path_parts):
                    # Check for [X] pattern
                    match = re.search(r'^\[(\d+(\.\d+)?)\]', part)
                    if match:
                        primary_sequence = f" #{match.group(1)}"
                        logger.info(
                            f"Found Primary Sequence in Folder: {part} -> {primary_sequence}")
                        break
            except:
                pass

        # 2. Add Readarr Series (With sequence if found!)
        if readarr_series_name:
            found_series.add(f"{readarr_series_name}{primary_sequence}")

        # 3. Fallback: If Readarr Series is empty, look at Folder Name parent
        if not readarr_series_name and file_path and r_author:
            try:
                path_parts = os.path.normpath(file_path).split(os.sep)
                author_idx = -1
                for i, part in enumerate(path_parts):
                    if not part:
                        continue
                    if part.lower() in ['mnt', 'media', 'audiobooks', 'books']:
                        continue
                    if r_author.lower() in part.lower():
                        author_idx = i
                        break

                # If we have Author/Series/Book/...
                if author_idx != -1 and author_idx + 1 < len(path_parts):
                    potential_series = path_parts[author_idx + 1]
                    # Check if this folder looks like a series (not the book title)
                    sim = SequenceMatcher(
                        None, potential_series.lower(), str(r_title).lower()).ratio()
                    if sim < 0.6:
                        # It is a series folder!
                        found_series.add(
                            f"{potential_series}{primary_sequence}")
            except:
                pass

        # 4. Google Books & Open Library (Universe/Secondary)
        # Google Books
        if gb_results:
            vol = gb_results[0].get('volumeInfo', {})
            meta_data['description'] = vol.get('description', '')
            meta_data['publisher'] = vol.get('publisher', '')
            meta_data['genres'] = vol.get('categories', [])
            meta_data['language'] = vol.get('language', '')
            meta_data['pageCount'] = vol.get('pageCount')

            for ident in vol.get('industryIdentifiers', []):
                if ident.get('type') == 'ISBN_13':
                    meta_data['isbn'] = ident.get('identifier')
                if ident.get('type') == 'ISBN_10':
                    meta_data['asin'] = ident.get('identifier')

            # Subtitle Analysis for additional series
            subtitle = vol.get('subtitle', '')
            if subtitle:
                meta_data['subtitle'] = subtitle

                # Logic: If subtitle is "Book 1 of Cosmere", and we haven't seen Cosmere...
                clean_subtitle_series = re.sub(
                    r'[,:]?\s*(?:Book|Vol\.?|Volume|#)\s*\d+', '', subtitle, flags=re.IGNORECASE).strip()
                subtitle_seq = self.extract_sequence_number(subtitle)

                if clean_subtitle_series and len(clean_subtitle_series) > 2:
                    # Avoid duplicates of the primary series if names are close
                    is_duplicate = False
                    for s in list(found_series):
                        if clean_subtitle_series.lower() in s.lower():
                            is_duplicate = True
                            break

                    if not is_duplicate:
                        found_series.add(
                            f"{clean_subtitle_series}{subtitle_seq}")

        # Open Library
        if ol_results:
            doc = ol_results[0]
            if 'series' in doc:
                for s in doc['series']:
                    # Add without sequence unless we can smartly deduce it
                    # (Open Library rarely gives sequence in simple search)
                    found_series.add(s)

            if 'author_name' in doc and isinstance(doc['author_name'], list):
                for a in doc['author_name']:
                    if a not in meta_data['authors']:
                        meta_data['authors'].append(a)

        # 5. Narrator Detection
        desc = meta_data.get('description', '')
        narrator_match = re.search(
            r'Narrated by[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)', desc)
        if narrator_match:
            meta_data['narrators'] = [narrator_match.group(1)]

        # --- Final Merge & Clean ---
        meta_data['series'] = list(found_series)
        logger.info(f"Final Series List to Write: {meta_data['series']}")

        # Update JSON
        success = self.update_abs_metadata(file_path, meta_data)
        msg = f"Updated metadata.json for **{r_title}**." if success else "Failed to update metadata.json."

        if interaction:
            await interaction.followup.send(f"✅ {msg}")
            try:
                await interaction.message.delete()
            except:
                pass

        await self.trigger_abs_scan()

    def update_abs_metadata(self, file_path: str, new_data: Dict) -> bool:
        try:
            book_dir = os.path.dirname(file_path)
            meta_path = os.path.join(book_dir, "metadata.json")
            current_data = {}

            if os.path.exists(meta_path):
                try:
                    with open(meta_path, 'r', encoding='utf-8') as f:
                        current_data = json.load(f)
                except:
                    pass

            # Force Series Update (Since our logic is better now)
            current_data['series'] = new_data.get('series', [])

            fields = ['title', 'subtitle', 'authors', 'narrators', 'description',
                      'publisher', 'publishedYear', 'genres', 'isbn', 'asin', 'language', 'pageCount']

            for field in fields:
                val = new_data.get(field)
                if val:
                    current_data[field] = val

            if 'tags' not in current_data:
                current_data['tags'] = []
            if 'chapters' not in current_data:
                current_data['chapters'] = []

            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump(current_data, f, indent=2, ensure_ascii=False)

            try:
                st = os.stat(book_dir)
                os.chown(meta_path, st.st_uid, st.st_gid)
                os.chmod(meta_path, 0o664)
            except:
                pass

            return True
        except Exception as e:
            logger.error(f"❌ Failed to update metadata.json: {e}")
            return False

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

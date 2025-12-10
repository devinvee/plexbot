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
        # Deduplicate books by ID/Title to avoid showing the same option twice
        seen_ids = set()
        unique_books = []

        for book in books:
            # Try to find a unique ID (Google ID or OpenLibrary ID)
            bid = book.get('id') or book.get('key') or book.get('title')
            if bid not in seen_ids:
                seen_ids.add(bid)
                unique_books.append(book)

        self.books_internal = unique_books[:25]  # Discord limit is 25

        for i, book in enumerate(self.books_internal):
            vol = book.get('volumeInfo', {})
            # Handle Open Library vs Google Books structure
            title = vol.get('title') or book.get('title', 'Unknown')

            # Authors
            authors_raw = vol.get('authors') or book.get('author_name', [])
            if isinstance(authors_raw, list):
                authors = ", ".join(authors_raw)[:50]
            else:
                authors = str(authors_raw)[:50]

            # Subtitle/Year for extra context
            year = vol.get('publishedDate', '')[:4] or str(
                book.get('first_publish_year', ''))
            subtitle = vol.get('subtitle', '')

            label = title[:90]
            desc = f"{year} | {authors}"
            if subtitle:
                desc += f" | {subtitle}"

            options.append(discord.SelectOption(
                label=label,
                description=desc[:100],
                value=str(i)
            ))

        super().__init__(placeholder="⚠️ Conflict Detected! Select the correct book...",
                         min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_index = int(self.values[0])
        selected_book = self.books_internal[selected_index]
        self.view.stop()

        logger.info(
            f"User {interaction.user} manually selected: {selected_book.get('title')}")
        await interaction.response.defer()

        # Force the tagger to use the USER SELECTED book, ignoring Readarr's bad data
        await self.view.cog.finalize_tagging(
            interaction,
            [selected_book],  # GB Results
            [selected_book],  # OL Results (pass same obj, logic handles it)
            self.view.file_path,
            self.view.readarr_metadata,
            force_title=selected_book.get('title') or selected_book.get(
                'volumeInfo', {}).get('title')
        )


class ManualMatchView(discord.ui.View):
    def __init__(self, books: List[Dict], file_path: str, readarr_metadata: Dict, cog):
        super().__init__(timeout=None)  # No timeout so it waits for you
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

    @commands.hybrid_command(name="scanauthor", description="Scan an author's folder.")
    @commands.is_owner()
    async def scan_author_command(self, ctx: commands.Context, author_name: str):
        # ... (Same as previous) ...
        await ctx.defer()
        actual_name = self.find_case_insensitive_folder(
            self.library_root, author_name)
        if not actual_name:
            await ctx.send(f"❌ **Author not found:** `{author_name}`")
            return

        target_dir = os.path.join(self.library_root, actual_name)
        await ctx.send(f"🔍 **Scanning:** `{actual_name}`...")

        count = 0
        for root, dirs, files in os.walk(target_dir):
            audio = [f for f in files if f.lower().endswith(('.m4b', '.mp3'))]
            if audio:
                mock_payload = {
                    "eventType": "Rename",
                    "book": {"title": os.path.basename(root)},
                    "author": {"name": actual_name},
                    "sourcePath": os.path.join(root, audio[0])
                }
                await self.process_readarr_event(mock_payload)
                count += 1
                await asyncio.sleep(1)

        await ctx.send(f"✅ **Complete.** Scanned {count} folders.")
        await self.trigger_abs_scan()

    @commands.hybrid_command(name="scanbook", description="Scan a specific book folder.")
    @commands.is_owner()
    async def scan_book_command(self, ctx: commands.Context, author: str, book_search: str):
        # ... (Same as previous) ...
        await ctx.defer()
        actual_author = self.find_case_insensitive_folder(
            self.library_root, author)
        if not actual_author:
            await ctx.send(f"❌ Author not found: {author}")
            return

        target_path = None
        for root, dirs, files in os.walk(os.path.join(self.library_root, actual_author)):
            if book_search.lower() in os.path.basename(root).lower():
                audio = [f for f in files if f.lower().endswith(
                    ('.m4b', '.mp3'))]
                if audio:
                    target_path = os.path.join(root, audio[0])
                    break

        if not target_path:
            await ctx.send(f"❌ Book folder not found for search: `{book_search}`")
            return

        mock_payload = {
            "eventType": "Rename",
            "book": {"title": os.path.basename(os.path.dirname(target_path))},
            "author": {"name": actual_author},
            "sourcePath": target_path
        }
        await self.process_readarr_event(mock_payload)
        await ctx.send(f"✅ Triggered scan for `{os.path.basename(os.path.dirname(target_path))}`")

    # --- Core Logic ---

    async def process_readarr_event(self, payload: Dict):
        """Main entry point."""
        try:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"PAYLOAD:\n{json.dumps(payload, indent=4)}")
        except:
            pass

        event_type = payload.get('eventType')
        if event_type == 'Test':
            return
        if event_type not in ['Download', 'Upgrade', 'Rename']:
            return

        # 1. Extract Basic Data
        book_info = payload.get('book', {})
        readarr_title = book_info.get('title', 'Unknown')

        # Clean title (remove [2], (2020))
        readarr_title_clean = re.sub(r'^\[\d+\]\s*', '', readarr_title)
        readarr_title_clean = re.sub(
            r'\s*\(\d{4}\).*$', '', readarr_title_clean).strip()

        author = payload.get('author', {}).get('name')
        if not author:
            author = book_info.get('authorTitle')

        # 2. Extract File Path AND Scene Name
        file_path = None
        scene_name = None

        if 'bookFile' in payload:
            file_path = payload['bookFile'].get('path')
            scene_name = payload['bookFile'].get('sceneName')
        elif 'bookFiles' in payload and len(payload['bookFiles']) > 0:
            file_path = payload['bookFiles'][0].get('path')
            scene_name = payload['bookFiles'][0].get('sceneName')
        elif 'renamedBookFiles' in payload and len(payload['renamedBookFiles']) > 0:
            file_path = payload['renamedBookFiles'][0].get('path')
            scene_name = payload['renamedBookFiles'][0].get('sceneName')
        elif 'sourcePath' in payload:
            file_path = payload['sourcePath']

        if not file_path or not os.path.exists(file_path):
            logger.error(f"❌ File path not found: {file_path}")
            return

        logger.info(f"Processing: {readarr_title_clean} by {author}")

        # 3. FORENSICS: Clean up the Scene Name
        # If Readarr got the match wrong, this string holds the truth.
        scene_title_clean = ""
        if scene_name:
            # Remove common junk (NMR, Audiobooks, MP3, Author Name at start)
            junk_regex = r"(\(NMR.*)|(\[.*?\])|(\d{3,4}kbps)|(- \d+ -)|(^\s*"+re.escape(
                author)+r"\s*-?)"
            scene_title_clean = re.sub(
                junk_regex, "", scene_name, flags=re.IGNORECASE).strip()
            # Remove "Bk 5" style numbering to leave just the raw title for searching
            # But keep it for comparison
            logger.info(
                f"Forensics - Readarr thinks: '{readarr_title_clean}' | File says: '{scene_title_clean}'")

        # 4. Search BOTH Readarr Title AND Scene Name
        search_tasks = [
            self.search_google_books(readarr_title_clean, author),
            self.search_openlibrary(readarr_title_clean, author)
        ]

        # If Scene Name is significantly different, search for it too!
        if scene_title_clean and SequenceMatcher(None, readarr_title_clean, scene_title_clean).ratio() < 0.6:
            logger.info(
                f"Mismatch detected! Searching for Scene Name: {scene_title_clean}")
            search_tasks.append(self.search_google_books(
                scene_title_clean, author))

        results_lists = await asyncio.gather(*search_tasks)

        # Flatten results
        all_results = []
        for r_list in results_lists:
            all_results.extend(r_list)

        if not all_results:
            logger.warning("No API results found. Using folder/Readarr logic.")
            await self.finalize_tagging(None, [], [], file_path, payload)
            return

        # 5. Verify Match
        # Check if the top result matches Readarr's title
        top_match = all_results[0]
        top_title = top_match.get('volumeInfo', {}).get(
            'title') or top_match.get('title')

        confidence = self.calculate_confidence(
            readarr_title_clean, author, top_title)

        # ALSO check confidence against Scene Name if it exists
        if scene_title_clean:
            scene_confidence = self.calculate_confidence(
                scene_title_clean, author, top_title)
            # If the file matches the search result BETTER than Readarr's title matches the search result...
            if scene_confidence > confidence:
                logger.warning(
                    f"⚠️ SCENE NAME matched better than Readarr Title! ({scene_confidence} vs {confidence})")
                confidence = 0.0  # Force manual approval

        if confidence > 0.85 and event_type != 'Rename':  # Always auto-tag on Rename/Scan commands
            logger.info(f"High confidence ({confidence}). Auto-tagging.")
            await self.finalize_tagging(None, all_results, all_results, file_path, payload)

        elif event_type == 'Rename':
            # On manual scans, we just trust the best API result we found
            await self.finalize_tagging(None, all_results, all_results, file_path, payload)

        else:
            # Low confidence or mismatch -> Ask User
            logger.info("Potential Mismatch. Sending Discord Menu.")
            await self.request_manual_approval(all_results, readarr_title_clean, author, file_path, payload)

    # --- (Helpers search_google_books, search_openlibrary, calculate_confidence unchanged) ---
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

    def extract_sequence_number(self, text: str) -> str:
        if not text:
            return ""
        match = re.search(
            r'(?:Book|Vol\.?|Volume|#|\[|\()\s*(\d+(\.\d+)?)', text, re.IGNORECASE)
        if match:
            return f" #{match.group(1)}"
        return ""

    async def request_manual_approval(self, results, title, author, file_path, payload):
        channel_id = os.getenv("READARR_CHANNEL_ID")
        if not channel_id:
            return
        try:
            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                return
            view = ManualMatchView(results, file_path, payload, self)
            await channel.send(f"⚠️ **Import Mismatch Detected:**\nReadarr says: **{title}**\nFile says something else.\nSelect the correct book:", view=view)
        except:
            pass

    async def finalize_tagging(self, interaction, gb_results, ol_results, file_path, readarr_payload, force_title=None):
        meta_data = {}

        # 1. Determine Title (User override > Google Books > Readarr)
        r_book = readarr_payload.get('book', {})

        # If user selected a specific book from dropdown, force that title
        if force_title:
            meta_data['title'] = force_title
        # Otherwise if we have Google Books result, use that (usually cleaner)
        elif gb_results:
            meta_data['title'] = gb_results[0].get(
                'volumeInfo', {}).get('title')
        # Fallback to Readarr
        else:
            meta_data['title'] = r_book.get('title')

        r_author = readarr_payload.get('author', {}).get(
            'name') or r_book.get('authorTitle')
        meta_data['authors'] = [r_author] if r_author else []
        meta_data['publishedYear'] = str(r_book.get('releaseDate', ''))[:4]

        # 2. Extract Metadata from Best Match (GB/OL)
        found_series: Set[str] = set()

        # Process Google Books Data
        if gb_results:
            vol = gb_results[0].get('volumeInfo', {})
            # Only overwrite if not already set (e.g. via force_title)
            if 'title' not in meta_data:
                meta_data['title'] = vol.get('title')

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

        # Process Open Library Data
        if ol_results:
            doc = ol_results[0]
            if 'series' in doc:
                for s in doc['series']:
                    found_series.add(s)

            if 'author_name' in doc and isinstance(doc['author_name'], list):
                for a in doc['author_name']:
                    if a not in meta_data['authors']:
                        meta_data['authors'].append(a)

        # 3. Readarr Series (Keep as fallback)
        readarr_series = readarr_payload.get('series', {}).get('title')
        if readarr_series:
            found_series.add(readarr_series)

        # 4. Folder Logic
        if file_path and r_author:
            try:
                path_parts = os.path.normpath(file_path).split(os.sep)
                author_idx = -1
                for i, part in enumerate(path_parts):
                    if not part or part.lower() in ['mnt', 'media', 'audiobooks', 'books']:
                        continue
                    if r_author.lower() in part.lower():
                        author_idx = i
                        break

                if author_idx != -1 and author_idx + 1 < len(path_parts):
                    potential_series = path_parts[author_idx + 1]
                    potential_book = path_parts[author_idx +
                                                2] if author_idx + 2 < len(path_parts) else ""

                    sim = SequenceMatcher(None, potential_series.lower(), str(
                        meta_data['title']).lower()).ratio()
                    if sim < 0.6:
                        seq_str = self.extract_sequence_number(potential_book)
                        if not seq_str:
                            seq_str = self.extract_sequence_number(
                                os.path.basename(file_path))
                        found_series.add(f"{potential_series}{seq_str}")
            except:
                pass

        # 5. Format Tags
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

        # Narrators
        desc = meta_data.get('description', '')
        narrator_match = re.search(
            r'Narrated by[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)', desc)
        if narrator_match:
            meta_data['narrators'] = [narrator_match.group(1)]

        # Update
        success = self.update_abs_metadata(file_path, meta_data)

        msg_title = meta_data.get('title', 'Unknown Title')
        msg = f"Updated metadata for **{msg_title}**." if success else "Failed update."

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

            current_data['series'] = new_data.get('series', [])

            for field in ['title', 'subtitle', 'authors', 'narrators', 'description', 'publisher', 'publishedYear', 'genres', 'isbn', 'asin', 'language']:
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
        except:
            return False

    async def trigger_abs_scan(self) -> bool:
        abs_url = os.getenv("ABS_URL")
        abs_token = os.getenv("ABS_TOKEN")
        library_id = os.getenv("ABS_LIBRARY_ID")
        if not all([abs_url, abs_token, library_id]):
            return False

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{abs_url.rstrip('/')}/api/libraries/{library_id}/scan", headers={"Authorization": f"Bearer {abs_token}"}) as resp:
                    return resp.status == 200
        except:
            return False


async def setup(bot):
    await bot.add_cog(AudiobookCog(bot))

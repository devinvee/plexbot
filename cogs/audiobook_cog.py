import discord
from discord.ext import commands
import logging
import os
import aiohttp
import json
import datetime

logger = logging.getLogger(__name__)


class AudiobookCog(commands.Cog, name="Audiobook"):
    def __init__(self, bot):
        self.bot = bot
        self.google_api_key = os.getenv("GOOGLE_BOOKS_API_KEY")

    # --- Utility: Manual ABS Scan (Kept as a useful tool) ---
    @commands.hybrid_command(name="absscan", description="Force a scan of the Audiobookshelf library.")
    @commands.is_owner()
    async def absscan_command(self, ctx: commands.Context):
        """Manually triggers a library scan."""
        await ctx.defer()

        abs_url = os.getenv("ABS_URL")
        abs_token = os.getenv("ABS_TOKEN")
        library_id = os.getenv("ABS_LIBRARY_ID")

        if not all([abs_url, abs_token, library_id]):
            await ctx.send("❌ ABS config missing. Check .env variables.")
            return

        url = f"{abs_url.rstrip('/')}/api/libraries/{library_id}/scan"
        headers = {"Authorization": f"Bearer {abs_token}"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers) as resp:
                    if resp.status == 200:
                        await ctx.send("✅ **Audiobookshelf Scan Initiated.**")
                    else:
                        await ctx.send(f"❌ **Scan Failed.** Status: {resp.status}")
        except Exception as e:
            await ctx.send(f"❌ **Connection Error:** {e}")

    # --- Main Event Handler ---
    async def process_readarr_event(self, payload: dict):
        """Processes Readarr webhooks and sends a Discord embed."""

        event_type = payload.get('eventType')

        # Filter Events: Only notify on new content
        if event_type not in ['Download', 'Upgrade', 'Test']:
            return

        if event_type == 'Test':
            await self.send_test_notification()
            return

        # 1. Extract Basic Info
        book = payload.get('book', {})
        author_obj = payload.get('author', {})

        title = book.get('title', 'Unknown Title')
        author = author_obj.get('name') or book.get(
            'authorTitle', 'Unknown Author')
        overview = book.get('overview', '')

        # 2. Extract File Info (Size, Quality)
        file_quality = "Unknown"
        file_size = "0 MB"
        client = payload.get('downloadClient', 'Unknown Client')

        # Readarr can send 'bookFile' (singular) or 'bookFiles' (plural)
        files_list = []
        if 'bookFile' in payload:
            files_list.append(payload['bookFile'])
        elif 'bookFiles' in payload:
            files_list = payload['bookFiles']

        if files_list:
            # Aggregate size if multiple files
            total_bytes = sum(f.get('size', 0) for f in files_list)
            file_size = self.human_readable_size(total_bytes)

            # Get quality from first file
            file_quality = files_list[0].get('quality', 'Unknown')

        logger.info(f"Readarr Import: {title} by {author} ({file_size})")

        # 3. Fetch Metadata (Cover Image & Description) from Google Books
        # Readarr webhooks don't always send a usable cover URL, so we fetch one.
        gb_data = await self.fetch_google_books_data(title, author)

        # Use Google Books description if Readarr's is empty
        if not overview and gb_data:
            overview = gb_data.get('description', '')

        # 4. Build Embed
        embed = discord.Embed(
            title=f"{title}",
            description=overview[:300] +
            "..." if len(overview) > 300 else overview,
            color=discord.Color.green() if event_type == 'Download' else discord.Color.blue(),
            timestamp=datetime.datetime.now()
        )

        embed.set_author(name=f"{author} - {event_type}",
                         icon_url="https://i.imgur.com/yV83rG6.png")  # Generic book icon

        # Set Cover Image
        if gb_data and gb_data.get('thumbnail'):
            embed.set_thumbnail(url=gb_data['thumbnail'])

        # Add Fields
        embed.add_field(name="Quality", value=file_quality, inline=True)
        embed.add_field(name="Size", value=file_size, inline=True)
        embed.add_field(name="Client", value=client, inline=True)

        if gb_data and gb_data.get('rating'):
            embed.add_field(
                name="Rating", value=f"⭐ {gb_data['rating']}/5", inline=True)

        embed.set_footer(text="Readarr • Audiobooks")

        # 5. Send Notification
        await self.send_notification(embed)

    # --- Helpers ---

    async def send_notification(self, embed):
        channel_id = os.getenv("READARR_CHANNEL_ID")
        if not channel_id:
            logger.warning(
                "READARR_CHANNEL_ID not set. Cannot send notification.")
            return

        try:
            channel = self.bot.get_channel(int(channel_id))
            if channel:
                await channel.send(embed=embed)
            else:
                logger.error(f"Could not find channel with ID {channel_id}")
        except Exception as e:
            logger.error(f"Failed to send Discord message: {e}")

    async def send_test_notification(self):
        embed = discord.Embed(
            title="Test Notification",
            description="Readarr connection is working successfully!",
            color=discord.Color.teal()
        )
        await self.send_notification(embed)

    async def fetch_google_books_data(self, title, author):
        """Fetches cover URL and extra metadata from Google Books."""
        import urllib.parse
        query = f"intitle:{title}+inauthor:{author}"
        url = f"https://www.googleapis.com/books/v1/volumes?q={urllib.parse.quote(query)}&maxResults=1"
        if self.google_api_key:
            url += f"&key={self.google_api_key}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if 'items' in data and len(data['items']) > 0:
                            info = data['items'][0]['volumeInfo']
                            return {
                                'description': info.get('description', ''),
                                'thumbnail': info.get('imageLinks', {}).get('thumbnail'),
                                'rating': info.get('averageRating')
                            }
        except Exception as e:
            logger.error(f"Google Books API Error: {e}")
        return None

    def human_readable_size(self, size, decimal_places=2):
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.{decimal_places}f} {unit}"
            size /= 1024.0
        return f"{size:.{decimal_places}f} PB"


async def setup(bot):
    await bot.add_cog(AudiobookCog(bot))

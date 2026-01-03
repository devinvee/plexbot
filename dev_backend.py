#!/usr/bin/env python3
"""
Standalone Flask server for local development.
This runs just the Flask backend without the Discord bot.
"""
import os
import logging
from dotenv import load_dotenv
from media_watcher_service import app

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

if __name__ == '__main__':
    print("Starting Flask development server...")
    print("Backend API: http://localhost:5000")
    print("Note: This is just the Flask backend. Start the Vite dev server separately.")
    print("Run: cd webui && npm run dev")
    print("\nPress Ctrl+C to stop\n")
    
    # Run Flask in debug mode for development
    app.run(host='0.0.0.0', port=5000, debug=True)


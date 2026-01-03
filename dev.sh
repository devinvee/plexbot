#!/bin/bash

# Local Development Script for PlexBot
# This script runs both the Flask backend and Vite frontend for local development

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}Starting PlexBot Local Development Environment...${NC}\n"

# Check if .env file exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}Warning: .env file not found. Creating from sample.env...${NC}"
    if [ -f sample.env ]; then
        cp sample.env .env
        echo -e "${YELLOW}Please edit .env with your configuration before continuing.${NC}"
    else
        echo -e "${YELLOW}Error: sample.env not found. Please create .env manually.${NC}"
        exit 1
    fi
fi

# Check if Python dependencies are installed
echo -e "${BLUE}Checking Python dependencies...${NC}"
if ! python3 -c "import flask" 2>/dev/null; then
    echo -e "${YELLOW}Installing Python dependencies...${NC}"
    pip3 install -r requirements.txt
fi

# Check if Node dependencies are installed
echo -e "${BLUE}Checking Node dependencies...${NC}"
if [ ! -d "webui/node_modules" ]; then
    echo -e "${YELLOW}Installing Node dependencies...${NC}"
    cd webui
    npm install
    cd ..
fi

# Function to cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}Shutting down development servers...${NC}"
    kill $FLASK_PID $VITE_PID 2>/dev/null || true
    exit
}

trap cleanup SIGINT SIGTERM

# Start Flask backend in background
echo -e "${GREEN}Starting Flask backend on http://localhost:5000${NC}"
python3 -c "
import os
from dotenv import load_dotenv
load_dotenv()
from media_watcher_service import app
app.run(host='0.0.0.0', port=5000, debug=True)
" &
FLASK_PID=$!

# Wait a moment for Flask to start
sleep 2

# Start Vite frontend
echo -e "${GREEN}Starting Vite dev server on http://localhost:5173${NC}"
cd webui
npm run dev &
VITE_PID=$!
cd ..

echo -e "\n${GREEN}✓ Development servers started!${NC}"
echo -e "${BLUE}Frontend: http://localhost:5173${NC}"
echo -e "${BLUE}Backend API: http://localhost:5000${NC}"
echo -e "\n${YELLOW}Press Ctrl+C to stop all servers${NC}\n"

# Wait for both processes
wait


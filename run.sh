#!/bin/bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

echo -e "${CYAN}Checking if MongoDB is running...${RESET}"
if pgrep mongod > /dev/null; then
  echo -e "${GREEN}MongoDB is running on localhost:27017.${RESET}"
else
  echo -e "${RED}MongoDB is not running. Starting MongoDB...${RESET}"
  sudo systemctl start mongod
  if pgrep mongod > /dev/null; then
    echo -e "${GREEN}MongoDB has started successfully.${RESET}"
  else
    echo -e "${RED}Failed to start MongoDB. Please check your setup.${RESET}"
    exit 1
  fi
fi

echo -e "${CYAN}Activating virtual environment...${RESET}"
source venv/bin/activate

echo -e "${CYAN}Starting the Flask app...${RESET}"
python3 main.py

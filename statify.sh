#!/bin/bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

echo -e "${CYAN}Activating virtual environment...${RESET}"
source venv/bin/activate

REQUIRED_PACKAGES=(
  "Flask==3.0.2"
  "spotipy==2.23.0"
  "sqlmodel==0.0.16"
  "pymongo==4.0.1"
  "networkx==3.4.2"
  "python-dotenv==1.0.1"
  "flask-session==0.8.0"
)

for PACKAGE in "${REQUIRED_PACKAGES[@]}"; do
  PACKAGE_NAME=$(echo "$PACKAGE" | cut -d'=' -f1)
  PACKAGE_VERSION=$(echo "$PACKAGE" | cut -d'=' -f3)

  INSTALLED_VERSION=$(pip show "$PACKAGE_NAME" 2>/dev/null | grep Version | awk '{print $2}')

  if [[ "$INSTALLED_VERSION" != "$PACKAGE_VERSION" ]]; then
    echo -e "${YELLOW}Installing or updating ${PACKAGE}${RESET}"
    pip install "$PACKAGE"
  else
    echo -e "${GREEN}${PACKAGE} is already installed.${RESET}"
  fi
done

if pgrep mongod > /dev/null; then
  echo -e "${GREEN}MongoDB is running.${RESET}"
else
  echo -e "${RED}MongoDB is not running. Starting MongoDB...${RESET}"
  sudo systemctl start mongod
  if pgrep mongod > /dev/null; then
    echo -e "${GREEN}MongoDB has started successfully.${RESET}"
  else
    echo -e "${RED}Failed to start MongoDB.${RESET}"
    exit 1
  fi
fi

echo -e "${CYAN}Starting the Flask app...${RESET}"
python3 main.py

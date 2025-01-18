#!/bin/bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

# python3 installation
echo -e "${CYAN}Checking for Python3 installation...${RESET}"
if ! command -v python3 &>/dev/null; then
  echo -e "${YELLOW}Python3 is not installed. Installing now...${RESET}"
  sudo apt update
  sudo apt install -y python3 python3-pip
  echo -e "${GREEN}Python3 and pip have been installed.${RESET}"
else
  echo -e "${GREEN}Python3 is already installed.${RESET}"
fi

# venv setup
echo -e "${CYAN}Setting up Python virtual environment...${RESET}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

# MongoDB installation
echo -e "${CYAN}Adding MongoDB repository...${RESET}"
if ! grep -q "https://repo.mongodb.org/apt/ubuntu" /etc/apt/sources.list.d/mongodb-org-*.list 2>/dev/null; then
  wget -qO - https://www.mongodb.org/static/pgp/server-6.0.asc | sudo apt-key add -
  echo "deb [ arch=amd64,arm64 ] https://repo.mongodb.org/apt/ubuntu $(lsb_release -cs)/mongodb-org/6.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-6.0.list
  sudo apt update
fi

echo -e "${CYAN}Checking if MongoDB is already installed...${RESET}"
if ! command -v mongod &>/dev/null; then
  echo -e "${YELLOW}Installing MongoDB...${RESET}"
  sudo apt install -y mongodb-org
  echo -e "${GREEN}MongoDB has been installed successfully.${RESET}"
else
  echo -e "${GREEN}MongoDB is already installed.${RESET}"
fi

echo -e "${CYAN}Ensuring MongoDB is enabled and starts on boot...${RESET}"
sudo systemctl enable mongod

echo -e "${CYAN}Starting MongoDB service...${RESET}"
sudo systemctl start mongod

if pgrep mongod > /dev/null; then
  echo -e "${GREEN}MongoDB is running on localhost:27017.${RESET}"
else
  echo -e "${RED}Failed to start MongoDB. Please check your setup.${RESET}"
  exit 1
fi

# Python dependencies installation
echo -e "${CYAN}Installing required Python packages...${RESET}"
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

echo -e "${CYAN}Installation complete. MongoDB is running, and all dependencies are installed.${RESET}"

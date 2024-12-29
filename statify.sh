#!/bin/bash

source venv/bin/activate

REQUIRED_PACKAGES=(
  "Flask==3.0.2"
  "spotipy==2.23.0"
  "sqlmodel==0.0.16"
  "pymongo==4.0.1"
  "networkx==3.4.2"
  "python-dotenv==1.0.1"
)

for PACKAGE in "${REQUIRED_PACKAGES[@]}"; do
  PACKAGE_NAME=$(echo "$PACKAGE" | cut -d'=' -f1)
  PACKAGE_VERSION=$(echo "$PACKAGE" | cut -d'=' -f3)

  INSTALLED_VERSION=$(pip show "$PACKAGE_NAME" 2>/dev/null | grep Version | awk '{print $2}')

  if [[ "$INSTALLED_VERSION" != "$PACKAGE_VERSION" ]]; then
    echo "Installing or updating $PACKAGE"
    pip install "$PACKAGE"
  else
    echo "$PACKAGE is already installed."
  fi
done

python3 app/main.py
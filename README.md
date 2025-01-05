# Statify

Statify is a Python application to analyze Spotify playlists with sorting features and graph visualizations.

## Setup Instructions

### Prerequisites

1. Install Python 3.8 or higher.
2. Install MongoDB and ensure it is running on `localhost:27017`.
3. Install the required dependencies listed in `statify.sh` by running the script.

### Create a Spotify App

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/).
2. Log in with your Spotify account.
3. Click on **Create App**.
4. Set the Redirect URI to `http://localhost:5000/callback` in the app settings.
5. Note down the `Client ID` and `Client Secret`.

### Configure Environment Variables

1. Update the .env file with your Spotify app credentials:
   ```makefile
   SPOTIPY_CLIENT_ID=<your_client_id>
   SPOTIPY_CLIENT_SECRET=<your_client_secret>
   SPOTIPY_REDIRECT_URI=http://localhost:5000/callback
   ```

### Running the Application

1. Activate the virtual environment and install dependencies:
   ```bash
   ./statify.sh
   ```

3. Open your browser and go to [http://localhost:5000](http://localhost:5000).






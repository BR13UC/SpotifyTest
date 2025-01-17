# Statify

Statify is a Python application to analyze Spotify playlists with sorting features and graph visualizations.

## Setup Instructions

### Prerequisites

1. Open a shell and execute:
```bash
./install.sh
```
It will install all dependencies and stard mongo db.

### Create a Spotify App

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com).
2. Log in with your Spotify account.
3. Click on **Create App**.
4. Set the Redirect URI to:
```bash
http://localhost:5000/callback
```
5. Note down the `Client ID` and `Client Secret` in the .env file.

```makefile
SPOTIPY_CLIENT_ID=<your_client_id>
SPOTIPY_CLIENT_SECRET=<your_client_secret>
SPOTIPY_REDIRECT_URI=http://localhost:5000/callback
```

### Running the Application

1. Run the app:
   ```bash
   ./run.sh
   ```

2. Open your browser and go to [http://localhost:5000](http://localhost:5000).






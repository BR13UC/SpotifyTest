document.addEventListener('DOMContentLoaded', () => {
    const pageId = document.body.id;

    switch (pageId) {
        case 'sorts':
            console.log("Sort Page");
            SortsPage.init();
            break;
        case 'graphs':
            console.log("Graph Page");
            GraphPage.init();
            break;
        default:
            console.log("Home Page");
            HomePage.init();
            break;
    }
    CommonFeatures.init();
});

const CommonFeatures = {
    init() {
        this.loadProfile();
    },

    loadProfile() {
        fetch('/users/get_profile')
            .then(this.handleResponse)
            .then(data => {
                const profileDiv = document.querySelector('.profile');
                if (profileDiv) {
                    profileDiv.innerHTML = `
                        <img src="${data.images[0]?.url}" alt="Profile Image" style="width: 50px; height: 50px; border-radius: 50%;">
                        <span>${data.display_name}</span>
                    `;
                }
            })
            .catch(error => {
                console.error('Error fetching profile:', error);
                const profileDiv = document.querySelector('.profile');
                if (profileDiv) {
                    profileDiv.innerHTML = `<span>Error loading profile. Please try again later.</span>`;
                }
            });
    },

    handleResponse(response) {
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        return response.json();
    }
};

const HomePage = {
    init() {
        console.log("Initializing Home Page...");
    }
};

const SortsPage = {
    init() {
        console.log("Initializing Sorts Page...");
        this.fetchPlaylists();
        this.fetchSortOptions();
        this.setupSortEvent();
    },

    fetchPlaylists() {
        const playlistSelect = document.getElementById('playlist-select');
        if (playlistSelect) {
            fetch('/playlists/get_playlists')
                .then(CommonFeatures.handleResponse)
                .then(data => {
                    data.playlists.forEach(playlist => {
                        const option = document.createElement('option');
                        option.value = playlist.id;
                        option.textContent = playlist.name;
                        playlistSelect.appendChild(option);
                    });

                    const firstPlaylistId = playlistSelect.value;
                    if (firstPlaylistId) {
                        this.fetchTracksForPlaylist(firstPlaylistId).then(tracks => {
                            if (tracks) {
                                console.log('Fetched tracks for the first playlist:', tracks);
                                window.currentPlaylistTracks = tracks;
                            }
                        });
                    }

                    playlistSelect.addEventListener('change', async () => {
                        const selectedPlaylistId = playlistSelect.value;
                        if (selectedPlaylistId) {
                            const tracks = await this.fetchTracksForPlaylist(selectedPlaylistId);
                            if (tracks) {
                                console.log('Fetched tracks:', tracks);
                                window.currentPlaylistTracks = tracks;
                            }
                        }
                    });
                });
        }
    },

    fetchSortOptions() {
        const sortOptions = document.getElementById('sort-options');
        if (sortOptions) {
            fetch('/sorts/get_sort_options')
                .then(CommonFeatures.handleResponse)
                .then(data => {
                    data.forEach(option => {
                        const optionElement = document.createElement('option');
                        optionElement.value = option.id;
                        optionElement.textContent = option.name;
                        sortOptions.appendChild(optionElement);
                    });
                });
        }
    },

    setupSortEvent() {
        const startSortBtn = document.getElementById('start-sort-btn');
        if (startSortBtn) {
            startSortBtn.addEventListener('click', (e) => {
                e.preventDefault();
                this.sortTracks();
            });
        }
    },

    fetchTracksForPlaylist(playlistId) {
        const spinner = document.getElementById('spinner');
        spinner.style.display = 'block';

        return fetch('/playlists/get_playlist_tracks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ playlist_id: playlistId })
        })
            .then(response => {
                spinner.style.display = 'none';
                if (!response.ok) {
                    throw new Error(`Error fetching tracks: ${response.statusText}`);
                }
                return response.json();
            })
            .then(data => {
                if (data.error) {
                    alert(data.error);
                    return null;
                }
                return data.tracks;
            })
            .catch(error => {
                console.error('Error:', error);
                spinner.style.display = 'none';
            });
    },

    sortTracks() {
        const playlistSelect = document.getElementById('playlist-select');
        const sortOptions = document.getElementById('sort-options');
        const trackTitlesDiv = document.getElementById('track-titles');
        const spinner = document.getElementById('spinner');

        if (playlistSelect && sortOptions) {
            const playlistId = playlistSelect.value;
            const sortOption = sortOptions.value;

            spinner.style.display = 'block';
            trackTitlesDiv.innerHTML = '';

            fetch('/sorts/sort_playlist_tracks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ playlist_id: playlistId, sort_option: sortOption })
            })
                .then(CommonFeatures.handleResponse)
                .then(data => {
                    if (data.error) {
                        trackTitlesDiv.innerHTML = `<div class="error">${data.error}</div>`;
                        return;
                    }
                    trackTitlesDiv.innerHTML = data.tracks.map(track => `<div>${track.track.name}</div>`).join('');
                })
                .finally(() => {
                    spinner.style.display = 'none';
                });
        }
    }
};

const GraphPage = {
    init() {
        console.log("Initializing Graph Page...");
        this.fetchGraphOptions();
        this.setupGenerateGraphButton();
        this.setupExportEvent();
    },

    fetchGraphOptions() {
        const graphOptions = document.getElementById('graph-options');
        if (graphOptions) {
            fetch('/graphs/get_graph_options')
                .then(CommonFeatures.handleResponse)
                .then(data => {
                    data.forEach(option => {
                        const optionElement = document.createElement('option');
                        optionElement.value = option.id;
                        optionElement.textContent = option.name;
                        graphOptions.appendChild(optionElement);
                    });
                });
        }
    },

    setupGenerateGraphButton() {
        const generateGraphBtn = document.getElementById('create-graph-btn');
        if (generateGraphBtn) {
            generateGraphBtn.addEventListener('click', () => {
                this.loadGraphData();
            });
        }
    },

    fetchFollowedArtists() {
        const spinner = document.getElementById('spinner');
        spinner.style.display = 'block';

        fetch('/users/get_followed_artists')
            .then(CommonFeatures.handleResponse)
            .then(data => {
                if (data.error) {
                    console.error('Error fetching liked artists:', data.error);
                    alert('Failed to fetch liked artists.');
                } else {
                    console.log('Fetched liked artists:', data);
                    window.followedArtists = data.artists;
                }
            })
            .catch(error => {
                console.error('Error fetching liked artists:', error);
                alert('An error occurred while fetching liked artists.');
            })
            .finally(() => {
                spinner.style.display = 'none';
            });
    },

    loadGraphData() {
        fetch('/graphs/get_artist_genre_graph_data')
            .then(CommonFeatures.handleResponse)
            .then(data => {
                console.log('Graph data:', data);

                const container = document.getElementById('graph-container');
                if (container) {
                    const options = {
                        nodes: { shape: 'dot', font: { size: 14 } },
                        edges: { arrows: 'to' },
                        physics: { stabilization: false }
                    };

                    new vis.Network(container, data, options);
                }
            })
            .catch(error => {
                console.error('Error fetching graph data:', error);
                const container = document.getElementById('graph-container');
                if (container) {
                    container.innerHTML = `<p style="color: red;">Failed to load graph data. Please try again later.</p>`;
                }
            });
    },

    setupExportEvent() {
        const exportGraphmlBtn = document.getElementById('export-graphml');
        if (exportGraphmlBtn) {
            exportGraphmlBtn.addEventListener('click', () => {
                fetch('/graphs/export_graph/graphml')
                    .then(CommonFeatures.handleResponse)
                    .then(data => {
                        if (data.file_url) {
                            window.location.href = data.file_url;
                        } else {
                            alert(data.error || 'Error exporting GraphML.');
                        }
                    });
            });
        }
    }
};

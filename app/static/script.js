document.addEventListener('DOMContentLoaded', function () {
    const pageId = document.body.id;

    switch (pageId) {
        case 'sort-page':
            initSortPage();
            break;
        case 'graph-page':
            initGraphPage();
            break;
        default:
            initHomePage();
            break;
    }
    initCommonFeatures();
});

function initCommonFeatures() {
    fetch('/get_profile')
        .then(response => response.json())
        .then(data => {
            const profileDiv = document.querySelector('.profile');
            if (profileDiv) {
                profileDiv.innerHTML = `
                    <img src="${data.images[0].url}" alt="Profile Image" style="width: 50px; height: 50px; border-radius: 50%;">
                    <span>${data.display_name}</span>
                `;
            }
        })
        .catch(error => {
            console.error('Error fetching profile:', error);
        });
}

function initHomePage() {
    console.log("Initializing home page...");
}

function initSortPage() {
    console.log("Initializing sort page...");
    const trackTitlesDiv = document.getElementById('track-titles');
    const createPlaylistBtn = document.getElementById('create-playlist-btn');
    const spinner = document.getElementById('spinner');
    const playlistNameInput = document.getElementById('playlist-name');

    const playlistSelect = document.getElementById('playlist-select');
    if (playlistSelect) {
        fetch('/get_playlists')
        .then(response => response.json())
        .then(data => {
            data.playlists.forEach(playlist => {
                const option = document.createElement('option');
                option.value = playlist.id;
                option.textContent = playlist.name;
                playlistSelect.appendChild(option);
            });
        });
    }

    const sortOptions = document.getElementById('sort-options');
    if (sortOptions) {
        fetch('/get_sort_options')
        .then(response => response.json())
        .then(data => {
            data.forEach(option => {
                const optionElement = document.createElement('option');
                optionElement.value = option.id;
                optionElement.textContent = option.name;
                optionElement.setAttribute('data-field', option.field);
                sortOptions.appendChild(optionElement);
            });
        });
    }

    const startSortBtn = document.getElementById('start-sort-btn');
    if (startSortBtn) {
        startSortBtn.addEventListener('click', function (e) {
            e.preventDefault();
            if (playlistSelect && sortOptions) {
                const playlistId = playlistSelect.value;
                const sortOption = sortOptions.value;

                spinner.style.display = 'block';
                trackTitlesDiv.innerHTML = '';

                fetch('/sort_playlist_tracks', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ playlist_id: playlistId, sort_option: sortOption })
                })
                    .then(response => response.json())
                    .then(data => {
                        if (data.error) {
                            console.error(data.error);
                            trackTitlesDiv.innerHTML = `<div class="error">${data.error}</div>`;
                            return;
                        }

                        const tracks = data.tracks;

                        trackTitlesDiv.innerHTML = tracks
                            .map(track => `<div>${track.track.name}</div>`)
                            .join('');
                        // createPlaylistBtn.style.display = 'block';
                        // playlistNameInput.style.display = 'block';
                    })
                    .finally(() => {
                        spinner.style.display = 'none';
                    });
            }
        });
    }
}

function initGraphPage() {
    console.log("Initializing graph page...");

    const graphOptions = document.getElementById('graph-options');
    if (graphOptions) {
        fetch('/get_graph_options')
        .then(response => response.json())
        .then(data => {
            data.forEach(option => {
                const optionElement = document.createElement('option');
                optionElement.value = option.id;
                optionElement.textContent = option.name;
                graphOptions.appendChild(optionElement);
            });
        });
    }

    const container = document.getElementById('graph-container');
    if (container) {
        fetch('/get_artist_genre_graph_data')
            .then(response => response.json())
            .then(data => {
                const options = {
                    nodes: {
                        shape: 'dot',
                        size: 20,
                    },
                    edges: {
                        arrows: 'to',
                    },
                    physics: {
                        stabilization: false,
                    },
                };
                const network = new vis.Network(container, data, options);
            });
    }

    const exportGraphmlBtn = document.getElementById('export-graphml');
    if (exportGraphmlBtn) {
        exportGraphmlBtn.addEventListener('click', () => {
            fetch('/export_graph/graphml')
                .then(response => response.json())
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

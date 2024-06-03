document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('playlist-form');
    const playlistSelect = document.getElementById('playlist-select');
    const sortOptions = document.getElementById('sort-options');
    const trackTitlesDiv = document.getElementById('track-titles');
    const createPlaylistBtn = document.getElementById('create-playlist-btn');
    const startSortBtn = document.getElementById('start-sort-btn');

    // Fetch user profile and update the profile section
    fetch('/get_profile')
        .then(response => response.json())
        .then(data => {
            const profileDiv = document.querySelector('.profile');
            profileDiv.innerHTML = `
                <img src="${data.images[0].url}" alt="Profile Image" style="width: 50px; height: 50px; border-radius: 50%;">
                <span>${data.display_name}</span>
            `;
        });

    // Fetch playlists and populate the playlist dropdown
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

    // Fetch sorting options and populate the sorting dropdown
    fetch('/get_sort_options')
        .then(response => response.json())
        .then(data => {
            data.forEach(option => {
                const optionElement = document.createElement('option');
                optionElement.value = option.id;
                optionElement.textContent = option.name;
                sortOptions.appendChild(optionElement);
            });
        });

    // Fetch and display tracks when the sort button is clicked
    startSortBtn.addEventListener('click', function (e) {
        e.preventDefault();
        const playlistId = playlistSelect.value;
        const sortOption = sortOptions.value;

        fetch('/get_playlist_tracks', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ playlist_id: playlistId, sort_option: sortOption })
        })
        .then(response => response.json())
        .then(data => {
            const sortedTitles = data.sorted_titles;
            trackTitlesDiv.innerHTML = sortedTitles.map(title => `<div>${title}</div>`).join('');
            createPlaylistBtn.style.display = 'block';
        });
    });

    // Create a new playlist with sorted tracks
    createPlaylistBtn.addEventListener('click', function () {
        const sortedTitles = trackTitlesDiv.innerText.split('\n');

        fetch('/create_sorted_playlist', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ track_titles: sortedTitles })
        })
        .then(response => response.text())
        .then(data => {
            alert(data);
        });
    });
});

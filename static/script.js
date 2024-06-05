document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('playlist-form');
    const playlistSelect = document.getElementById('playlist-select');
    const sortOptions = document.getElementById('sort-options');
    const trackTitlesDiv = document.getElementById('track-titles');
    const createPlaylistBtn = document.getElementById('create-playlist-btn');
    const startSortBtn = document.getElementById('start-sort-btn');
    const spinner = document.getElementById('spinner');
    const playlistNameInput = document.getElementById('playlist-name');
    const settingsBtn = document.getElementById('settings-btn');
    const changeThemeBtn = document.getElementById('change-theme-btn');
    const greenThemeBtn = document.getElementById('green-theme-btn');
    const purpleThemeBtn = document.getElementById('purple-theme-btn');


    fetch('/get_profile')
    .then(response => response.json())
    .then(data => {
        const profileDiv = document.querySelector('.profile');
        profileDiv.innerHTML = `
        <img src="${data.images[0].url}" alt="Profile Image" style="width: 50px; height: 50px; border-radius: 50%;">
        <span>${data.display_name}</span>
        `;
    });

    settingsBtn.addEventListener('click', function () {
        window.location.href = '/settings';
    });

    greenThemeBtn.addEventListener('click', function() {
        document.documentElement.style.setProperty('--primary-color', '#1db954');
        document.documentElement.style.setProperty('--secondary-color', '#551E99');
    });

    purpleThemeBtn.addEventListener('click', function() {
        document.documentElement.style.setProperty('--primary-color', '#551E99');
        document.documentElement.style.setProperty('--secondary-color', '#1db954');
    });

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

    startSortBtn.addEventListener('click', function (e) {
        e.preventDefault();
        const playlistId = playlistSelect.value;
        const sortOption = sortOptions.value;

        spinner.style.display = 'block';
        trackTitlesDiv.innerHTML = '';

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
            playlistNameInput.style.display = 'block';
        })
        .finally(() => {
            spinner.style.display = 'none';
        });
    });

    createPlaylistBtn.addEventListener('click', function () {
        const sortedTitles = trackTitlesDiv.innerText.split('\n');
        const playlistName = playlistNameInput.value || 'Sorted Playlist';

        fetch('/create_sorted_playlist', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ track_titles: sortedTitles, playlist_name: playlistName })
        })
        .then(response => response.text())
        .then(data => {
            alert(data);
        });
    });
});

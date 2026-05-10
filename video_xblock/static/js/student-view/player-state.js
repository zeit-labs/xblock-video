/**
 * This part is responsible for loading and saving player state.
 * State includes:
 * - Current time
 * - Playback rate
 * - Volume
 * - Muted
 *
 * State is loaded after VideoJs player is fully initialized.
 * State is saved at certain events.
 */

var PlayerState = function(player, playerState) {
    'use strict';
    var xblockUsageId = getXblockUsageId();

    /** Create hashmap with all transcripts */
    var getTranscipts = function(transcriptsData) {
        var result = {};
        transcriptsData.forEach(function(transcript) {
            result[transcript.lang] = {
                label: transcript.label,
                url: transcript.url
            };
        });
        return result;
    };

    var transcripts = getTranscipts(playerState.transcripts);

    /** Restore default or previously saved player state */
    var setInitialState = function(state) {
        var stateCurrentTime = state.currentTime;
        if (state.maxTimeForProgress) {
            stateCurrentTime = state.maxPlayedTime;
        } else {
            var playbackProgress = localStorage.getItem('playbackProgress');
            if (playbackProgress) {
                playbackProgress = JSON.parse(playbackProgress);
                if (playbackProgress[window.videoPlayerId]) {
                    stateCurrentTime = playbackProgress[window.videoPlayerId];
                }
            }
        }
        if (stateCurrentTime > 0) {
            player.currentTime(stateCurrentTime);
        }
        player.volume(state.volume);
        player.muted(state.muted);
        player.playbackRate(state.playbackRate);
        player.captionsLanguage = state.captionsLanguage;  // eslint-disable-line no-param-reassign
        // To switch off transcripts and captions state if doesn`t have transcripts with current captions language
        if (!transcripts[player.captionsLanguage]) {
            player.captionsEnabled = player.transcriptsEnabled = false; // eslint-disable-line no-param-reassign
        } else {
            player.transcriptsEnabled = state.transcriptsEnabled; // eslint-disable-line no-param-reassign
            player.captionsEnabled = state.captionsEnabled; // eslint-disable-line no-param-reassign
        }
    };

    /**
     * Save player state by posting it in a message to parent frame.
     * Parent frame passes it to a server by calling VideoXBlock.save_state() handler.
     */
    var saveState = function() {
        var playerObj = this;
        var transcriptUrl = getDownloadTranscriptUrl(transcripts, playerObj);

        var newState = {
            volume: playerObj.volume(),
            currentTime: playerObj.ended() ? 0 : playerObj.currentTime(),
            playbackRate: playerObj.playbackRate(),
            muted: playerObj.muted(),
            transcriptsEnabled: playerObj.transcriptsEnabled,
            captionsEnabled: playerObj.captionsEnabled,
            captionsLanguage: playerObj.captionsLanguage
        };
        if (JSON.stringify(newState) !== JSON.stringify(playerState)) {
            console.log('Starting saving player state');  // eslint-disable-line no-console
            playerState = newState; // eslint-disable-line no-param-reassign
            parent.postMessage(
                {
                    action: 'saveState',
                    info: newState,
                    xblockUsageId: xblockUsageId,
                    xblockFullUsageId: getXblockFullUsageId(),
                    downloadTranscriptUrl: transcriptUrl || '#'
                },
                document.location.protocol + '//' + document.location.host
            );
        }
    };

    /**
     *  Save player progress in browser's local storage.
     *  We need it when user is switching between tabs.
     */
    var saveProgressToLocalStore = function() {
        var playerObj = this;
        var playbackProgress;
        playbackProgress = JSON.parse(localStorage.getItem('playbackProgress') || '{}');
        playbackProgress[window.videoPlayerId] = playerObj.ended() ? 0 : playerObj.currentTime();
        localStorage.setItem('playbackProgress', JSON.stringify(playbackProgress));
    };

    setInitialState(playerState);

    player.on('timeupdate', saveProgressToLocalStore);
    player.on('volumechange', saveState);
    player.on('ratechange', saveState);
    player.on('play', saveState);
    player.on('pause', saveState);
    player.on('ended', saveState);
    player.on('transcriptstatechanged', saveState);
    player.on('captionstatechanged', saveState);
    player.on('languagechange', saveState);

    /**
     * Send a watch-progress ping to the parent frame every 5 seconds while playing.
     * The parent frame forwards it to the `update_progress` XBlock handler.
     */
    var PROGRESS_PING_INTERVAL_MS = 5000;
    var progressInterval = null;

    var sendProgressPing = function() {
        var playerObj = player;
        var currentTime = playerObj.currentTime();
        var duration = playerObj.duration();
        if (duration > 0) {
            parent.postMessage(
                {
                    action: 'updateProgress',
                    xblockUsageId: xblockUsageId,
                    xblockFullUsageId: getXblockFullUsageId(),
                    info: {
                        current_time: currentTime,
                        duration: duration
                    }
                },
                document.location.protocol + '//' + document.location.host
            );
        }
    };

    /**
     * Send a guaranteed progress ping via fetch({ keepalive: true }).
     *
     * Regular progress pings use postMessage → parent $.ajax which can be
     * silently dropped if the page is being unloaded (e.g. the user navigates
     * to the next unit immediately after the video ends). This variant sets
     * beacon: true so the parent routes it through fetch({ keepalive: true })
     * instead — a browser-guaranteed delivery path that survives page unload.
     *
     * Use this for end-of-video and page-hide events only.
     */
    var sendBeaconProgressPing = function(currentTime, duration) {
        if (duration > 0) {
            parent.postMessage(
                {
                    action: 'updateProgress',
                    beacon: true,
                    xblockUsageId: xblockUsageId,
                    xblockFullUsageId: getXblockFullUsageId(),
                    info: {
                        current_time: currentTime,
                        duration: duration
                    }
                },
                document.location.protocol + '//' + document.location.host
            );
        }
    };

    player.on('play', function() {
        if (!progressInterval) {
            progressInterval = setInterval(sendProgressPing, PROGRESS_PING_INTERVAL_MS);
        }
    });

    player.on('pause', function() {
        // Send one final ping on pause so progress is not lost between intervals
        sendProgressPing();
        clearInterval(progressInterval);
        progressInterval = null;
    });

    player.on('ended', function() {
        // Send a beacon ping with full duration as current_time to guarantee 100% progress
        // is recorded. We use player.duration() directly (not currentTime()) because Vimeo
        // resets currentTime to 0 before the 'ended' event fires. The beacon flag ensures
        // the parent uses fetch({ keepalive: true }) so this ping survives page navigation
        // that may be triggered immediately after the video ends. The server clamps
        // progress to 1.0, so there is no overshoot risk from sending the exact duration.
        var duration = player.duration();
        sendBeaconProgressPing(duration, duration);
        clearInterval(progressInterval);
        progressInterval = null;
    });

    // Flush progress when the tab becomes hidden (tab switch, browser minimise,
    // or navigation to another page). visibilitychange is more reliable than
    // beforeunload on mobile browsers.
    document.addEventListener('visibilitychange', function() {
        if (document.visibilityState === 'hidden') {
            sendBeaconProgressPing(player.currentTime(), player.duration());
        }
    });

    // Fallback for environments where visibilitychange is not supported or
    // does not fire on navigation (some older desktop browsers).
    window.addEventListener('beforeunload', function() {
        sendBeaconProgressPing(player.currentTime(), player.duration());
    });
};

domReady(function() {
    'use strict';
    videojs(window.videoPlayerId).ready(function() {
        PlayerState(this, window.playerStateObj);
    });
});

from os.path import join

from ovos_plugin_common_play.ocp.status import *
from ovos_utils.gui import GUIInterface
from ovos_utils.log import LOG


class OCPMediaPlayerGUI(GUIInterface):
    def __init__(self):
        # the skill_id is chosen so the namespace matches the regular bus api
        # ie, the gui event "XXX" is sent in the bus as "ovos.common_play.XXX"
        super(OCPMediaPlayerGUI, self).__init__(skill_id="ovos.common_play")

    def bind(self, player):
        self.player = player
        super().set_bus(self.bus)
        self.player.add_event("ovos.common_play.playback_time",
                              self.handle_sync_seekbar)
        self.player.add_event('ovos.common_play.playlist.play',
                              self.handle_play_from_playlist)
        self.player.add_event('ovos.common_play.search.play',
                              self.handle_play_from_search)
        self.player.add_event('ovos.common_play.collection.play',
                              self.handle_play_from_collection)

    @property
    def search_spinner_page(self):
        return join(self.player.res_dir, "ui", "BusyPage.qml")

    @property
    def audio_player_page(self):
        return join(self.player.res_dir, "ui", "OVOSAudioPlayer.qml")

    @property
    def audio_service_page(self):
        return join(self.player.res_dir, "ui", "OVOSSyncPlayer.qml")

    @property
    def video_player_page(self):
        return join(self.player.res_dir, "ui", "OVOSVideoPlayer.qml")

    @property
    def search_page(self):
        return join(self.player.res_dir, "ui", "Disambiguation.qml")

    @property
    def playlist_page(self):
        return join(self.player.res_dir, "ui", "Playlist.qml")

    def shutdown(self):
        self.bus.remove("ovos.common_play.playback_time",
                        self.handle_sync_seekbar)
        super().shutdown()

    # OCPMediaPlayer interface
    def update_current_track(self):
        self["media"] = self.player.now_playing.info
        self["title"] = self.player.now_playing.title
        self["image"] = self.player.now_playing.image
        self["artist"] = self.player.now_playing.artist
        self["bg_image"] = self.player.now_playing.bg_image
        self["duration"] = self.player.now_playing.length
        self["position"] = self.player.now_playing.position

    def update_search_results(self):
        self["searchModel"] = {
            "data": [e.info for e in self.player.disambiguation]
        }

    def update_playlist(self):
        self["playlistModel"] = {
            "data": [e.info for e in self.player.tracks]
        }

    def show_playback_error(self):
        # TODO error page
        self.show_text("Playback error")

    def show_search_spinner(self):
        self.clear()
        self["footer_text"] = "Querying Skills\n\n"
        self.show_page(self.search_spinner_page,
                       override_idle=30)

    def show_player(self):
        to_remove = [self.search_spinner_page]
        if self.player.active_backend == PlaybackType.AUDIO_SERVICE or \
                self.player.settings.force_audioservice:
            page = self.audio_service_page
            to_remove += [self.video_player_page, self.audio_player_page]
        elif self.player.active_backend == PlaybackType.VIDEO:
            page = self.video_player_page
            to_remove += [self.audio_service_page, self.audio_player_page]
        elif self.player.active_backend == PlaybackType.AUDIO:
            page = self.audio_player_page
            to_remove += [self.video_player_page, self.audio_service_page]
        else:  # skill / mpris / undefined
            # TODO ?
            page = self.audio_service_page
            to_remove += [self.video_player_page, self.audio_player_page]

        if self.player.active_backend in [PlaybackType.MPRIS]:
            # external player, no search or playlists
            pages = [page]
            to_remove += [self.search_page, self.playlist_page]
        else:
            pages = [page]
            if len(self.player.disambiguation):
                pages.append(self.search_page)
            else:
                to_remove.append(self.search_page)

            if len(self.player.tracks):
                pages.append(self.playlist_page)
            else:
                to_remove.append(self.playlist_page)

        self.remove_pages([p for p in to_remove if p in self.pages])
        self.show_pages(pages, index=0, override_idle=True,
                        override_animations=True)

    # gui <-> playlists
    def handle_play_from_playlist(self, message):
        LOG.info("Playback requested from playlist results")
        media = message.data["playlistData"]
        self.player.play_media(media)

    def handle_play_from_search(self, message):
        LOG.info("Playback requested from search results")
        media = message.data["playlistData"]
        self.player.play_media(media)

    def handle_play_from_collection(self, message):
        playlist = message.data["playlistData"]
        collection = message.data["collection"]
        media = playlist[0]
        self.player.play_media(media, playlist=playlist,
                               disambiguation=collection)

    # audio service -> gui
    def handle_sync_seekbar(self, message):
        """ event sent by ovos audio backend plugins """
        self["length"] = message.data["length"]
        self["position"] = message.data["position"]

    # media player -> gui
    def handle_end_of_playback(self, message=None):
        self.clear()
        # show media results, release screen after 60 seconds
        self.show_page(self.search_page, override_idle=60)

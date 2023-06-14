import enum
from os.path import join, dirname

from ovos_plugin_common_play.ocp import OCP_ID
from time import sleep
from ovos_bus_client.message import Message
from ovos_config import Configuration
from ovos_utils.events import EventSchedulerInterface
from ovos_utils.gui import GUIInterface
from ovos_utils.log import LOG

from ovos_plugin_common_play.ocp.status import *
from ovos_plugin_common_play.ocp.utils import is_qtav_available
from threading import Timer


class VideoPlayerBackend(str, enum.Enum):
    AUTO = "auto"
    QTAV = "qtav"
    NATIVE = "native"


class OCPMediaPlayerGUI(GUIInterface):
    def __init__(self):
        # the skill_id is chosen so the namespace matches the regular bus api
        # ie, the gui event "XXX" is sent in the bus as "ovos.common_play.XXX"
        gui_config = Configuration().get("gui") or {}
        super(OCPMediaPlayerGUI, self).__init__(skill_id=OCP_ID,
                                                config=gui_config)
        self.ocp_skills = {}  # skill_id: meta
        self.active_extension = gui_config.get("extension", "generic")
        self.notification_timeout = None
        self.search_mode_is_app = False
        self.persist_home_display = False
        self.event_scheduler_interface = None

    def bind(self, player):
        self.player = player
        super().set_bus(self.bus)
        self.player.add_event("ovos.common_play.playback_time",
                              self.handle_sync_seekbar)
        self.player.add_event('ovos.common_play.playlist.play',
                              self.handle_play_from_playlist)
        self.player.add_event('ovos.common_play.search.play',
                              self.handle_play_from_search)
        self.player.add_event('ovos.common_play.skill.play',
                              self.handle_play_skill_featured_media)
        self.event_scheduler_interface = \
            EventSchedulerInterface(skill_id=OCP_ID, bus=self.bus)

    @property
    def video_backend(self):
        return self.player.settings.get("video_player_backend") or \
               VideoPlayerBackend.AUTO

    @property
    def home_screen_page(self):
        return join(self.player.res_dir, "ui", "Home.qml")
    
    @property
    def disambiguation_playlists_page(self):
        return join(self.player.res_dir, "ui", "SuggestionsView.qml")

    @property
    def audio_player_page(self):
        return join(self.player.res_dir, "ui", "OVOSAudioPlayer.qml")

    @property
    def audio_service_page(self):
        return join(self.player.res_dir, "ui", "OVOSSyncPlayer.qml")

    @property
    def video_player_page(self):
        qtav = join(self.player.res_dir, "ui", "OVOSVideoPlayerQtAv.qml")
        native = join(self.player.res_dir, "ui", "OVOSVideoPlayer.qml")
        has_qtav = is_qtav_available()
        if has_qtav:
            LOG.info("QtAV detected")

        if self.video_backend == VideoPlayerBackend.AUTO:
            # detect if qtav is available, if yes use it
            if has_qtav:
                LOG.debug("defaulting to OVOSVideoPlayerQtAv")
                return qtav
            LOG.debug("defaulting to native OVOSVideoPlayer")
        elif self.video_backend == VideoPlayerBackend.QTAV:
            LOG.debug("OVOSVideoPlayerQtAv explicitly configured")
            return qtav
        elif self.video_backend == VideoPlayerBackend.NATIVE:
            LOG.debug("native OVOSVideoPlayer explicitly configured")

        return native

    @property
    def web_player_page(self):
        return join(self.player.res_dir, "ui", "OVOSWebPlayer.qml")

    @property
    def player_loader_page(self):
        return join(self.player.res_dir, "ui", "PlayerLoader.qml")

    def shutdown(self):
        self.bus.remove("ovos.common_play.playback_time",
                        self.handle_sync_seekbar)
        super().shutdown()

    # OCPMediaPlayer interface
    def update_ocp_skills(self):
        skills_cards = [
            {"skill_id": skill["skill_id"],
             "title": skill["skill_name"],
             "image": skill["thumbnail"],
             "media_type": skill.get("media_type") or [MediaType.GENERIC]
        } for skill in self.player.media.get_featured_skills()]
        self["skillCards"] = skills_cards

    def update_seekbar_capabilities(self):
        self["canResume"] = True
        self["canPause"] = True
        self["canPrev"] = self.player.can_prev
        self["canNext"] = self.player.can_next

        if self.player.loop_state == LoopState.NONE:
            self["loopStatus"] = "None"
        elif self.player.loop_state == LoopState.REPEAT_TRACK:
            self["loopStatus"] = "RepeatTrack"
        elif self.player.loop_state == LoopState.REPEAT:
            self["loopStatus"] = "Repeat"

        if self.player.active_backend == PlaybackType.MPRIS:
            self["loopStatus"] = "None"
            self["shuffleStatus"] = False
        else:
            self["shuffleStatus"] = self.player.shuffle

    def update_current_track(self):
        self.update_seekbar_capabilities()

        self["media"] = self.player.now_playing.info
        self["uri"] = self.player.now_playing.uri
        self["title"] = self.player.now_playing.title
        self["image"] = self.player.now_playing.image or \
            join(dirname(__file__), "res/ui/images/ocp.png")
        self["artist"] = self.player.now_playing.artist
        self["bg_image"] = self.player.now_playing.bg_image or \
            join(dirname(__file__), "res/ui/images/ocp_bg.png")
        self["duration"] = self.player.now_playing.length
        self["position"] = self.player.now_playing.position
        # options below control the web player
        # javascript can be executed on page load and page behaviour modified
        # default values provide crude protection against ads and popups
        # TODO default permissive or restrictive?
        self["javascript"] = self.player.now_playing.javascript
        self["javascriptCanOpenWindows"] = False  # TODO allow to be defined per track
        self["allowUrlChange"] = False # TODO allow to be defined per track

    def update_search_results(self):
        self["searchModel"] = {
            "data": [e.infocard for e in self.player.disambiguation]
        }

    def update_playlist(self):
        self["playlistModel"] = {
            "data": [e.infocard for e in self.player.tracks]
        }

    def show_playback_error(self):
        if self.active_extension == "smartspeaker":
            self.display_notification("Sorry, An error occurred while playing media")
            sleep(0.4)
            self.clear_notification()
        else:
            self["footer_text"] = "Sorry, An error occurred while playing media"
            self.remove_search_spinner()

    def manage_display(self, page_requested, timeout=None):
        # Home:
        # The home search page will always be shown at Protocol level index 0
        # This is to ensure that the home is always available to the user
        # regardless of what other pages are currently open
        # Swiping from the player to the left will always show the home page
        
        # The home page will only be in view if the user is not currently playing an active track
        # If the user is playing a track, the player will be shown instead
        # This is to ensure that the user always returns to the player when they are playing a track
        
        # The search_spinner_page has been integrated into the home page as an overlay
        # It will be shown when the user is searching for a track and will be hidden when the search is complete
        # on platforms that don't support the notification system
        
        # Player:
        # Player loader will always be shown at Protocol level index 1
        # The merged playlist and disambiguation pages will always be shown at Protocol level index 2
        
        # If the user has just opened the ocp home page, and nothing was played previously,
        # the player and merged playlist/disambiguation page will not be shown
        
        # If the user has just opened the ocp home page, and a track was previously played,
        # the player and merged playlist/disambiguation page will always be shown
        
        # If the player is not paused or stopped, the player will be shown instead of the home page
        # when ocp is opened
        
        # Timeout is used to ensure that ocp is fully closed once the timeout has expired
        
        sleep(0.2)
        player_status = self.player.state
        state2str = {PlayerState.PLAYING: "Playing", PlayerState.PAUSED: "Paused", PlayerState.STOPPED: "Stopped"}
        self["status"] = state2str[player_status]
        self["app_view_timeout_enabled"] = self.player.app_view_timeout_enabled
        self["app_view_timeout"] = self.player.app_view_timeout_value
        self["app_view_timeout_mode"] = self.player.app_view_timeout_mode

        LOG.debug(f"manage_display: page_requested: {page_requested}")
        LOG.debug(f"manage_display: player_status: {player_status}")
                
        if page_requested == "home":
            self["homepage_index"] = 0
            self["displayBottomBar"] = False
            
            # Check if the skills page has anything to show, only show it if it does
            if self["skillCards"]:
                self["displayBottomBar"] = True

            if player_status == PlayerState.PLAYING:
                self.show_page(self.player_loader_page, override_idle=True, override_animations=True)
            elif player_status == PlayerState.PAUSED:
                self.show_page(self.home_screen_page, override_idle=True, override_animations=True)
            else:
                self.show_page(self.home_screen_page, override_idle=True, override_animations=True)

        elif page_requested == "player":
            self["playerBackend"] = self._get_player_page()
            self.show_pages(self._get_pages_to_display(), 0, override_idle=True, override_animations=True)

        elif page_requested == "playlist":
            self["displaySuggestionBar"] = False
            self._show_suggestion_playlist()
            
            if timeout is not None:
                self.show_page(self.disambiguation_playlists_page, override_idle=timeout, override_animations=True)
            else:
                self.show_page(self.disambiguation_playlists_page, override_idle=True, override_animations=True)
                
        elif page_requested == "disambiguation":
            self["displaySuggestionBar"] = False
            self._show_suggestion_disambiguation()
            
            if timeout is not None:
                self.show_page(self.disambiguation_playlists_page, override_idle=timeout, override_animations=True)
            else:
                self.show_page(self.disambiguation_playlists_page, override_idle=True, override_animations=True)

        if (self.player.app_view_timeout_enabled and page_requested == "player"
                and self.player.app_view_timeout_mode == "all"):
            self.schedule_app_view_timeout()

    def cancel_app_view_timeout(self, restart=False):
        self.event_scheduler_interface.cancel_scheduled_event("ocp_app_view_timer")
        if restart:
            self.schedule_app_view_timeout()

    def schedule_app_view_pause_timeout(self):
        if (self.player.app_view_timeout_enabled
            and self.player.app_view_timeout_mode == "pause"
                and self.player.state == PlayerState.PAUSED):
            self.schedule_app_view_timeout()

    def schedule_app_view_timeout(self):
        self.event_scheduler_interface.schedule_event(
            self.timeout_app_view, self.player.app_view_timeout_value, data=None, name="ocp_app_view_timer")

    def timeout_app_view(self):
        self.bus.emit(Message("homescreen.manager.show_active"))

    def unload_player_loader(self):
        self.send_event("ocp.gui.player.loader.clear")

    def show_home(self, app_mode=True):
        self.update_ocp_skills()
        self.clear_notification()

        sleep(0.2)
        self.manage_display("home")

        if app_mode:
            self.persist_home_display = True
        else:
            self.persist_home_display = False

        if (self.player.state == PlayerState.PLAYING and self.player.app_view_timeout_enabled
                and self.player.app_view_timeout_mode == "all"):
            self.schedule_app_view_timeout()

    def release(self):
        self.clear()
        super().release()

    def show_player(self):
        # Always clear the spinner and notification before showing the player
        self.persist_home_display = True
        self.remove_search_spinner()
        self.clear_notification()

        check_backend = self._get_player_page()
        if self.get("playerBackend", "") != check_backend:
            self.unload_player_loader()

        sleep(0.2)
        self.manage_display("player")

    # page helpers
    def _get_player_page(self):
        if self.player.active_backend == PlaybackType.AUDIO_SERVICE or \
                self.player.settings.get("force_audioservice", False):
            return self.audio_service_page
        elif self.player.active_backend == PlaybackType.VIDEO:
            return self.video_player_page
        elif self.player.active_backend == PlaybackType.AUDIO:
            return self.audio_player_page
        elif self.player.active_backend == PlaybackType.WEBVIEW:
            return self.web_player_page
        elif self.player.active_backend == PlaybackType.MPRIS:
            return self.audio_service_page
        else:  # external playback (eg. skill)
            # TODO ?
            return self.audio_service_page

    def _get_pages_to_display(self):
        # determine pages to be shown
        self["playerBackend"] = self._get_player_page()
        LOG.debug(f"pages to display backend: {self['playerBackend']}")
        
        if len(self.player.disambiguation):
            self["displaySuggestionBar"] = False
            self._show_suggestion_disambiguation()
            
        if len(self.player.tracks):
            self["displaySuggestionBar"] = False
            self._show_suggestion_playlist()

        if len(self.player.disambiguation) and len(self.player.tracks):   
            self["displaySuggestionBar"] = True
            self._show_suggestion_playlist()
            
        pages = [self.player_loader_page, self.disambiguation_playlists_page]

        return pages

    def _show_home_search(self):
        self.send_event("ocp.gui.show.home.view.search")

    def _show_home_skills(self):
        self.send_event("ocp.gui.show.home.view.skills")

    def _show_suggestion_playlist(self):
        self.send_event("ocp.gui.show.suggestion.view.playlist")
     
    def _show_suggestion_disambiguation(self):
        self.send_event("ocp.gui.show.suggestion.view.disambiguation")

    # gui <-> playlists
    def handle_play_from_playlist(self, message):
        LOG.debug("Playback requested from playlist results")
        media = message.data["playlistData"]
        for track in self.player.playlist:
            if track == media:  # found track
                self.player.play_media(track)
                break
        else:
            LOG.error("Track is not part of loaded playlist!")

    def handle_play_from_search(self, message):
        LOG.debug("Playback requested from search results")
        media = message.data["playlistData"]        
        for track in self.player.disambiguation:
            if track == media:  # found track
                self.player.play_media(track)
                break
        else:
            LOG.error("Track is not part of search results!")

    def handle_play_skill_featured_media(self, message):
        skill_id = message.data["skill_id"]
        LOG.debug(f"Featured Media request: {skill_id}")
        playlist = message.data["playlist"]

        self.player.playlist.clear()
        self.player.media.replace(playlist)
        self["displaySuggestionBar"] = False

        self.manage_display("disambiguation")

        # Model and page are heavy wait for them to load
        # Calling event has no listners on first boot
        # As page has never been loaded in GUI stack before
        sleep(1.5)
        self._show_suggestion_disambiguation()

    # audio_only service -> gui
    def handle_sync_seekbar(self, message):
        """ event sent by ovos audio_only backend plugins """
        self["length"] = message.data["length"]
        self["position"] = message.data["position"]

    # media player -> gui
    def handle_end_of_playback(self, message=None):
        show_results = False
        try:
            if len(self["searchModel"]["data"]):
                show_results = True
        except:
            pass

        # show search results, release screen after 60 seconds
        if show_results:
            self.manage_display("playlist", timeout=60)
            
    def display_notification(self, text, style="info"):
        """ Display a notification on the screen instead of spinner on platform that support it """
        self.show_controlled_notification(text, style=style)
        self.reset_timeout_notification()

    def clear_notification(self):
        """ Remove the notification on the screen """
        if self.notification_timeout:
            self.notification_timeout.cancel()
        self.remove_controlled_notification()

    def start_timeout_notification(self):
        """ Remove the notification on the screen after 1 minute of inactivity """
        self.notification_timeout = Timer(60, self.clear_notification).start()

    def reset_timeout_notification(self):
        """ Reset the timer to remove the notification """
        if self.notification_timeout:
            self.notification_timeout.cancel()
        self.start_timeout_notification()

    def show_search_spinner(self, persist_home=False):
        self.show_home(app_mode=persist_home)
        sleep(0.2)
        self.send_event("ocp.gui.show.busy.overlay")
        self["footer_text"] = "Querying Skills\n\n"

    def remove_search_spinner(self):
        self.send_event("ocp.gui.hide.busy.overlay")
        
    def remove_homescreen(self):
        self.release()


class OCPExternalGuiInterface(GUIInterface):
    def __init__(self, skill_id):
        super(OCPExternalGuiInterface, self).__init__(skill_id=skill_id)
        self.ocp_registered_pages = []

    # Allow the skill to handle the display, this is useful for skills that want to run as standalone apps
    # and not be part of the OCP GUI

    # All skills that use this interface must implement the following pages that will override the OCP GUI
    # pages:
    # - Home / Login / Search, this is the page that will be shown when the skill is launched
    # - Player / Player Loaders, this is the page that will be shown when the skill is launched and the skill is playing
    # - Extra / Disambiguation / Playlist, this is the page that will be shown when the skill is launched and the skill is playing
    # - Custom, allow the skill to show any custom page it wants
    # Page management lifecycle will be handled by the skill itself
    
    def bind(self, player):
        self.player = player
        super().set_bus(self.bus)

    def register_screen_type(self, page_url, page_type):
        for page in self.ocp_registered_pages:
            if page["type"] == page_type:
                return
    
        page_to_register = {
            "page_url": page_url,
            "type": page_type
        }
        self.ocp_registered_pages[page_type] = page_to_register
    
    def get_screen_type(self, page_type):
        return self.ocp_registered_pages[page_type]
    
    def show_screen(self, page_type, override_idle=False, override_animations=False):
        page_to_show = self.get_screen_type(page_type)
        self.show_page(page_to_show["page_url"], override_idle=override_idle, override_animations=override_animations)
    
    def show_home(self, override_idle=False, override_animations=False):
        self.show_screen("home", override_idle, override_animations)
    
    def show_player(self, override_idle=False, override_animations=False):
        self.show_screen("player", override_idle, override_animations)
    
    def show_extra(self, override_idle=False, override_animations=False):
        self.show_screen("extra", override_idle, override_animations)
        
    def remove_screen(self, page_type):
        page_to_remove = self.get_screen_type(page_type)
        self.remove_page(page_to_remove["page_url"])
    
    def remove_home(self):
        self.remove_screen("home")
    
    def remove_player(self):
        self.remove_screen("player")
    
    def remove_extra(self):
        self.remove_screen("extra")

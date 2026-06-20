import time
import queue
import sys
from threading import Thread, Event

class DiscordRPCManager:
    def __init__(self, client_id="1517667151920496821", enabled=True):
        self.client_id = client_id
        self.enabled = enabled
        self._queue = queue.Queue()
        self._stop_event = Event()
        self._thread = None
        self._client = None
        self._connected = False
        self._last_payload = None

    def _log(self, msg):
        try:
            import xbmc
            xbmc.log(msg)
        except Exception:
            pass
        print(msg, file=sys.stderr)


    def start(self):
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = Thread(target=self._run, daemon=True, name="DiscordRPCWorker")
        self._thread.start()

    def update_config(self, enabled, client_id):
        old_enabled = self.enabled
        old_client_id = self.client_id
        self.enabled = enabled
        self.client_id = client_id

        # If client ID changed or disabled, force disconnect
        if not enabled or client_id != old_client_id:
            self._queue.put({"action": "disconnect"})
        
        if enabled and (not old_enabled or client_id != old_client_id):
            self._queue.put({"action": "connect"})

    def set_status(self, state, details, is_watching=False, start_time=None, end_time=None):
        if not self.enabled:
            return
            
        from lib.ff.settings import settings
        
        # Check granular permissions
        if is_watching:
            show_watching = settings.getString("tui.discord_show_watching") != "false"
            show_time = settings.getString("tui.discord_show_time") != "false"
            
            final_state = state
            final_details = details if show_watching else "Ogląda wideo"
            final_start = start_time if show_time else None
            final_end = end_time if show_time else None
        else:
            is_scraping = state == "Szuka źródeł"
            if is_scraping:
                show_scraping = settings.getString("tui.discord_show_scraping") != "false"
                if not show_scraping:
                    self.clear_status()
                    return
                final_state = state
                final_details = details
            else: # Menu browsing
                show_menu = settings.getString("tui.discord_show_menu") != "false"
                if not show_menu:
                    self.clear_status()
                    return
                final_state = state
                final_details = details
            
            final_start = None
            final_end = None

        payload = {
            "action": "update",
            "state": final_state,
            "details": final_details,
            "is_watching": is_watching,
            "start_time": final_start,
            "end_time": final_end,
            "timestamp": time.time()
        }
        self._last_payload = payload
        self._queue.put(payload)

    def clear_status(self):
        self._last_payload = None
        if not self.enabled:
            return
        self._queue.put({"action": "clear"})

    def shutdown(self):
        self._last_payload = None
        self._stop_event.set()
        self._queue.put({"action": "shutdown"})
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._connected = False
        self._client = None

    def _run(self):
        last_connect_attempt = 0
        connect_cooldown = 15.0 # seconds between retries

        while not self._stop_event.is_set():
            try:
                # Handle connection if enabled and not connected
                if self.enabled and not self._connected:
                    now = time.time()
                    if now - last_connect_attempt >= connect_cooldown:
                        last_connect_attempt = now
                        self._connect_client()

                # Process queue items
                try:
                    # Timeout periodically to check connection and stop events
                    item = self._queue.get(timeout=2.0)
                except queue.Empty:
                    continue

                action = item.get("action")
                if action == "shutdown":
                    self._disconnect_client()
                    break
                elif action == "disconnect":
                    self._disconnect_client()
                elif action == "connect":
                    self._disconnect_client()
                    self._connect_client()
                elif action == "clear":
                    if self._connected and self._client:
                        try:
                            self._client.clear()
                        except Exception as e:
                            self._log(f"[Discord RPC] Clear failed: {type(e).__name__} - {e}")
                            self._disconnect_client()
                elif action == "update" and self.enabled:
                    if self._connected and self._client:
                        # Only apply the newest update in queue to prevent backlog lag
                        current_item = item
                        while not self._queue.empty():
                            try:
                                next_item = self._queue.get_nowait()
                                if next_item.get("action") == "update":
                                    current_item = next_item
                                else:
                                    # Put back or process immediate control commands
                                    self._queue.put(next_item)
                                    break
                            except queue.Empty:
                                break

                        try:
                            from lib.ff.settings import settings
                            show_images = settings.getString("tui.discord_show_images") == "true"

                            state_str = current_item["state"]
                            if state_str and len(state_str) < 2:
                                state_str = state_str.ljust(2)
                            details_str = current_item["details"]
                            if details_str and len(details_str) < 2:
                                details_str = details_str.ljust(2)

                            kwargs = {
                                "state": state_str[:128] if state_str else None,
                                "details": details_str[:128] if details_str else None,
                            }
                            if show_images:
                                kwargs["large_image"] = "icon"
                                kwargs["large_text"] = "FanFilm TUI"
                                if current_item.get("is_watching"):
                                    kwargs["small_image"] = "play"
                                    kwargs["small_text"] = "Odtwarzanie"
                                else:
                                    kwargs["small_image"] = "menu"
                                    kwargs["small_text"] = "Menu"

                            if current_item.get("start_time"):
                                kwargs["start"] = int(current_item["start_time"])
                            if current_item.get("end_time"):
                                kwargs["end"] = int(current_item["end_time"])

                            self._log(f"[Discord RPC] Updating status: state={kwargs.get('state')!r}, details={kwargs.get('details')!r}, images={show_images}")
                            self._client.update(**kwargs)
                            self._log(f"[Discord RPC] Status updated successfully!")
                        except Exception as e:
                            # If update fails, assume disconnected and clean up
                            self._log(f"[Discord RPC] Update failed: {type(e).__name__} - {e}")
                            self._disconnect_client()
                
                self._queue.task_done()

            except Exception as e:
                # Top level error guard for thread safety
                try:
                    self._log(f"Error in Discord RPC worker loop: {e}")
                except Exception:
                    pass
                time.sleep(1.0)

    def _connect_client(self):
        if not self.enabled or not self.client_id:
            return
        try:
            from pypresence import Presence
            self._log(f"[Discord RPC] Connecting to Discord client with ID: {self.client_id}...")
            self._client = Presence(self.client_id)
            self._client.connect()
            self._connected = True
            self._log(f"[Discord RPC] Connected successfully!")
            # Resend last status on successful connection
            if self._last_payload:
                self._log(f"[Discord RPC] Resending last status on successful connect")
                self._queue.put(self._last_payload)
        except Exception as e:
            self._connected = False
            self._client = None
            self._log(f"[Discord RPC] Connection failed: {type(e).__name__} - {e}")

    def _disconnect_client(self):
        if self._client:
            try:
                self._log(f"[Discord RPC] Closing connection...")
                self._client.close()
            except Exception as e:
                self._log(f"[Discord RPC] Exception during connection close: {e}")
        self._client = None
        self._connected = False

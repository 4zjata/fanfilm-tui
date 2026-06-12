#!/usr/bin/env python3
import tui.environment # This MUST be imported first to setup sys.path and mock Kodi components
from tui.app import FanFilmApp

if __name__ == "__main__":
    app = FanFilmApp()
    app.run()

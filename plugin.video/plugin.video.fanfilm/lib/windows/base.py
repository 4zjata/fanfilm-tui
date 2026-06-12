from __future__ import annotations
from typing import Optional, Union, Any, Tuple, Dict, Sequence, Callable, Type, ClassVar, cast, TYPE_CHECKING
from typing_extensions import Self, TypeVar, Generic, Literal
from threading import Thread, Condition
from pathlib import Path
from datetime import datetime, timezone
from attrs import define, frozen, field
from xbmcgui import WindowXML, WindowXMLDialog, Control, ControlList, ControlImage
from xbmcgui import (
    ACTION_PARENT_DIR, ACTION_PREVIOUS_MENU, ACTION_STOP, ACTION_NAV_BACK,
    ACTION_MOUSE_RIGHT_CLICK, ACTION_MOUSE_LONG_CLICK, ACTION_CONTEXT_MENU,
)
from ..ff import control
from ..ff.tricks import MISSING, MissingType
from ..ff.threads import Queue
from ..ff.log_utils import fflog, fflog_exc
# from ..ff.debug import logtime
from const import const
if TYPE_CHECKING:
    from xbmcgui import ListItem, Action
    from .gui import CustomXmlData, SwitchState, CustomXmlRequest


T = TypeVar('T')
B = TypeVar('B', 'BaseWindow', 'BaseDialog')
Args = Tuple[Type[T], ...]
KwArgs = Dict[str, Any]
RESULT = TypeVar('RESULT')

Errors = Literal['strict', 'ignore']

#: Type letter in edit-control (on Linux).
ACTION_EDIT_TYPING = 61952
ACTION_EDIT_BACKSPACE = 61448
ACTION_EDIT_DELETE = 61575
EDIT_ACTIONS = {
    ACTION_EDIT_TYPING,
    ACTION_EDIT_BACKSPACE,
    ACTION_EDIT_DELETE,
}

#: Actions (keys) to close / cancel / escape.
CANCEL_ACTIONS = {
    ACTION_PARENT_DIR,
    ACTION_PREVIOUS_MENU,
    # ACTION_PAUSE,
    ACTION_STOP,
    ACTION_NAV_BACK,
}

#: Actions (keys) for context-menu.
MENU_ACTIONS = {
    ACTION_MOUSE_RIGHT_CLICK,
    ACTION_MOUSE_LONG_CLICK,
    ACTION_CONTEXT_MENU,
}


@frozen
class WindowCommand:
    method: Callable
    args: Args
    kwargs: KwArgs


class WindowThread(Thread, Generic[B]):
    """Thread to call window's do_modal() in thread (non blocking)."""

    def __init__(self,
                 win_class: Type[B],
                 name: Optional[str] = None,
                 args: Args = (),
                 kwargs: KwArgs = {},  # noqa: B006
                 ) -> None:
        super().__init__(name=name)
        #: Window class to create the window.
        self.win_class = win_class
        #: Arguments for window create.
        self.args: Args = args
        #: Keyword arguments for window create.
        self.kwargs: KwArgs = kwargs
        #: The window.
        self.win: B | None = None
        #: Thread condition variable for window creation.
        self.win_ready = Condition()
        #: Window commands (do_modal/doModal) queue.
        self.win_commands: Queue[WindowCommand | None] = Queue()

    def run(self) -> None:
        """Do job. Main thread proc."""
        if const.debug.log_gui:
            fflog.debug(f'••• [TH] create win {self.win_class}, {self.args}, {self.kwargs}')
        self.win = self.win_class(*self.args, **self.kwargs)
        try:
            if const.debug.log_gui:
                fflog.debug('••• [TH] notify')
            with self.win_ready:
                self.win_ready.notify()
            while True:
                fflog.debug('••• [TH] wait for command')
                cmd = self.win_commands.get()
                fflog.debug(f'••• [TH] {cmd=}')
                if not cmd:
                    break
                cmd.method(*cmd.args, **cmd.kwargs)
                self.win_commands.task_done()
        finally:
            if const.debug.log_gui:
                fflog.debug('••• [TH] close')
            self.win.close()
        if const.debug.log_gui:
            fflog.debug('••• [TH] finished')

    def command(self, method: Callable, *args, **kwargs) -> None:
        """Postpone window method call."""
        self.win_commands.put(WindowCommand(method, args, kwargs))

    def stop(self) -> None:
        """Stop the window thread."""
        self.win_commands.put(None)


class _WindowMetaClass(type):
    """Base Window meta-class to modify __init__ arguments."""

    def __call__(self, *args, **kwargs):
        return super().__call__(*args, **kwargs).__wrapped__


@frozen
class XmlWindowsArgs:
    xml_filename: str
    script_path: str
    default_skin: str
    default_res: str
    xml_source: Path
    xml_path: Path
    customized_xml: bool
    custom_data: CustomXmlData


class AbstractWindow:
    """Mix-in class for Window and Dialog with custom XML support."""

    XML: ClassVar[str] = ''
    CUSTOMIZED_XML: ClassVar[bool] = False

    if TYPE_CHECKING:
        _customised_xml: bool
        _customised_data: CustomXmlData

        def getControl(self, iControlId: int) -> Control: ...
        def setFocus(self, pControl: Control) -> None: ...
        def setFocusId(self, iControlId: int) -> None: ...

    @classmethod
    def xml_request(cls, **kwargs) -> CustomXmlRequest | None:
        """Get custom XML request for window/dialog."""
        return kwargs.pop('xml_request', None)

    @classmethod
    def _resolve_args(cls,
                      xmlFilename: Optional[str] = None,
                      scriptPath: Optional[str] = None,
                      defaultSkin: Optional[str] = None,
                      defaultRes: Optional[str] = None,
                      *args,
                      **kwargs,
                      ) -> XmlWindowsArgs:
        if xmlFilename is None:
            xmlFilename = cls.XML
        if scriptPath is None:
            scriptPath = control.addonPath
        if defaultSkin is None:
            defaultSkin = 'Default'
        if defaultRes is None:
            defaultRes = '1080i'  # kodi default: 720p
        xml_source = Path(scriptPath) / 'resources' / 'skins' / defaultSkin / defaultRes / xmlFilename

        if customized_xml := kwargs.pop('customized_xml', cls.CUSTOMIZED_XML):
            now = datetime.now(timezone.utc)
            xmlFilename = const.tune.gui.xml_output_filename.format(xml_source.name, name=xml_source.name, stem=xml_source.stem,
                                                                    suffix=xml_source.suffix, suffixes=xml_source.suffixes,
                                                                    path=xml_source, parent=xml_source.parent, folder=xml_source.parent,
                                                                    now=now, date=now.date(), time=now.time(), timestamp=int(now.timestamp()))
            xml_path = Path(scriptPath) / 'resources' / 'skins' / defaultSkin / defaultRes / xmlFilename
            custom_data = _customize_xml(xml_source, xml_path, request=cls.xml_request(**kwargs))
        else:
            from .gui import CustomXmlData
            xml_path = xml_source
            custom_data = CustomXmlData()
        return XmlWindowsArgs(xmlFilename, scriptPath, defaultSkin, defaultRes, xml_source, xml_path, customized_xml, custom_data)

    def on_closing(self) -> bool | None:
        """Custom function called when window going to close. Custom callback. Return False to cancel software closing."""

    def on_close(self) -> None:
        """Custom function called on window close. Custom callback."""

    def on_init(self) -> None:
        """Kodi call it on window initialization, access to XML controls is allowed from now. Custom callback."""

    def default_action(self, action: Action) -> bool:
        """Handle default action."""
        action_id = action.getId()
        if action_id in CANCEL_ACTIONS:
            self.close()
            return True
        return False

    def on_action(self, action: Action) -> None:
        """Kodi sent the action. Custom callback."""
        self.default_action(action)

    def on_click(self, control_id: int) -> None:
        """Kodi sent the control's click. Custom callback."""

    def on_focus(self, control_id: int) -> None:
        """Kodi sent the control's focus. Custom callback."""

    def on_control(self, control: Control) -> None:
        """Kodi sent all click events on owned and selected controls when the control itself doesn't handle the message. Custom callback."""

    def on_switch_changed(self, control_id: int, state: SwitchState) -> None:
        """FanFilm sent the switch's state change. Custom callback."""

    def on_notification(self, sender: str, method: str, data: str) -> None:
        """Kodi notification handler. Custom callback."""

    def on_edit_finished(self, control_id: int) -> None:
        """FanFilm sent the edit control finished editing. Custom callback."""

    def on_number_button(self, button: int) -> bool:
        """FanFilm sent the number button pressed. Custom callback. Return True if handled."""
        return False

    def _base_on_notification(self, sender: str, method: str, data: str) -> None:
        """Kodi notification handler."""
        if const.debug.log_gui:
            fflog(f' +++++++++ onNotification: {sender=}, {method=}, {data=}')
        self.on_notification(sender, method, data)
        if sender == 'xbmc' and method == 'Input.OnInputFinished':
            self.on_edit_finished(self.focused_id())

    def _already_closed(self) -> None:
        """Cleanup if already closed (by Kodi)."""
        from ..ff.kotools import KodiMonitor
        KodiMonitor.instance().remove_watcher(self._base_on_notification)
        self.on_close()

    def close(self) -> None:
        from ..ff.kotools import KodiMonitor
        self.on_closing()
        KodiMonitor.instance().remove_watcher(self._base_on_notification)
        super().close()  # type: ignore[reportAttributeAccessIssue]
        self.on_close()

    def onInit(self) -> None:
        """Kodi call it on window initialization, access to XML controls is allowed from now."""
        from ..ff.kotools import KodiMonitor
        if const.debug.log_gui:
            fflog(' +++++++++ onInit:')
        self.on_init()
        KodiMonitor.instance().add_watcher(self._base_on_notification)

    def onAction(self, action: Action) -> None:
        """Kodi sent the action."""
        import xbmcgui
        if const.debug.log_gui:
            fflog(f' +++++++++ onAction: id = {action.getId()!r}, amount = {action.getAmount1()!r}, {action.getAmount2()!r},'
                  f' button = {action.getButtonCode()!r}')
        aid = action.getId()
        if xbmcgui.ACTION_JUMP_SMS2 <= aid <= xbmcgui.ACTION_JUMP_SMS9:
            btn = aid - xbmcgui.ACTION_JUMP_SMS2 + 2
        elif xbmcgui.REMOTE_0 <= aid <= xbmcgui.REMOTE_9:
            btn = aid - xbmcgui.REMOTE_0
        else:
            btn = None
        if btn is not None:
            if const.debug.log_gui:
                fflog(f' +++++++++ onAction / on_number_button: {btn = }')
            if self.on_number_button(btn) is True:
                return
        self.on_action(action)

    def onClick(self, controlId: int) -> None:
        """Kodi sent the control's click."""
        if const.debug.log_gui:
            fflog(f' +++++++++ onClick: {controlId = }')
        if switch := self._customised_data.switches.get(controlId):
            state = switch.state
            switch.click()
            self._handle_switch(controlId)
            if switch.state is not state:
                self.on_switch_changed(controlId, switch.state)
        self.on_click(controlId)

    def onFocus(self, controlId: int) -> None:
        """Kodi sent the control's focus."""
        if const.debug.log_gui:
            fflog(f' +++++++++ onFocus: {controlId = }')
        self._handle_switch(controlId, check_focus=True)
        self.on_focus(controlId)

    def onControl(self, control: Control) -> None:
        """Kodi sent all click events on owned and selected controls when the control itself doesn't handle the message."""
        if const.debug.log_gui:
            fflog(f' +++++++++ onControl: {control = }')
        self.on_control(control)

    def _handle_switch(self, control_id: int, *, check_focus: bool = False) -> None:
        if check_focus:
            focused_id = self.focused_id()
            for control_id, switch in self._customised_data.switches.items():
                switch.control_state = switch.control_state.with_flag(switch.FOCUSED, control_id == focused_id)

        for control_id, switch in self._customised_data.switches.items():
            for subcontrol_name in switch.style.subcontrols:
                try:
                    subcontrol = cast(ControlImage, self.getControl(switch.subcontrols_id[subcontrol_name]))
                    subcontrol_def = switch.state.subcontrols[subcontrol_name]
                    old_subcontrol = switch.applied.state and switch.applied.state.subcontrols[subcontrol_name]
                except (RuntimeError, KeyError):
                    pass
                    # fflog_exc(title=f' +++++++++ _handle_switch: {control_id = }, {switch = }, {subcontrol_name = }')
                else:
                    if not switch.is_applied():
                        texture = subcontrol_def.texture(switch.control_state)
                        texture_path = texture.format_path(switch.control_state, switch_state=switch.state)
                        old_control_state = switch.applied.control_state
                        if (old_subcontrol is None or switch.applied.state is None
                                or old_subcontrol.texture_path(old_control_state, switch_state=switch.applied.state) != texture_path):
                            subcontrol.setImage('' if texture_path == '-' else texture_path)
                        if old_subcontrol is None or old_subcontrol.texture(old_control_state).color_diffuse != texture.color_diffuse:
                            subcontrol.setColorDiffuse(texture.color_diffuse)
            if not switch.is_applied():
                switch.set_applied()

    def focused_id(self) -> int:
        """Return ID of focused control or zero if no focus."""
        try:
            return self.getFocusId()  # type: ignore[reportAttributeAccessIssue]
        except RuntimeError:  # from kodi docs: raises RuntimeError if no control has focus
            return 0

    def get_control(self, control_id: int, type: type[T] = Control) -> T | None:
        """Get control by ID or None if not found."""
        try:
            ctl = cast(T, self.getControl(control_id))
            if type and isinstance(ctl, type):
                return ctl
            return None
        except RuntimeError:
            return None

    def set_control_enabled(self, control_id: int, enabled: bool, *, errors: Errors = 'ignore') -> None:
        """Enable or disable control by ID."""
        try:
            control = self.getControl(control_id)
        except RuntimeError:
            if errors == 'strict':
                msg = f'No control {control_id!r} in {self.__class__.__name__}'
                fflog(msg)
                raise KeyError(msg)
            return
        control.setEnabled(bool(enabled))
        if switch := self._customised_data.switches.get(control_id):
            new_control_state = switch.control_state.with_flag(switch.ENABLED, enabled)
            if switch.control_state != new_control_state:
                switch.control_state = new_control_state
                self._handle_switch(control_id)

    def set_focus(self, control: Control | int) -> None:
        """Set focus to control by ID or Control."""
        if isinstance(control, int):
            self.setFocusId(control)
        else:
            self.setFocus(control)

    def set_switch_state(self, control_id: int, state: str, *, errors: Errors = 'ignore') -> None:
        """Set switch state by control ID."""
        if switch := self._customised_data.switches.get(control_id):
            if state != switch.state.name:
                switch.set(state)
                self._handle_switch(control_id)
                self.on_switch_changed(control_id, switch.state)
        elif errors == 'strict':
            msg = f'No switch for control {control_id!r} in {self.__class__.__name__}'
            fflog(msg)
            raise KeyError(msg)

    def switch_state(self, control_id: int) -> SwitchState:
        """Get switch state by control ID."""
        if switch := self._customised_data.switches.get(control_id):
            return switch.state
        msg = f'No switch for control {control_id!r} in {self.__class__.__name__}'
        fflog(msg)
        raise KeyError(msg)

    def get_switch_state(self, control_id: int) -> SwitchState | None:
        """Get switch state by control ID."""
        if switch := self._customised_data.switches.get(control_id):
            return switch.state
        return None


class BaseWindow(AbstractWindow, WindowXML):

    def __new__(cls,
                xmlFilename: Optional[str] = None,
                scriptPath: str = control.addonPath,
                defaultSkin: str = 'Default',
                defaultRes: str = '1080i',  # '720p',
                *args,
                **kwargs) -> Self:
        obj: BaseWindow
        xml_args = cls._resolve_args(xmlFilename, scriptPath, defaultSkin, defaultRes, *args, **kwargs)
        obj = super().__new__(cls, xml_args.xml_filename, xml_args.script_path, xml_args.default_skin, xml_args.default_res, *args)
        obj.__init__(xml_args.xml_filename, xml_args.script_path, xml_args.default_skin, xml_args.default_res, *args, **kwargs)
        obj._customised_xml = xml_args.customized_xml
        obj._customised_data = xml_args.custom_data
        return obj

    def add_items(self, control_id: int, items: Sequence[Union[str, ListItem]]) -> None:
        control = self.getControl(control_id)
        if isinstance(control, ControlList):
            control.reset()
            control.addItems(items)  # type: ignore - ControlList.addItems() accepts Sequence[]


class BaseDialog(AbstractWindow, WindowXMLDialog, Generic[RESULT]):

    if TYPE_CHECKING:
        _result: RESULT | None
        _exception: Optional[BaseException]
        _closed: bool
        _thread: WindowThread | None

    def __new__(cls,
                xmlFilename: Optional[str] = None,
                scriptPath: Optional[str] = None,
                defaultSkin: Optional[str] = None,
                defaultRes: Optional[str] = None,
                *args,
                modal: bool = True,
                _call_init: bool = False,
                **kwargs) -> Self:
        obj: Self
        if const.debug.log_gui:
            fflog.debug(f'BaseDialog.__new__({cls=}, {xmlFilename=}, {scriptPath=}, {defaultSkin=}, {defaultRes=}, {args=}, {modal=}, {_call_init=}, ...)')
        # fflog.debug(f'BaseDialog.__new__({cls=}, {xmlFilename=}, {scriptPath=}, {defaultSkin=}, {defaultRes=}, {args=}, {modal=}, {_call_init=}, {kwargs=})')
        xml_args = cls._resolve_args(xmlFilename, scriptPath, defaultSkin, defaultRes, *args, **kwargs)
        if modal:
            obj = super().__new__(cls, xml_args.xml_filename, xml_args.script_path, xml_args.default_skin, xml_args.default_res)
            obj._thread = None
        else:
            # for non-blocking dialogs, window is created in thread
            th = WindowThread[Self](super().__new__, name=f'WindowThread.{xml_args.xml_filename}',
                                    args=(cls, xml_args.xml_filename, xml_args.script_path, xml_args.default_skin, xml_args.default_res, *args))
            if const.debug.log_gui:
                fflog.debug(f'••• start thread {th}')
            th.start()
            with th.win_ready:
                if not th.win:
                    if const.debug.log_gui:
                        fflog.debug('••• wait for window')
                    th.win_ready.wait()
            obj = th.win
            obj._thread = th
        obj._closed = False
        obj._result = None
        obj._exception = None
        obj._customised_xml = xml_args.customized_xml
        obj._customised_data = xml_args.custom_data
        if _call_init:
            if const.debug.log_gui:
                fflog.debug('••• init')
            obj.__init__(xml_args.xml_filename, xml_args.script_path, xml_args.default_skin, xml_args.default_res, *args, **kwargs)
        if const.debug.log_gui:
            fflog.debug('••• created')
        return obj
        # return ObjectProxy(obj)  # to avoid __init__ call, _WindowMetaClass will remove ObjectProxy

    def __init__(self, *args, **kwargs) -> None:
        """Useless, because WindowXMLDialog uses __new__ only."""
        pass

    def raise_exception(self, exception: BaseException) -> None:
        """Set exception to raise from do_modal() / doModal()."""
        self._exception = exception

    def result(self) -> RESULT | None:
        """Get result of dialog."""
        if self._closed:
            return self._result
        raise RuntimeError('Dialog is not closed yet, call do_modal() or close() first.')

    def set_result(self, result: Any) -> None:
        """Explicit set the result. Useful with do_modal() / doModal()."""
        self._result = result

    def close(self, result: RESULT | MissingType | None = MISSING) -> None:
        fflog.debug(f'••• going to close (closed={self._closed})')
        if self._closed:
            return
        self._closed = True
        if result is not MISSING:
            if TYPE_CHECKING:
                assert not isinstance(result, MissingType)
            self._result = result
        if self.on_closing() is False:
            fflog.debug('••• close cancelled')
            return
        if self._thread:
            if const.debug.log_gui:
                fflog.debug('••• postpone exit')
            self._thread.stop()
        if const.debug.log_gui:
            fflog.debug('••• close')
        super().close()
        if const.debug.log_gui:
            fflog.debug('••• on_close')
        self._already_closed()
        if const.debug.log_gui:
            fflog.debug('••• finished')

    def do_modal(self) -> RESULT | None:
        """Execute the window. Call do_modal() / doModal() direct (modal) or in thread (modeless)."""
        if self._thread:
            from ..ff.kotools import KodiMonitor
            self.show()
            if const.debug.log_gui:
                fflog.debug(f'••• postpone {self.doModal}')
            xmonitor = KodiMonitor.instance()
            if 1 or type(self) is BaseDialog:
                parent_do_modal = super().doModal
            else:
                parent_do_modal = super().do_modal  # type: ignore[reportAttributeAccessIssue]  -- called in derived class, then do_modal exists
            fflog.debug(f'••• sending {parent_do_modal}, {xmonitor=}')
            if xmonitor is None:
                self._thread.command(parent_do_modal)
            else:
                # Abort window thread on Kodi exit
                with xmonitor.abort_context(self._thread.stop):
                    x = super()
                    self._thread.command(parent_do_modal)
            if const.debug.log_gui:
                fflog.debug(f'••• sent {parent_do_modal}')
        else:
            super().doModal()
            if not self._closed:
                self._closed = True
                self._already_closed()
        if self._exception:
            raise self._exception
        return self._result

    def doModal(self) -> Any:
        """Execute the window. Call doModal direct (modal) or in thread (modeless)."""
        return self.do_modal()

    def is_modal(self) -> bool:
        """Return True if dialog is modal."""
        return not self._thread

    def destroy(self) -> None:
        """Close and clean up."""
        self.close()
        if self._thread:
            self._thread.join()

    def add_items(self, control_id: int, items: Sequence[Union[str, ListItem]]) -> None:
        """Add items to control list `control_id`."""
        control = self.getControl(control_id)
        if isinstance(control, ControlList):
            control.reset()
            control.addItems(items)  # type: ignore[reportArgumentType] - ControlList.addItems() accepts Sequence[]


def _customize_xml(xml_source: Path, xml_path: Path, *, request: CustomXmlRequest | None = None) -> CustomXmlData:
    """Fix window/dialog source XML to handle what kodi can not."""
    from .gui import custom_xml
    return custom_xml(xml_source, xml_path, request=request)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#######################################################################
#
# VidCutter - media cutter & joiner
#
# copyright © 2017 Pete Alexandrou
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#######################################################################

import datetime
import logging
import os
import re
import sys
import time
from datetime import timedelta

from PyQt5.QtCore import (pyqtSignal, pyqtSlot, QDir, QFile, QFileInfo, QModelIndex, QPoint, QSize, Qt, QTextStream,
                          QTime, QTimer, QUrl)
from PyQt5.QtGui import QCloseEvent, QDesktopServices, QFont, QFontDatabase, QIcon, QKeyEvent, QMovie, QPixmap
from PyQt5.QtWidgets import (QAction, QActionGroup, qApp, QApplication, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                             QGroupBox, QHBoxLayout, QLabel, QListWidgetItem, QMenu, QMessageBox, QPushButton,
                             QSizePolicy, QStyleFactory, QVBoxLayout, QWidget, QWidgetAction)

from vidcutter.about import About
from vidcutter.updater import Updater
from vidcutter.videoinfo import VideoInfo
from vidcutter.videolist import VideoList
from vidcutter.videoslider import VideoSlider, VideoSliderWidget
from vidcutter.videostyle import VideoStyleDark, VideoStyleLight
from vidcutter.videotoolbar import VideoToolBar

from vidcutter.libs.mpvwidget import mpvWidget
from vidcutter.libs.notifications import JobCompleteNotification
from vidcutter.libs.taskbarprogress import TaskbarProgress
from vidcutter.libs.videoservice import VideoService
from vidcutter.libs.widgets import FrameCounter, TimeCounter, VCProgressBar, VolumeSlider

# noinspection PyUnresolvedReferences
import vidcutter.resources


class VideoCutter(QWidget):
    errorOccurred = pyqtSignal(str)

    timeformat = 'hh:mm:ss.zzz'
    runtimeformat = 'hh:mm:ss'

    def __init__(self, parent: QWidget):
        super(VideoCutter, self).__init__(parent)
        self.setObjectName('videocutter')
        self.logger = logging.getLogger(__name__)
        self.parent = parent
        self.theme = self.parent.theme
        self.settings = self.parent.settings
        self.init_theme()

        self.videoService = VideoService(self)
        self.updater = Updater(self)
        self.taskbarControl = TaskbarProgress(self)

        self.latest_release_url = 'https://github.com/ozmartian/vidcutter/releases/latest'
        self.ffmpeg_installer = {
            'win32': {
                64: 'https://ffmpeg.zeranoe.com/builds/win64/static/ffmpeg-latest-win64-static.7z',
                32: 'https://ffmpeg.zeranoe.com/builds/win32/static/ffmpeg-latest-win32-static.7z'
            },
            'darwin': {
                64: 'http://evermeet.cx/pub/ffmpeg/snapshots',
                32: 'http://evermeet.cx/pub/ffmpeg/snapshots'
            },
            'linux': {
                64: 'https://johnvansickle.com/ffmpeg/builds/ffmpeg-git-64bit-static.tar.xz',
                32: 'https://johnvansickle.com/ffmpeg/builds/ffmpeg-git-32bit-static.tar.xz'
            }
        }

        self.mpvWidget = None
        self.clipTimes = []
        self.inCut, self.newproject = False, False
        self.finalFilename = ''
        self.totalRuntime = 0
        self.frameRate = 0
        self.notifyInterval = 1000
        self.currentMedia, self.mediaAvailable, self.mpvError = None, False, False

        self.enableOSD = self.settings.value('enableOSD', 'on', type=str) in {'on', 'true'}
        self.hardwareDecoding = self.settings.value('hwdec', 'on', type=str) in {'on', 'auto'}
        self.keepClips = self.settings.value('keepClips', 'off', type=str) in {'on', 'true'}
        self.nativeDialogs = self.settings.value('nativeDialogs', 'on', type=str) in {'on', 'true'}
        self.timelineThumbs = self.settings.value('timelineThumbs', 'on', type=str) in {'on', 'true'}
        self.hideConsole = self.settings.value('hideConsole', 'on', type=str) in {'on', 'true'}

        self.edlblock_re = re.compile(r'(\d+(?:\.?\d+)?)\s(\d+(?:\.?\d+)?)\s([01])')

        self.level1_spinner, self.level2_spinner = QDoubleSpinBox(self), QDoubleSpinBox(self)

        self.initIcons()
        self.initActions()
        self.toolbar = VideoToolBar(self)
        self.initToolbar()

        self.appMenu, self.clipindex_removemenu, self.clipindex_contextmenu = QMenu(self), QMenu(self), QMenu(self)
        self.initMenus()

        self.seekSlider = VideoSlider(self)
        self.seekSlider.sliderMoved.connect(self.setPosition)
        self.sliderWidget = VideoSliderWidget(self, self.seekSlider)
        self.sliderWidget.setLoader(True)

        self.initNoVideo()

        self.cliplist = VideoList(self)
        self.cliplist.customContextMenuRequested.connect(self.itemMenu)
        self.cliplist.itemClicked.connect(self.positionAtClip)
        self.cliplist.model().rowsMoved.connect(self.syncClipList)

        listHeader = QLabel(self)
        listHeader.setPixmap(QPixmap(':/images/%s/clipindex.png' % self.theme, 'PNG'))
        listHeader.setAlignment(Qt.AlignCenter)
        listHeader.setObjectName('listHeader')

        self.runtimeLabel = QLabel('<div align="right">00:00:00</div>', self)
        self.runtimeLabel.setObjectName('runtimeLabel')

        self.clipindex_add = QPushButton('ADD', self)
        self.clipindex_add.setToolTip('Add')
        self.clipindex_add.setStatusTip('Add external clips to the clip index')
        self.clipindex_add.setCursor(Qt.PointingHandCursor)
        self.clipindex_remove = QPushButton('REMOVE', self)
        self.clipindex_remove.setToolTip('Remove')
        self.clipindex_remove.setStatusTip('Remove a selected clip or all of them from the clip index')
        self.clipindex_remove.setMenu(self.clipindex_removemenu)
        self.clipindex_remove.setCursor(Qt.PointingHandCursor)
        if sys.platform == 'win32':
            self.clipindex_add.setStyle(QStyleFactory.create('Fusion'))
            self.clipindex_remove.setStyle(QStyleFactory.create('Fusion'))

        clipindex_layout = QHBoxLayout()
        clipindex_layout.setSpacing(0)
        clipindex_layout.setContentsMargins(0, 0, 0, 0)
        clipindex_layout.addWidget(self.clipindex_add)
        clipindex_layout.addSpacing(3)
        clipindex_layout.addWidget(self.clipindex_remove)
        clipindexTools = QWidget(self)
        clipindexTools.setObjectName('clipindextools')
        clipindexTools.setLayout(clipindex_layout)

        self.clipindexLayout = QVBoxLayout()
        self.clipindexLayout.setSpacing(0)
        self.clipindexLayout.setContentsMargins(0, 0, 0, 0)
        self.clipindexLayout.addWidget(listHeader)
        self.clipindexLayout.addWidget(self.cliplist)
        self.clipindexLayout.addWidget(self.runtimeLabel)
        self.clipindexLayout.addSpacing(3)
        self.clipindexLayout.addWidget(clipindexTools)

        self.videoLayout = QHBoxLayout()
        self.videoLayout.setContentsMargins(0, 0, 0, 0)
        self.videoLayout.addWidget(self.novideoWidget)
        self.videoLayout.addSpacing(10)
        self.videoLayout.addLayout(self.clipindexLayout)

        self.timeCounter = TimeCounter(self)
        self.timeCounter.timeChanged.connect(lambda newtime: self.setPosition(newtime.msecsSinceStartOfDay()))
        self.frameCounter = FrameCounter(self)
        self.frameCounter.setReadOnly(True)

        countersLayout = QHBoxLayout()
        countersLayout.setContentsMargins(0, 0, 0, 0)
        countersLayout.addStretch(1)
        # noinspection PyArgumentList
        countersLayout.addWidget(QLabel('TIME:', objectName='tcLabel'))
        countersLayout.addWidget(self.timeCounter)
        countersLayout.addStretch(1)
        # noinspection PyArgumentList
        countersLayout.addWidget(QLabel('FRAME:', objectName='fcLabel'))
        countersLayout.addWidget(self.frameCounter)
        countersLayout.addStretch(1)

        countersWidget = QWidget(self)
        countersWidget.setObjectName('counterwidgets')
        countersWidget.setContentsMargins(0, 0, 0, 0)
        countersWidget.setLayout(countersLayout)
        countersWidget.setMaximumHeight(28)

        self.initMPV()

        self.videoplayerLayout = QVBoxLayout()
        self.videoplayerLayout.setSpacing(0)
        self.videoplayerLayout.setContentsMargins(0, 0, 0, 0)
        self.videoplayerLayout.addWidget(self.mpvWidget)
        self.videoplayerLayout.addWidget(countersWidget)

        self.videoplayerWidget = QWidget(self)
        self.videoplayerWidget.setVisible(False)
        self.videoplayerWidget.setObjectName('videoplayer')
        self.videoplayerWidget.setLayout(self.videoplayerLayout)

        # noinspection PyArgumentList
        self.thumbnailsButton = QPushButton(icon=self.thumbnailsIcon, flat=True, iconSize=QSize(16, 16),
                                            statusTip='Show timeline thumbnails', cursor=Qt.PointingHandCursor,
                                            toggled=self.toggleThumbs, checkable=True,
                                            objectName='thumbnailsButton')
        if self.timelineThumbs:
            self.thumbnailsButton.setChecked(True)
        else:
            self.seekSlider.setObjectName('nothumbs')

        # noinspection PyArgumentList
        self.osdButton = QPushButton(icon=self.osdIcon, flat=True, iconSize=QSize(16, 16), checkable=True,
                                     statusTip='Toggle on-screen-display', cursor=Qt.PointingHandCursor,
                                     toggled=self.toggleOSD, objectName='osdButton')
        if self.enableOSD:
            self.osdButton.setChecked(True)

        if sys.platform == 'darwin':
            self.osdButton.setChecked(False)
            self.osdButton.hide()

        # noinspection PyArgumentList
        self.consoleButton = QPushButton(icon=self.consoleIcon, flat=True, iconSize=QSize(16, 16), checkable=True,
                                         statusTip='Hide debug console window', cursor=Qt.PointingHandCursor,
                                         toggled=self.toggleConsole, objectName='consoleButton')

        if self.hideConsole:
            self.consoleButton.setChecked(True)
        else:
            self.mpvWidget.setLogLevel('v')
            os.environ['DEBUG'] = '1'
            self.parent.console.show()

        self.thumbnailsButton.setStyle(QStyleFactory.create('Fusion'))
        self.osdButton.setStyle(QStyleFactory.create('Fusion'))
        self.consoleButton.setStyle(QStyleFactory.create('Fusion'))

        # noinspection PyArgumentList
        self.muteButton = QPushButton(objectName='muteButton', icon=self.unmuteIcon, flat=True, toolTip='Mute',
                                      statusTip='Toggle audio mute', iconSize=QSize(16, 16), clicked=self.muteAudio,
                                      cursor=Qt.PointingHandCursor)

        # noinspection PyArgumentList
        self.volSlider = VolumeSlider(orientation=Qt.Horizontal, toolTip='Volume', statusTip='Adjust volume level',
                                    cursor=Qt.PointingHandCursor, value=self.parent.startupvol, minimum=0,
                                    maximum=130, sliderMoved=self.setVolume, objectName='volumeSlider')

        if sys.platform == 'darwin':
            self.volSlider.setStyle(QStyleFactory.create('Macintosh'))

        # noinspection PyArgumentList
        self.fullscreenButton = QPushButton(objectName='fullscreenButton', icon=self.fullscreenIcon, flat=True,
                                            toolTip='Fullscreen', statusTip='Switch to fullscreen video',
                                            iconSize=QSize(14, 14), clicked=self.toggleFullscreen,
                                            cursor=Qt.PointingHandCursor)

        # noinspection PyArgumentList
        self.menuButton = QPushButton(self, toolTip='Menu', cursor=Qt.PointingHandCursor, flat=True,
                                      objectName='menuButton', statusTip='Click to view menu options')
        self.menuButton.setFixedSize(QSize(40, 42))
        self.menuButton.setMenu(self.appMenu)

        self.seekSlider.initStyle()

        toolbarLayout = QHBoxLayout()
        toolbarLayout.addWidget(self.toolbar)
        toolbarLayout.setContentsMargins(0, 0, 0, 0)

        toolbarGroup = QGroupBox()
        toolbarGroup.setLayout(toolbarLayout)
        toolbarGroup.setStyleSheet('border: 0;')

        controlsLayout = QHBoxLayout()
        controlsLayout.addSpacing(10)
        controlsLayout.addWidget(self.thumbnailsButton)
        controlsLayout.addSpacing(4)
        controlsLayout.addWidget(self.osdButton)
        controlsLayout.addSpacing(4)
        controlsLayout.addWidget(self.consoleButton)
        controlsLayout.addStretch(10)
        controlsLayout.addWidget(toolbarGroup)
        controlsLayout.addStretch(10)
        controlsLayout.addWidget(self.muteButton)
        controlsLayout.addSpacing(5)
        controlsLayout.addWidget(self.volSlider)
        controlsLayout.addSpacing(5)
        controlsLayout.addWidget(self.fullscreenButton)
        controlsLayout.addSpacing(10)
        controlsLayout.addWidget(self.menuButton)
        controlsLayout.addSpacing(10)

        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(10, 10, 10, 0)
        layout.addLayout(self.videoLayout)
        layout.addWidget(self.sliderWidget)
        layout.addSpacing(5)
        layout.addLayout(controlsLayout)

        self.setLayout(layout)

    @pyqtSlot(Exception)
    def on_mpvError(self, error: Exception = None) -> None:
        pencolor1 = '#C681D5' if self.theme == 'dark' else '#642C68'
        pencolor2 = '#FFF' if self.theme == 'dark' else '#222'
        mbox = QMessageBox(self)
        mbox.setObjectName('genericdialog')
        mbox.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        mbox.setIconPixmap(QIcon(':/images/mpv.png').pixmap(128, 128))
        mbox.setWindowTitle('Missing libmpv library...')
        mbox.setMinimumWidth(500)
        mbox.setText('''
        <style>
            h1 {
                color: %s;
                font-family: 'Futura LT', sans-serif;
                font-weight: 400;
            }
            p, li { font-size: 15px; }
            p { color: %s; }
            li { color: %s; font-weight: bold; }
        </style>
        <table border="0" cellpadding="6" cellspacing="0" width="500">
        <tr><td>
            <h1>Cannot locate libmpv (MPV libraries) required for media playback</h1>
            <p>The app will now exit, please try again once you have installed
            libmpv via package installation or building from mpv source yourself.</p>
            <p>In most distributions libmpv can be found under package names like:
            <ul>
                <li>mpv <span style="font-size:12px;">(bundled with the mpv video player)</span></li>
                <li>libmpv1</li>
                <li>mpv-libs</li>
            </ul></p>
        </td></tr>
        </table>''' % (pencolor1, pencolor2, pencolor1))
        mbox.addButton(QMessageBox.Ok)
        sys.exit(mbox.exec_())

    def init_theme(self) -> None:
        qApp.setStyle(VideoStyleDark() if self.theme == 'dark' else VideoStyleLight())
        QFontDatabase.addApplicationFont(':/fonts/FuturaLT.ttf')
        QFontDatabase.addApplicationFont(':/fonts/OpenSans.ttf')
        QFontDatabase.addApplicationFont(':/fonts/OpenSansBold.ttf')
        self.style().loadQSS(self.theme, self.parent.devmode)
        QApplication.setFont(QFont('Open Sans', 12 if sys.platform == 'darwin' else 10, 300))

    def initMPV(self) -> None:
        self.mpvWidget = mpvWidget(
            parent=self,
            vo='opengl-cb',
            ytdl=False,
            pause=True,
            keep_open=True,
            idle=True,
            osc=False,
            osd_font='Futura LT',
            osd_level=0,
            osd_align_x='left',
            osd_align_y='top',
            cursor_autohide=False,
            input_cursor=False,
            input_default_bindings=False,
            stop_playback_on_init_failure=False,
            input_vo_keyboard=False,
            sub_auto=False,
            sid=False,
            video_sync='display-vdrop',
            audio_file_auto=False,
            quiet=True,
            # terminal=True,
            msg_level=('all=v' if os.getenv('DEBUG', False) else 'error'),
            volume=self.parent.startupvol,
            keepaspect=self.keepRatioAction.isChecked(),
            hwdec=('auto' if self.hardwareDecoding else 'no'))
        self.mpvWidget.durationChanged.connect(self.on_durationChanged)
        self.mpvWidget.positionChanged.connect(self.on_positionChanged)

    def initNoVideo(self) -> None:
        self.novideoWidget = QWidget(self)
        self.novideoWidget.setObjectName('novideoWidget')
        self.novideoWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        self.novideoLabel = QLabel(self)
        self.novideoLabel.setAlignment(Qt.AlignCenter)
        self.novideoMovie = QMovie(':/images/novideotext.gif', b'GIF', self)
        self.novideoMovie.setScaledSize(QSize(250, 30))
        self.novideoMovie.frameChanged.connect(lambda: self.novideoLabel.setPixmap(self.novideoMovie.currentPixmap()))
        self.novideoMovie.start()
        novideoLayout = QVBoxLayout()
        novideoLayout.addStretch(1)
        novideoLayout.addWidget(self.novideoLabel)
        novideoLayout.addSpacing(95)
        self.novideoWidget.setLayout(novideoLayout)

    def initIcons(self) -> None:
        self.appIcon = QIcon(':/images/vidcutter.png')
        self.openIcon = QIcon()
        self.openIcon.addFile(':/images/%s/toolbar-open.png' % self.theme, QSize(50, 53), QIcon.Normal)
        self.openIcon.addFile(':/images/%s/toolbar-open-on.png' % self.theme, QSize(50, 53), QIcon.Active)
        self.openIcon.addFile(':/images/%s/toolbar-open-disabled.png' % self.theme, QSize(50, 53), QIcon.Disabled)
        self.playIcon = QIcon()
        self.playIcon.addFile(':/images/%s/toolbar-play.png' % self.theme, QSize(50, 53), QIcon.Normal)
        self.playIcon.addFile(':/images/%s/toolbar-play-on.png' % self.theme, QSize(50, 53), QIcon.Active)
        self.playIcon.addFile(':/images/%s/toolbar-play-disabled.png' % self.theme, QSize(50, 53), QIcon.Disabled)
        self.pauseIcon = QIcon()
        self.pauseIcon.addFile(':/images/%s/toolbar-pause.png' % self.theme, QSize(50, 53), QIcon.Normal)
        self.pauseIcon.addFile(':/images/%s/toolbar-pause-on.png' % self.theme, QSize(50, 53), QIcon.Active)
        self.pauseIcon.addFile(':/images/%s/toolbar-pause-disabled.png' % self.theme, QSize(50, 53), QIcon.Disabled)
        self.cutStartIcon = QIcon()
        self.cutStartIcon.addFile(':/images/%s/toolbar-start.png' % self.theme, QSize(50, 53), QIcon.Normal)
        self.cutStartIcon.addFile(':/images/%s/toolbar-start-on.png' % self.theme, QSize(50, 53), QIcon.Active)
        self.cutStartIcon.addFile(':/images/%s/toolbar-start-disabled.png' % self.theme, QSize(50, 53), QIcon.Disabled)
        self.cutEndIcon = QIcon()
        self.cutEndIcon.addFile(':/images/%s/toolbar-end.png' % self.theme, QSize(50, 53), QIcon.Normal)
        self.cutEndIcon.addFile(':/images/%s/toolbar-end-on.png' % self.theme, QSize(50, 53), QIcon.Active)
        self.cutEndIcon.addFile(':/images/%s/toolbar-end-disabled.png' % self.theme, QSize(50, 53), QIcon.Disabled)
        self.saveIcon = QIcon()
        self.saveIcon.addFile(':/images/%s/toolbar-save.png' % self.theme, QSize(50, 53), QIcon.Normal)
        self.saveIcon.addFile(':/images/%s/toolbar-save-on.png' % self.theme, QSize(50, 53), QIcon.Active)
        self.saveIcon.addFile(':/images/%s/toolbar-save-disabled.png' % self.theme, QSize(50, 53), QIcon.Disabled)
        self.muteIcon = QIcon(':/images/%s/muted.png' % self.theme)
        self.unmuteIcon = QIcon(':/images/%s/unmuted.png' % self.theme)
        self.upIcon = QIcon(':/images/up.png')
        self.downIcon = QIcon(':/images/down.png')
        self.removeIcon = QIcon(':/images/remove.png')
        self.removeAllIcon = QIcon(':/images/remove-all.png')
        self.successIcon = QIcon(':/images/thumbsup.png')
        self.openProjectIcon = QIcon(':/images/open.png')
        self.saveProjectIcon = QIcon(':/images/save.png')
        self.mediaInfoIcon = QIcon(':/images/info.png')
        self.viewLogsIcon = QIcon(':/images/viewlogs.png')
        self.updateCheckIcon = QIcon(':/images/update.png')
        self.thumbsupIcon = QIcon(':/images/thumbs-up.png')
        self.keyRefIcon = QIcon(':/images/keymap.png')
        self.thumbnailsIcon = QIcon()
        self.thumbnailsIcon.addFile(':/images/%s/thumbnails-on.png' % self.theme, QSize(16, 16),
                                    QIcon.Normal, QIcon.On)
        self.thumbnailsIcon.addFile(':/images/%s/thumbnails-off.png' % self.theme, QSize(16, 16),
                                    QIcon.Normal, QIcon.Off)
        self.osdIcon = QIcon()
        self.osdIcon.addFile(':/images/%s/osd-on.png' % self.theme, QSize(16, 16), QIcon.Normal, QIcon.On)
        self.osdIcon.addFile(':/images/%s/osd-off.png' % self.theme, QSize(16, 16), QIcon.Normal, QIcon.Off)
        self.consoleIcon = QIcon()
        self.consoleIcon.addFile(':/images/%s/console-on.png' % self.theme, QSize(16, 16), QIcon.Normal, QIcon.On)
        self.consoleIcon.addFile(':/images/%s/console-off.png' % self.theme, QSize(16, 16), QIcon.Normal, QIcon.Off)
        self.fullscreenIcon = QIcon(':/images/%s/fullscreen.png' % self.theme)

    # noinspection PyArgumentList
    def initActions(self) -> None:
        self.themeAction = QActionGroup(self)
        self.zoomAction = QActionGroup(self)
        self.labelAction = QActionGroup(self)
        self.openAction = QAction(self.openIcon, 'Open\nMedia', self, statusTip='Open a media file',
                                  triggered=self.openMedia)
        self.playAction = QAction(self.playIcon, 'Play\nMedia', self, triggered=self.playMedia,
                                  statusTip='Play media file', enabled=False)
        self.pauseAction = QAction(self.pauseIcon, 'Pause\nMedia', self, visible=False, triggered=self.playMedia,
                                   statusTip='Pause currently playing media')
        self.cutStartAction = QAction(self.cutStartIcon, 'Start\nClip', self, triggered=self.clipStart, enabled=False,
                                      statusTip='Set the start position of a new clip')
        self.cutEndAction = QAction(self.cutEndIcon, 'End\nClip', self, triggered=self.clipEnd,
                                    enabled=False, statusTip='Set the end position of a new clip')
        self.saveAction = QAction(self.saveIcon, 'Save\nMedia', self, triggered=self.cutMedia, enabled=False,
                                  statusTip='Save clips to a new media file')
        self.moveItemUpAction = QAction(self.upIcon, 'Move up', self, statusTip='Move clip position up in list',
                                        triggered=self.moveItemUp, enabled=False)
        self.moveItemDownAction = QAction(self.downIcon, 'Move down', self, statusTip='Move clip position down in list',
                                          triggered=self.moveItemDown, enabled=False)
        self.removeItemAction = QAction(self.removeIcon, 'Remove selected', self, triggered=self.removeItem,
                                        statusTip='Remove selected clip from list', enabled=False)
        self.removeAllAction = QAction(self.removeAllIcon, 'Remove all', self, statusTip='Remove all clips from list',
                                       triggered=self.clearList, enabled=False)
        self.mediaInfoAction = QAction(self.mediaInfoIcon, 'Media information', self, triggered=self.mediaInfo,
                                       statusTip='View current media file\'s technical properties', enabled=False)
        self.openProjectAction = QAction(self.openProjectIcon, 'Open project file', self, triggered=self.openProject,
                                         statusTip='Open a previously saved project file (*.vcp or *.edl)',
                                         enabled=True)
        self.saveProjectAction = QAction(self.saveProjectIcon, 'Save project file', self, triggered=self.saveProject,
                                         statusTip='Save current work to a project file (*.vcp or *.edl)',
                                         enabled=False)
        self.viewLogsAction = QAction(self.viewLogsIcon, 'View log file', self, triggered=self.viewLogs,
                                      statusTip='View the application\'s log file')
        self.updateCheckAction = QAction(self.updateCheckIcon, 'Check for updates...', self,
                                         statusTip='Check for application updates', triggered=self.updater.check)
        self.aboutQtAction = QAction('About Qt', self, statusTip='About Qt', triggered=qApp.aboutQt, shortcut=0)
        self.aboutAction = QAction('About %s' % qApp.applicationName(), self, triggered=self.aboutApp,
                                   statusTip='About %s' % qApp.applicationName(), shortcut=0)
        self.keyRefAction = QAction(self.keyRefIcon, 'Keyboard shortcuts', self, triggered=self.showKeyRef,
                                    statusTip='View shortcut key bindings')
        self.lightThemeAction = QAction('Light', self.themeAction, checkable=True, checked=True,
                                        statusTip='Use a light colored theme to match your desktop')
        self.darkThemeAction = QAction('Dark', self.themeAction, checkable=True, checked=False,
                                       statusTip='Use a dark colored theme to match your desktop')
        self.qtrZoomAction = QAction('1:4 Quarter', self.zoomAction, checkable=True, checked=False,
                                     statusTip='Zoom to a quarter of the source video size')
        self.halfZoomAction = QAction('1:2 Half', self.zoomAction, statusTip='Zoom to half of the source video size',
                                      checkable=True, checked=False)
        self.origZoomAction = QAction('1:1 Original', self.zoomAction, checkable=True, checked=True,
                                      statusTip='Set to original source video zoom level')
        self.dblZoomAction = QAction('2:1 Double', self.zoomAction, checkable=True, checked=False,
                                     statusTip='Zoom to double the original source video size')
        self.besideLabelsAction = QAction('Labels next to buttons', self.labelAction, checkable=True,
                                          statusTip='Show labels on right side of toolbar buttons', checked=True)
        self.underLabelsAction = QAction('Labels under buttons', self.labelAction, checkable=True,
                                         statusTip='Show labels under toolbar buttons', checked=False)
        self.noLabelsAction = QAction('No labels', self.labelAction, statusTip='Do not show labels on toolbar',
                                      checkable=True, checked=False)
        self.keepRatioAction = QAction('Keep aspect ratio', self, checkable=True, triggered=self.setAspect,
                                       statusTip='Keep window aspect ratio when resizing the window', enabled=False)
        self.nativeDialogsAction = QAction('Use native dialogs', self, checkable=True,
                                           statusTip='Use platform-native dialogs on file open & save operations',
                                           triggered=(lambda checked: self.saveSetting('nativeDialogs', checked)))
        self.keepClipsAction = QAction('Keep individual clips', self, checkable=True,
                                       statusTip='Keep the individual clips used to produce final media',
                                       triggered=(lambda checked: self.saveSetting('keepClips', checked)))
        self.hardwareDecodingAction = QAction('Hardware decoding', self, triggered=self.switchDecoding, checkable=True,
                                              statusTip='Enable hardware based video decoding for playback ' +
                                                        '(e.g. vdpau, vaapi, dxva2, d3d11, cuda)')

        if self.theme == 'dark':
            self.darkThemeAction.setChecked(True)
        else:
            self.lightThemeAction.setChecked(True)
        self.themeAction.triggered.connect(self.switchTheme)
        if self.keepClips:
            self.keepClipsAction.setChecked(True)
        if self.nativeDialogs:
            self.nativeDialogsAction.setChecked(True)
        if self.hardwareDecoding:
            self.hardwareDecodingAction.setChecked(True)
        if self.settings.value('aspectRatio', 'keep', type=str) == 'keep':
            self.keepRatioAction.setChecked(True)
            self.zoomAction.setEnabled(False)
        self.zoomAction.triggered.connect(self.setZoom)

    def initToolbar(self) -> None:
        self.toolbar.addAction(self.openAction)
        self.toolbar.addAction(self.playAction)
        self.toolbar.addAction(self.pauseAction)
        self.toolbar.addAction(self.cutStartAction)
        self.toolbar.addAction(self.cutEndAction)
        self.toolbar.addAction(self.saveAction)
        self.toolbar.disableTooltips()
        self.labelAction.triggered.connect(self.toolbar.setLabels)
        self.toolbar.setLabelByType(self.settings.value('toolbarLabels', 'beside', type=str))

    def initMenus(self) -> None:
        labelsMenu = QMenu('Toolbar labels', self.appMenu)
        labelsMenu.addAction(self.besideLabelsAction)
        labelsMenu.addAction(self.underLabelsAction)
        labelsMenu.addAction(self.noLabelsAction)

        zoomMenu = QMenu('Zoom', self.appMenu)
        zoomMenu.addAction(self.qtrZoomAction)
        zoomMenu.addAction(self.halfZoomAction)
        zoomMenu.addAction(self.origZoomAction)
        zoomMenu.addAction(self.dblZoomAction)

        self.level1_spinner.setDecimals(1)
        self.level1_spinner.setRange(0.1, 999.9)
        self.level1_spinner.setSingleStep(0.1)
        self.level1_spinner.setSuffix(' secs')
        self.level1_spinner.setValue(self.settings.value('level1Seek', 2, type=float))
        level1_layout = QHBoxLayout()
        level1_layout.addStretch(1)
        level1_layout.addWidget(QLabel('Seek #1'))
        level1_layout.addWidget(self.level1_spinner)
        level1_layout.addStretch(1)
        level1Seek = QWidget(self)
        level1Seek.setLayout(level1_layout)
        level1seekAction = QWidgetAction(self)
        level1seekAction.setDefaultWidget(level1Seek)

        self.level2_spinner.setDecimals(1)
        self.level2_spinner.setRange(0.1, 999.9)
        self.level2_spinner.setSingleStep(0.1)
        self.level2_spinner.setSuffix(' secs')
        self.level2_spinner.setValue(self.settings.value('level2Seek', 5, type=float))
        level2_layout = QHBoxLayout()
        level2_layout.addStretch(1)
        level2_layout.addWidget(QLabel('Seek #2'))
        level2_layout.addWidget(self.level2_spinner)
        level2_layout.addStretch(1)
        level2Seek = QWidget(self)
        level2Seek.setLayout(level2_layout)
        level2seekAction = QWidgetAction(self)
        level2seekAction.setDefaultWidget(level2Seek)

        optionsMenu = QMenu('Settings...', self.appMenu)
        if sys.platform != 'darwin':
            optionsMenu.addSection('Theme')
            optionsMenu.addAction(self.lightThemeAction)
            optionsMenu.addAction(self.darkThemeAction)
            optionsMenu.addSeparator()
        optionsMenu.addAction(self.keepClipsAction)
        optionsMenu.addSeparator()
        optionsMenu.addAction(level1seekAction)
        optionsMenu.addAction(level2seekAction)
        optionsMenu.addSeparator()
        optionsMenu.addAction(self.nativeDialogsAction)
        optionsMenu.addMenu(labelsMenu)
        optionsMenu.addAction(self.hardwareDecodingAction)
        optionsMenu.addAction(self.keepRatioAction)
        optionsMenu.addMenu(zoomMenu)
        optionsMenu.aboutToShow.connect(self.clearSpinners)

        self.appMenu.addAction(self.openProjectAction)
        self.appMenu.addAction(self.saveProjectAction)
        self.appMenu.addSeparator()
        self.appMenu.addMenu(optionsMenu)
        self.appMenu.addSeparator()
        self.appMenu.addAction(self.mediaInfoAction)
        self.appMenu.addAction(self.keyRefAction)
        self.appMenu.addSeparator()
        self.appMenu.addAction(self.viewLogsAction)
        self.appMenu.addAction(self.updateCheckAction)
        self.appMenu.addSeparator()
        self.appMenu.addAction(self.aboutQtAction)
        self.appMenu.addAction(self.aboutAction)

        self.clipindex_contextmenu.addAction(self.moveItemUpAction)
        self.clipindex_contextmenu.addAction(self.moveItemDownAction)
        self.clipindex_contextmenu.addSeparator()
        self.clipindex_contextmenu.addAction(self.removeItemAction)
        self.clipindex_contextmenu.addAction(self.removeAllAction)

        self.clipindex_removemenu.addActions([self.removeItemAction, self.removeAllAction])
        self.clipindex_removemenu.aboutToShow.connect(self.initRemoveMenu)

        if sys.platform == 'win32':
            labelsMenu.setStyle(QStyleFactory.create('Fusion'))
            zoomMenu.setStyle(QStyleFactory.create('Fusion'))
            optionsMenu.setStyle(QStyleFactory.create('Fusion'))
            self.appMenu.setStyle(QStyleFactory.create('Fusion'))
            self.clipindex_contextmenu.setStyle(QStyleFactory.create('Fusion'))
            self.clipindex_removemenu.setStyle(QStyleFactory.create('Fusion'))

    def saveSetting(self, setting: str, checked: bool) -> None:
        val = 'on' if checked else 'off'
        self.settings.setValue(setting, val)

    def clearSpinners(self) -> None:
        for obj in (self.level1_spinner, self.level2_spinner):
            obj.clearFocus()
            obj.lineEdit().deselect()

    def setRunningTime(self, runtime: str) -> None:
        self.runtimeLabel.setText('<div align="right">%s</div>' % runtime)

    @pyqtSlot()
    def initRemoveMenu(self):
        self.removeItemAction.setEnabled(False)
        self.removeAllAction.setEnabled(False)
        if self.cliplist.count() > 0:
            self.removeAllAction.setEnabled(True)
            if len(self.cliplist.selectedItems()) > 0:
                self.removeItemAction.setEnabled(True)

    def itemMenu(self, pos: QPoint) -> None:
        globalPos = self.cliplist.mapToGlobal(pos)
        self.initRemoveMenu()
        self.moveItemUpAction.setEnabled(False)
        self.moveItemDownAction.setEnabled(False)
        index = self.cliplist.currentRow()
        if index != -1:
            if not self.inCut:
                if index > 0:
                    self.moveItemUpAction.setEnabled(True)
                if index < self.cliplist.count() - 1:
                    self.moveItemDownAction.setEnabled(True)
        self.clipindex_contextmenu.exec_(globalPos)

    def moveItemUp(self) -> None:
        index = self.cliplist.currentRow()
        tmpItem = self.clipTimes[index]
        del self.clipTimes[index]
        self.clipTimes.insert(index - 1, tmpItem)
        self.renderTimes()

    def moveItemDown(self) -> None:
        index = self.cliplist.currentRow()
        tmpItem = self.clipTimes[index]
        del self.clipTimes[index]
        self.clipTimes.insert(index + 1, tmpItem)
        self.renderTimes()

    def removeItem(self) -> None:
        index = self.cliplist.currentRow()
        del self.clipTimes[index]
        if self.inCut and index == self.cliplist.count() - 1:
            self.inCut = False
            self.initMediaControls()
        self.renderTimes()

    def clearList(self) -> None:
        self.clipTimes.clear()
        self.cliplist.clear()
        self.inCut = False
        self.renderTimes()
        self.initMediaControls(True)

    def projectFilters(self, savedialog: bool = False) -> str:
        if savedialog:
            return 'VidCutter Project (*.vcp);;MPlayer EDL (*.edl)'
        elif self.mediaAvailable:
            return 'Project files (*.vcp, *.edl);;VidCutter Project (*.vcp);;MPlayer EDL (*.edl);;All files (*.*)'
        else:
            return 'VidCutter Project (*.vcp);;All files (*.*)'

    @staticmethod
    def mediaFilters() -> str:
        all_types = 'All media files (*.3gp, *.3g2, *.amv, * .avi, *.divx, *.div, *.flv, *.f4v, *.webm, *.mkv, ' + \
                    '*.mp3, *.mpa, *.mp1, *.mpeg, *.mpg, *.mpe, *.m1v, *.tod, *.mpv, *.m2v, *.ts, *.m2t, *.m2ts, ' + \
                    '*.mp4, *.m4v, *.mpv4, *.mod, *.mjpg, *.mjpeg, *.mov, *.qt, *.rm, *.rmvb, *.dat, *.bin, *.vob, ' + \
                    '*.wav, *.wma, *.wmv, *.asf, *.asx, *.xvid)'
        video_types = 'All video files (*.3gp, *.3g2, *.amv, * .avi, *.divx, *.div, *.flv, *.f4v, *.webm, *.mkv, ' + \
                      '*.mpeg, *.mpg, *.mpe, *.m1v, *.tod, *.mpv, *.m2v, *.ts, *.m2t, *.m2ts, ' + \
                      '*.mp4, *.m4v, *.mpv4, *.mod, *.mjpg, *.mjpeg, *.mov, *.qt, *.rm, *.rmvb, *.dat, *.bin, ' + \
                      '*.vob, *.wmv, *.asf, *.asx, *.xvid)'
        audio_types = 'All audio files (*.mp3, *.mpa, *.mp1, *.wav, *.wma)'
        specific_types = '3GPP files (*.3gp, *.3g2);;AMV files (*.amv);;AVI files (* .avi);;' + \
                         'DivX files (*.divx, *.div);;Flash files (*.flv, *.f4v);;WebM files (*.webm);;' + \
                         'MKV files (*.mkv);;MPEG Audio files (*.mp3, *.mpa, *.mp1);;' + \
                         'MPEG files (*.mpeg, *.mpg, *.mpe, *.m1v, *.tod);;' + \
                         'MPEG-2 files (*.mpv, *.m2v, *.ts, *.m2t, *.m2ts);;MPEG-4 files (*.mp4, *.m4v, *.mpv4);;' + \
                         'MOD files (*.mod);;MJPEG files (*.mjpg, *.mjpeg);;QuickTime files (*.mov, *.qt) ;;' + \
                         'RealMedia files (*.rm, *.rmvb);;VCD DAT files (*.dat);;VCD SVCD BIN/CUE images (*.bin);;' + \
                         'VOB files (*.vob);;Wave Audio files (*.wav);;Windows Media Audio files (*.wma);;' + \
                         'Windows Media files (*.wmv, *.asf, *.asx);;Xvid files (*.xvid)'
        return '%s;;%s;;%s;;%s;;All files (*.*)' % (all_types, video_types, audio_types, specific_types)

    def openMedia(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, caption='Select media file', filter=self.mediaFilters(),
                                                  directory=QDir.homePath(),
                                                  options=(QFileDialog.DontUseNativeDialog
                                                           if not self.nativeDialogsAction.isChecked()
                                                           else QFileDialog.Options()))
        if len(filename.strip()):
            self.loadMedia(filename)

    def openProject(self, checked: bool = False, project_file: str = None) -> None:
        initialFilter = 'Project files (*.vcp, *.edl)' if self.mediaAvailable else 'VidCutter Project (*.vcp)'
        if project_file is None:
            project_file, _ = QFileDialog.getOpenFileName(self, caption='Select project file',
                                                          filter=self.projectFilters(),
                                                          initialFilter=initialFilter,
                                                          directory=QDir.homePath(),
                                                          options=(QFileDialog.DontUseNativeDialog
                                                                   if not self.nativeDialogsAction.isChecked()
                                                                   else QFileDialog.Options()))
        if len(project_file.strip()):
            file = QFile(project_file)
            info = QFileInfo(file)
            project_type = info.suffix()
            if not file.open(QFile.ReadOnly | QFile.Text):
                QMessageBox.critical(self.parent, 'Open project file',
                                     'Cannot read project file %s:\n\n%s' % (project_file, file.errorString()))
                return
            qApp.setOverrideCursor(Qt.WaitCursor)
            self.clipTimes.clear()
            linenum = 1
            while not file.atEnd():
                line = file.readLine().trimmed()
                if line.length() > 0:
                    try:
                        line = str(line, encoding='utf-8')
                    except TypeError:
                        line = str(line)
                    except UnicodeDecodeError:
                        qApp.restoreOverrideCursor()
                        self.logger.error('Invalid project file was selected', exc_info=True)
                        sys.stderr.write('Invalid project file was selected')
                        QMessageBox.critical(self.parent, 'Invalid project file',
                                             'Could not make sense of the selected project file. Try viewing it in a ' +
                                             'text editor to ensure it is valid and not corrupted.')
                        return
                    if project_type == 'vcp' and linenum == 1:
                        self.loadMedia(line)
                        time.sleep(1)
                    else:
                        mo = self.edlblock_re.match(line)
                        if mo:
                            start, stop, action = mo.groups()
                            clip_start = self.delta2QTime(int(float(start) * 1000))
                            clip_end = self.delta2QTime(int(float(stop) * 1000))
                            clip_image = self.captureImage(clip_start)
                            self.clipTimes.append([clip_start, clip_end, clip_image])
                        else:
                            qApp.restoreOverrideCursor()
                            QMessageBox.critical(self.parent, 'Invalid project file',
                                                 'Invalid entry at line %s:\n\n%s' % (linenum, line))
                            return
                linenum += 1
            self.cutStartAction.setEnabled(True)
            self.cutEndAction.setDisabled(True)
            self.seekSlider.setRestrictValue(0, False)
            self.inCut = False
            self.newproject = True
            qApp.restoreOverrideCursor()
            self.showText('Project file loaded')

    def saveProject(self, filepath: str) -> None:
        if self.currentMedia is None:
            return
        project_file, _ = os.path.splitext(self.currentMedia)
        project_save, ptype = QFileDialog.getSaveFileName(self, caption='Save project',
                                                          directory='%s.vcp' % project_file,
                                                          filter=self.projectFilters(True),
                                                          initialFilter='VidCutter Project (*.vcp)',
                                                          options=(QFileDialog.DontUseNativeDialog
                                                                   if not self.nativeDialogsAction.isChecked()
                                                                   else QFileDialog.Options()))
        if len(project_save.strip()):
            file = QFile(project_save)
            if not file.open(QFile.WriteOnly | QFile.Text):
                QMessageBox.critical(self.parent, 'Save project',
                                     'Cannot save project file at %s:\n\n%s' % (project_save, file.errorString()))
                return
            qApp.setOverrideCursor(Qt.WaitCursor)
            if ptype == 'VidCutter Project (*.vcp)':
                QTextStream(file) << '%s\n' % self.currentMedia
            for clip in self.clipTimes:
                start_time = timedelta(hours=clip[0].hour(), minutes=clip[0].minute(), seconds=clip[0].second(),
                                       milliseconds=clip[0].msec())
                stop_time = timedelta(hours=clip[1].hour(), minutes=clip[1].minute(), seconds=clip[1].second(),
                                      milliseconds=clip[1].msec())
                QTextStream(file) << '%s\t%s\t%d\n' % (self.delta2String(start_time), self.delta2String(stop_time), 0)
            qApp.restoreOverrideCursor()
            self.showText('Project file saved')

    def loadMedia(self, filename: str) -> None:
        if not os.path.exists(filename):
            return
        self.currentMedia = filename
        self.initMediaControls(True)
        self.cliplist.clear()
        self.clipTimes.clear()
        self.seekSlider.clearRegions()
        self.parent.setWindowTitle('%s - %s' % (qApp.applicationName(), os.path.basename(self.currentMedia)))
        if not self.mediaAvailable:
            self.videoLayout.replaceWidget(self.novideoWidget, self.videoplayerWidget)
            self.novideoWidget.hide()
            self.novideoMovie.stop()
            self.novideoMovie.deleteLater()
            self.novideoWidget.deleteLater()
            self.videoplayerWidget.show()
            self.mediaAvailable = True
        self.mpvWidget.play(self.currentMedia)

    def playMedia(self) -> None:
        if self.mpvWidget.mpv.get_property('pause'):
            self.playAction.setVisible(False)
            self.pauseAction.setVisible(True)
        else:
            self.playAction.setVisible(True)
            self.pauseAction.setVisible(False)
        self.timeCounter.clearFocus()
        self.frameCounter.clearFocus()
        self.mpvWidget.pause()

    def showText(self, text: str, duration: int = 3, override: bool = False) -> None:
        if self.mediaAvailable:
            if not self.osdButton.isChecked() and not override:
                return
            if len(text.strip()):
                self.mpvWidget.showText(text, duration, 0)

    def initMediaControls(self, flag: bool = True) -> None:
        self.playAction.setEnabled(flag)
        self.saveAction.setEnabled(False)
        self.cutStartAction.setEnabled(flag)
        self.cutEndAction.setEnabled(False)
        self.mediaInfoAction.setEnabled(flag)
        self.keepRatioAction.setEnabled(flag)
        self.zoomAction.setEnabled(flag)
        self.seekSlider.clearRegions()
        if flag:
            self.seekSlider.setRestrictValue(0)
        else:
            self.seekSlider.setValue(0)
            self.seekSlider.setRange(0, 0)
            self.timeCounter.reset()
            self.frameCounter.reset()
        self.openProjectAction.setEnabled(flag)
        self.saveProjectAction.setEnabled(False)

    @pyqtSlot(int)
    def setPosition(self, position: int) -> None:
        if position >= self.seekSlider.restrictValue:
            self.mpvWidget.seek(position / 1000)

    @pyqtSlot(float, int)
    def on_positionChanged(self, progress: float, frame: int) -> None:
        progress *= 1000
        if self.seekSlider.restrictValue < progress or progress == 0:
            self.seekSlider.setValue(int(progress))
            self.timeCounter.setTime(self.delta2QTime(int(progress)).toString(self.timeformat))
            self.frameCounter.setFrame(frame)

    @pyqtSlot(float, int)
    def on_durationChanged(self, duration: float, frames: int) -> None:
        duration *= 1000
        self.seekSlider.setRange(0, int(duration))
        self.timeCounter.setDuration(self.delta2QTime(int(duration)).toString(self.timeformat))
        self.frameCounter.setFrameCount(frames)
        if self.thumbnailsButton.isChecked():
            self.seekSlider.initThumbs()
        else:
            self.sliderWidget.setLoader(False)

    @pyqtSlot(QListWidgetItem)
    def positionAtClip(self, item: QListWidgetItem) -> None:
        row = self.cliplist.indexFromItem(item).row()
        self.setPosition(self.clipTimes[row][0].msecsSinceStartOfDay())

    def muteAudio(self) -> None:
        if self.mpvWidget.mpv.get_property('mute'):
            self.showText('Audio enabled')
            self.muteButton.setIcon(self.unmuteIcon)
            self.muteButton.setToolTip('Mute')
        else:
            self.showText('Audio disabled')
            self.muteButton.setIcon(self.muteIcon)
            self.muteButton.setToolTip('Unmute')
        self.mpvWidget.mute()

    def setVolume(self, vol: int) -> None:
        if self.mediaAvailable:
            self.mpvWidget.volume(vol)

    @pyqtSlot(bool)
    def toggleThumbs(self, checked: bool) -> None:
        self.seekSlider.showThumbs = checked
        self.saveSetting('timelineThumbs', checked)
        if checked:
            self.showText('Thumbnails enabled')
            self.seekSlider.initStyle()
            if self.mediaAvailable:
                self.seekSlider.reloadThumbs()
        else:
            self.showText('Thumbnails disabled')
            self.seekSlider.removeThumbs()
            self.seekSlider.initStyle()

    @pyqtSlot(bool)
    def toggleConsole(self, checked: bool):
        if not hasattr(self, 'debugonstart'):
            self.debugonstart = os.getenv('DEBUG', False)
        if checked:
            if not self.debugonstart:
                os.environ['DEBUG'] = '0'
                self.mpvWidget.setLogLevel('error')
            self.parent.console.hide()
        else:
            self.mpvWidget.setLogLevel('v')
            os.environ['DEBUG'] = '1'
            self.parent.console.show()
        self.saveSetting('hideConsole', checked)

    @pyqtSlot(bool)
    def setAspect(self, checked: bool = True) -> None:
        self.mpvWidget.mpv.set_option('keepaspect', checked)
        self.zoomAction.setEnabled(checked)

    @pyqtSlot(QAction)
    def setZoom(self, action: QAction) -> None:
        if action == self.qtrZoomAction:
            level = -2
        elif action == self.halfZoomAction:
            level = -1
        elif action == self.dblZoomAction:
            level = 1
        else:
            level = 0
        self.mpvWidget.mpv.set_property('video-zoom', level)

    def clipStart(self) -> None:
        # if os.getenv('DEBUG', False):
        #     sys.stdout.write('cut start position: %s' % self.seekSlider.value())
        starttime = self.delta2QTime(self.seekSlider.value())
        self.clipTimes.append([starttime, '', self.captureImage(starttime)])
        self.timeCounter.setMinimum(starttime.toString(self.timeformat))
        self.frameCounter.lockMinimum()
        self.cutStartAction.setDisabled(True)
        self.cutEndAction.setEnabled(True)
        self.seekSlider.setRestrictValue(self.seekSlider.value(), True)
        self.inCut = True
        self.showText('start clip at %s' % starttime.toString(self.timeformat))
        self.renderTimes()

    def clipEnd(self) -> None:
        # if os.getenv('DEBUG', False):
        #     sys.stdout.write('cut end position: %s' % self.seekSlider.value())
        item = self.clipTimes[len(self.clipTimes) - 1]
        endtime = self.delta2QTime(self.seekSlider.value())
        if endtime.__lt__(item[0]):
            QMessageBox.critical(self.parent, 'Invalid END Time',
                                 'The clip end time must come AFTER it\'s start time. Please try again.')
            return
        item[1] = endtime
        self.cutStartAction.setEnabled(True)
        self.cutEndAction.setDisabled(True)
        self.timeCounter.setMinimum()
        self.seekSlider.setRestrictValue(0, False)
        self.inCut = False
        self.showText('end clip at %s' % endtime.toString(self.timeformat))
        self.renderTimes()

    @pyqtSlot(QModelIndex, int, int, QModelIndex, int)
    def syncClipList(self, parent: QModelIndex, start: int, end: int, destination: QModelIndex, row: int) -> None:
        if start < row:
            index = row - 1
        else:
            index = row
        clip = self.clipTimes.pop(start)
        self.clipTimes.insert(index, clip)
        self.seekSlider.switchRegions(start, index)

    def renderTimes(self) -> None:
        self.cliplist.clear()
        self.seekSlider.clearRegions()
        if len(self.clipTimes) > 4:
            self.cliplist.setFixedWidth(210)
        else:
            self.cliplist.setFixedWidth(190)
        self.totalRuntime = 0
        for clip in self.clipTimes:
            endItem = ''
            if isinstance(clip[1], QTime):
                endItem = clip[1].toString(self.timeformat)
                self.totalRuntime += clip[0].msecsTo(clip[1])
            listitem = QListWidgetItem()
            listitem.setToolTip('Drag clip to reorder')
            listitem.setStatusTip('Reorder clips with drag and drop or right-click menu')
            listitem.setTextAlignment(Qt.AlignVCenter)
            listitem.setData(Qt.DecorationRole, clip[2])
            listitem.setData(Qt.DisplayRole, clip[0].toString(self.timeformat))
            listitem.setData(Qt.UserRole + 1, endItem)
            listitem.setFlags(Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsEnabled)
            self.cliplist.addItem(listitem)
            if isinstance(clip[1], QTime):
                self.seekSlider.addRegion(clip[0].msecsSinceStartOfDay(), clip[1].msecsSinceStartOfDay())
        if len(self.clipTimes) and not self.inCut:
            self.saveAction.setEnabled(True)
            self.saveProjectAction.setEnabled(True)
        if self.inCut or len(self.clipTimes) == 0 or not isinstance(self.clipTimes[0][1], QTime):
            self.saveAction.setEnabled(False)
            self.saveProjectAction.setEnabled(False)
        self.setRunningTime(self.delta2QTime(self.totalRuntime).toString(self.runtimeformat))

    @staticmethod
    def delta2QTime(millisecs: int) -> QTime:
        secs = millisecs / 1000
        return QTime(int((secs / 3600) % 60), int((secs / 60) % 60), int(secs % 60), int((secs * 1000) % 1000))

    @staticmethod
    def delta2String(td: timedelta) -> str:
        if td is None or td == timedelta.max:
            return ''
        else:
            return '%f' % (td.days * 86400 + td.seconds + td.microseconds / 1000000.)

    @staticmethod
    def getClipTimestamp(source_timestamp: datetime.datetime, clip_start_msec: int) -> str:
        if source_timestamp is None:
            return None
        has_timezone = source_timestamp.tzinfo is not None and source_timestamp.tzinfo.utcoffset(source_timestamp) is not None
        clip_timestamp = source_timestamp + timedelta(milliseconds=clip_start_msec)
        if has_timezone:
            clip_timestamp = clip_timestamp.astimezone(datetime.timezone.utc)
            return clip_timestamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        else:
            return clip_timestamp.strftime('%Y-%m-%dT%H:%M:%S.%f')

    def captureImage(self, frametime: QTime) -> QPixmap:
        return VideoService.capture(self.currentMedia, frametime.toString(self.timeformat))

    def cutMedia(self) -> bool:
        clips = len(self.clipTimes)
        filename, filelist = '', []
        source_file, source_ext = os.path.splitext(self.currentMedia)
        source_timestamp = self.videoService.getCreateDate(source = self.currentMedia)
        if clips > 0:
            self.finalFilename, _ = QFileDialog.getSaveFileName(parent=self, caption='Save video',
                                                                directory='%s_EDIT%s' % (source_file, source_ext),
                                                                filter='Video files (*%s)' % source_ext,
                                                                options=(QFileDialog.DontUseNativeDialog
                                                                         if not self.nativeDialogsAction.isChecked()
                                                                         else QFileDialog.Options()))
            if not len(self.finalFilename.strip()):
                return False
            file, ext = os.path.splitext(self.finalFilename)
            if len(ext) == 0:
                ext = source_ext
                self.finalFilename += ext
            qApp.setOverrideCursor(Qt.WaitCursor)
            self.saveAction.setDisabled(True)
            interval = round(100 / (clips + (2 if clips == 1 else 3)))  # analysis + clipcount + joining + complete
            self.showProgress(interval)
            progval = self.progress.value()
            for clip in self.clipTimes:
                index = self.clipTimes.index(clip)
                progval += interval
                self.progress.updateProgress(progval, 'Cutting media clips [%s / %s]...'
                                             % ('{0:0>2}'.format(index + 1), '{0:0>2}'.format(clips)))
                qApp.processEvents()
                duration = self.delta2QTime(clip[0].msecsTo(clip[1])).toString(self.timeformat)
                clip_msecs = QTime(0, 0, 0).msecsTo(clip[0])
                clip_timestamp = self.getClipTimestamp(source_timestamp, clip_msecs)
                filename = '%s_%s%s' % (file, '{0:0>2}'.format(index), ext)
                filelist.append(filename)
                self.videoService.cut(source='%s%s' % (source_file, source_ext), output=filename,
                                      frametime=clip[0].toString(self.timeformat), duration=duration,
                                      timestamp=clip_timestamp,
                                      allstreams=True)
                if QFile(filename).size() < 1000:
                    self.logger.info('cut resulted in 0 length file, trying again without all stream mapping')
                    self.videoService.cut(source='%s%s' % (source_file, source_ext), output=filename,
                                          frametime=clip[0].toString(self.timeformat), duration=duration,
                                          timestamp=clip_timestamp,
                                          allstreams=False)
                index += 1
            if len(filelist) > 1:
                rc = False
                if self.isMPEGcodec():
                    self.logger.info('file is MPEG based thus join() via mpegts file based protocol')
                    rc = self.videoService.mpegtsJoin(filelist, self.finalFilename)
                if not rc or not QFile(self.finalFilename).size():
                    rc = self.joinMedia(filelist, self.finalFilename, True)
                if not rc or not QFile(self.finalFilename).size():
                    self.logger.info('join() resulted in 0 length file, trying again without all stream mapping')
                    self.joinMedia(filelist, self.finalFilename, False)
                if not self.keepClipsAction.isChecked():
                    for f in filelist:
                        if os.path.isfile(f):
                            QFile.remove(f)
            else:
                QFile.remove(self.finalFilename)
                QFile.rename(filename, self.finalFilename)
            self.progress.updateProgress(100, 'Complete...')
            QTimer.singleShot(1000, self.progress.close)
            qApp.processEvents()
            qApp.restoreOverrideCursor()
            self.saveAction.setEnabled(True)
            notify = JobCompleteNotification(self)
            notify.exec_()
            return True
        return False

    def joinMedia(self, joinlist: list, filename: str, allstreams: bool = True) -> bool:
        listfile = os.path.normpath(os.path.join(os.path.dirname(joinlist[0]), '.vidcutter.list'))
        fobj = open(listfile, 'w')
        for file in joinlist:
            fobj.write('file \'%s\'\n' % file.replace("'", "\\'"))
        fobj.close()
        result = self.videoService.join(listfile, filename, allstreams)
        QFile.remove(listfile)
        return result

    def isMPEGcodec(self) -> bool:
        mpegCodecList = ['h264', 'hevc', 'mpeg4', 'divx', 'xvid', 'mpeg2video', 'mpg2', 'mp2', 'mp3', 'aac']
        return self.mpvWidget.format().lower() in mpegCodecList

    @pyqtSlot()
    def mediaInfo(self) -> None:
        if self.mediaAvailable:
            if self.videoService.mediainfo is None:
                self.logger.error('Error trying to load media information. mediainfo could not be found')
                sys.stderr.write('Error trying to load media information. mediainfo could not be found')
                QMessageBox.critical(self.parent, 'Could not find mediainfo tool',
                                     'The <b>mediainfo</b> command line tool could not be found on your system. ' +
                                     'This is required for the Media Information option ' +
                                     'to work.<br/><br/>If you are on Linux, you can solve ' +
                                     'this by installing the <b>mediainfo</b> package via your ' +
                                     'package manager.')
                return
            mediainfo = VideoInfo(media=self.currentMedia, parent=self)
            mediainfo.show()

    @pyqtSlot()
    def showKeyRef(self) -> None:
        shortcuts = QWidget(self)
        shortcuts.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        shortcuts.setObjectName('shortcuts')
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(shortcuts.hide)
        layout = QVBoxLayout()
        layout.addWidget(QLabel(pixmap=QPixmap(':/images/%s/shortcuts.png' % self.theme)))
        layout.addWidget(buttons)
        shortcuts.setLayout(layout)
        shortcuts.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        shortcuts.setContentsMargins(10, 10, 10, 10)
        shortcuts.setWindowModality(Qt.WindowModal)
        shortcuts.setWindowTitle('Keyboard shortcuts')
        shortcuts.setMinimumWidth(400 if self.parent.scale == 'LOW' else 600)
        shortcuts.show()

    @pyqtSlot()
    def aboutApp(self) -> None:
        appInfo = About(self)
        appInfo.exec_()

    def showProgress(self, interval: int) -> None:
        self.progress = VCProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.show()
        self.progress.updateProgress(interval, 'Analyzing source video...')

    @staticmethod
    def sizeof_fmt(num: float, suffix: chr = 'B') -> str:
        for unit in ['', 'K', 'M', 'G', 'T', 'P', 'E', 'Z']:
            if abs(num) < 1024.0:
                return "%3.1f %s%s" % (num, unit, suffix)
            num /= 1024.0
        return "%.1f %s%s" % (num, 'Y', suffix)

    @staticmethod
    @pyqtSlot()
    def viewLogs() -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(logging.getLoggerClass().root.handlers[0].baseFilename))

    @pyqtSlot(bool)
    def switchDecoding(self, checked: bool = True) -> None:
        self.mpvWidget.mpv.set_property('hwdec', 'auto' if checked else 'no')
        self.saveSetting('hwdec', checked)

    @pyqtSlot(QAction)
    def switchTheme(self, action: QAction) -> None:
        if action == self.darkThemeAction:
            newtheme = 'dark'
        else:
            newtheme = 'light'
        if newtheme != self.theme:
            # noinspection PyArgumentList
            mbox = QMessageBox(icon=QMessageBox.NoIcon, windowTitle='Restart required', minimumWidth=500,
                               textFormat=Qt.RichText, objectName='genericdialog')
            mbox.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
            mbox.setText('''
                <style>
                    h1 {
                        color: %s;
                        font-family: "Futura LT", sans-serif;
                        font-weight: 400;
                    }
                    p { font-size: 15px; }
                </style>
                <h1>Warning</h1>
                <p>The application needs to be restarted in order to switch the theme. Ensure you have saved
                your project and no tasks are still in progress.</p>
                <p>Would you like to restart and switch themes now?</p>'''
                         % ('#C681D5' if self.theme == 'dark' else '#642C68'))
            mbox.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            mbox.setDefaultButton(QMessageBox.Yes)
            response = mbox.exec_()
            if response == QMessageBox.Yes:
                self.parent.reboot()
            else:
                if action == self.darkThemeAction:
                    self.lightThemeAction.setChecked(True)
                else:
                    self.darkThemeAction.setChecked(True)

    def ffmpeg_check(self) -> bool:
        valid = os.path.exists(self.videoService.backend) if self.videoService.backend is not None else False
        if not valid:
            if sys.platform == 'win32':
                exe = 'bin\\ffmpeg.exe'
            else:
                valid = os.path.exists(self.parent.get_path('bin/ffmpeg', override=True))
                exe = 'bin/ffmpeg'
            if sys.platform.startswith('linux'):
                link = self.ffmpeg_installer['linux'][self.parent.get_bitness()]
            else:
                link = self.ffmpeg_installer[sys.platform][self.parent.get_bitness()]
            QMessageBox.critical(self.parent, 'Missing FFMpeg executable', '<style>li { margin: 1em 0; }</style>' +
                                 '<h3 style="color:#6A687D;">MISSING FFMPEG EXECUTABLE</h3>' +
                                 '<p>The FFMpeg utility is missing in your ' +
                                 'installation. It should have been installed when you first setup VidCutter.</p>' +
                                 '<p>You can easily fix this by manually downloading and installing it yourself by' +
                                 'following the steps provided below:</p><ol>' +
                                 '<li>Download the <b>static</b> version of FFMpeg from<br/>' +
                                 '<a href="%s" target="_blank"><b>this clickable link</b></a>.</li>' % link +
                                 '<li>Extract this file accordingly and locate the ffmpeg executable within.</li>' +
                                 '<li>Move or Cut &amp; Paste the executable to the following path on your system: ' +
                                 '<br/><br/>&nbsp;&nbsp;&nbsp;&nbsp;%s</li></ol>'
                                 % QDir.toNativeSeparators(self.parent.get_path(exe, override=True)) +
                                 '<p><b>NOTE:</b> You will most likely need Administrator rights (Windows) or ' +
                                 'root access (Linux/Mac) in order to do this.</p>')
        return valid

    @pyqtSlot()
    def toggleFullscreen(self) -> None:
        if self.mediaAvailable:
            if self.mpvWidget.originalParent is not None:
                self.videoplayerLayout.insertWidget(0, self.mpvWidget)
                self.mpvWidget.originalParent = None
                self.parent.show()
            elif self.mpvWidget.parentWidget() != 0:
                self.parent.hide()
                self.videoplayerLayout.removeWidget(self.mpvWidget)
                self.mpvWidget.originalParent = self
                self.mpvWidget.setGeometry(qApp.desktop().screenGeometry(self))
                self.mpvWidget.setParent(None)
                self.mpvWidget.showFullScreen()

    def toggleOSD(self, checked: bool) -> None:
        self.showText('On screen display %s' % ('enabled' if checked else 'disabled'), override=True)
        self.saveSetting('enableOSD', checked)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.mediaAvailable:
            if event.key() in {Qt.Key_F}:
                self.toggleFullscreen()
            elif event.key() == Qt.Key_Left:
                self.mpvWidget.frameBackStep()
            elif event.key() == Qt.Key_Down:
                if qApp.queryKeyboardModifiers() == Qt.ShiftModifier:
                    self.mpvWidget.seek(-self.level2_spinner.value(), 'relative+exact')
                else:
                    self.mpvWidget.seek(-self.level1_spinner.value(), 'relative+exact')
            elif event.key() == Qt.Key_Right:
                self.mpvWidget.frameStep()
            elif event.key() == Qt.Key_Up:
                if qApp.queryKeyboardModifiers() == Qt.ShiftModifier:
                    self.mpvWidget.seek(self.level2_spinner.value(), 'relative+exact')
                else:
                    self.mpvWidget.seek(self.level1_spinner.value(), 'relative+exact')
            elif event.key() == Qt.Key_Home:
                self.mpvWidget.mpv.set_property('time-pos', 0)
            elif event.key() == Qt.Key_End:
                self.setPosition(self.seekSlider.maximum() - 1)
            elif event.key() in (Qt.Key_Return, Qt.Key_Enter) and (
                        not self.timeCounter.hasFocus() and not self.frameCounter.hasFocus()):
                if self.cutStartAction.isEnabled():
                    self.clipStart()
                elif self.cutEndAction.isEnabled():
                    self.clipEnd()
            elif event.key() == Qt.Key_Space:
                self.playMedia()
            event.accept()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.parent.closeEvent(event)

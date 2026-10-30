#!/bin/sh
cd /opt/console_gui || exit 1
exec /usr/bin/python3 /opt/console_gui/console_gui.py "$@"

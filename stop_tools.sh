#!/bin/bash

pkill -f "python3 tools/pdu_editor.py" 2>/dev/null && echo "Stopped pdu editor" || true
pkill -f "python3 tools/trace_analyzer.py" 2>/dev/null && echo "Stopped trace analyzer" || true
pkill -f "python3 tools/trace_editor.py" 2>/dev/null && echo "Stopped trace editor" || true
pkill -f "python3 tools/eth_trace_analyzer.py" 2>/dev/null && echo "Stopped eth trace analyzer" || true

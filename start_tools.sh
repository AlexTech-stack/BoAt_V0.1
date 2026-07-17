#!/bin/bash

python3 tools/pdu_editor.py &
python3 tools/trace_analyzer.py &
python3 tools/trace_editor.py &
python3 tools/eth_trace_analyzer.py &

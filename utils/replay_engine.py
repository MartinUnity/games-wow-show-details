"""utils/replay_engine.py
──────────────────────
Generates a standalone HTML/JS replay 'manuscript' for a combat encounter.
Uses a simple 2D canvas with panning and zooming to show unit movement.
"""

import csv
import json
import os
from datetime import datetime

import pandas as pd
import streamlit as st


def generate_replay_manuscript(combat_df, log_file_path):
    """
    Scans the original log file for positional data for the given combat_df.
    Returns a JSON string containing the 'manuscript' for the JS player.
    """
    if combat_df.empty or not log_file_path or not os.path.exists(log_file_path):
        return None

    # Get the time range for the combat to optimize scanning
    start_dt = combat_df["timestamp_dt"].min()
    end_dt = combat_df["timestamp_dt"].max()

    # We need to map source/target GUIDs to names and initial positions
    units = {}  # guid -> {name: str, color: str}
    events = []  # list of {t: float, type: str, guid: str, x: float, y: float, val: int}

    # Color palette
    colors = ["#7CFC00", "#FF4500", "#00CED1", "#FFD700", "#FF69B4", "#8A2BE2"]
    color_idx = 0

    try:
        with open(log_file_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line:
                    continue
                # Quick check for timestamp range before full parse
                try:
                    ts_part = line.split("  ", 1)[0]
                    dt = datetime.strptime(ts_part, "%m/%d/%Y %H:%M:%S.%f")
                    if dt < start_dt:
                        continue
                    if dt > end_dt:
                        break
                except:
                    continue

                # Parse basic header
                try:
                    header, rest = line.split(",", 1)
                    event_type = header.split("  ")[1]
                    parts = list(csv.reader([rest]))[0]
                    if len(parts) < 8:
                        continue

                    src_guid = parts[0]
                    src_name = parts[1].replace('"', "")
                    dst_guid = parts[4]
                    dst_name = parts[5].replace('"', "")

                    x, y = None, None

                    if "DAMAGE" in event_type or "HEAL" in event_type:
                        # Search from the end for something that looks like X, Y
                        for i in range(len(parts) - 5, 10, -1):
                            try:
                                val1 = float(parts[i])
                                val2 = float(parts[i + 1])
                                # Very loose heuristic for coordinates
                                if abs(val1) > 10 and abs(val2) > 10:
                                    x, y = val1, val2
                                    break
                            except:
                                continue

                    if x is not None and y is not None:
                        elapsed = (dt - start_dt).total_seconds()

                        # Register units
                        if src_guid not in units:
                            units[src_guid] = {"name": src_name, "color": colors[color_idx % len(colors)]}
                            color_idx += 1
                        if dst_guid not in units:
                            units[dst_guid] = {"name": dst_name, "color": "#FF0000"}

                        # Add movement/action event
                        event = {"t": round(elapsed, 2), "guid": src_guid, "x": x, "y": y}
                        events.append(event)
                except:
                    continue
    except Exception as e:
        print(f"Replay build failed: {e}")
        return None

    if not events:
        return None

    return json.dumps({"units": units, "events": events, "duration": (end_dt - start_dt).total_seconds()})


def render_replay_viewer(manuscript_json):
    """
    Returns the HTML/JS string for the replay viewer.
    """
    if not manuscript_json:
        return "<p>No positional data found in this combat.</p>"

    html_template = f"""
    <div id="replay-container" style="width: 100%; height: 500px; background: #0f1720; position: relative; border-radius: 8px; overflow: hidden; border: 1px solid #22303a;">
        <canvas id="replay-canvas" style="width: 100%; height: 100%; cursor: move;"></canvas>
        <div style="position: absolute; bottom: 10px; left: 10px; right: 10px; background: rgba(0,0,0,0.6); padding: 10px; border-radius: 4px; color: white; font-family: sans-serif; display: flex; align-items: center; gap: 15px;">
            <button id="play-btn" style="background: #7CFC00; border: none; padding: 5px 15px; border-radius: 3px; cursor: pointer; font-weight: bold; color: #000;">Play</button>
            <input type="range" id="time-slider" min="0" max="100" value="0" style="flex-grow: 1; cursor: pointer;">
            <span id="time-display">0.0s / 0.0s</span>
        </div>
    </div>

    <script>
    (function() {{
        const data = {manuscript_json};
        const canvas = document.getElementById('replay-canvas');
        const ctx = canvas.getContext('2d');
        const playBtn = document.getElementById('play-btn');
        const slider = document.getElementById('time-slider');
        const timeDisplay = document.getElementById('time-display');

        let currentTime = 0;
        let isPlaying = false;
        let lastTimestamp = 0;
        let scale = 1.0;
        let offsetX = 0;
        let offsetY = 0;

        function resize() {{
            canvas.width = canvas.parentElement.clientWidth;
            canvas.height = canvas.parentElement.clientHeight;
            if (data.events.length > 0) {{
                const xs = data.events.map(e => e.x);
                const ys = data.events.map(e => e.y);
                const minX = Math.min(...xs);
                const maxX = Math.max(...xs);
                const minY = Math.min(...ys);
                const maxY = Math.max(...ys);
                const centerLogX = (minX + maxX) / 2;
                const centerLogY = (minY + maxY) / 2;

                scale = Math.min(canvas.width / (maxX - minX + 100), canvas.height / (maxY - minY + 100));
                if (!scale || scale === Infinity || isNaN(scale)) scale = 2.0;

                offsetX = canvas.width / 2 - centerLogX * scale;
                offsetY = canvas.height / 2 + centerLogY * scale;
            }}
        }}

        window.addEventListener('resize', resize);
        setTimeout(resize, 100);

        function draw() {{
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            const currentUnits = {{}};
            data.events.forEach(e => {{
                if (e.t <= currentTime) {{
                    currentUnits[e.guid] = e;
                }}
            }});

            Object.keys(currentUnits).forEach(guid => {{
                const unitData = data.units[guid];
                const pos = currentUnits[guid];
                const screenX = pos.x * scale + offsetX;
                const screenY = -pos.y * scale + offsetY;

                ctx.beginPath();
                ctx.arc(screenX, screenY, 6, 0, Math.PI * 2);
                ctx.fillStyle = unitData.color;
                ctx.fill();
                ctx.strokeStyle = 'white';
                ctx.lineWidth = 1;
                ctx.stroke();

                ctx.fillStyle = 'rgba(255,255,255,0.8)';
                ctx.font = '10px sans-serif';
                ctx.fillText(unitData.name, screenX + 10, screenY + 3);
            }});

            timeDisplay.innerText = currentTime.toFixed(1) + 's / ' + data.duration.toFixed(1) + 's';
            slider.value = (currentTime / data.duration) * 100;
        }}

        function animate(timestamp) {{
            if (!lastTimestamp) lastTimestamp = timestamp;
            const delta = (timestamp - lastTimestamp) / 1000;
            lastTimestamp = timestamp;

            if (isPlaying) {{
                currentTime += delta;
                if (currentTime >= data.duration) {{
                    currentTime = data.duration;
                    isPlaying = false;
                    playBtn.innerText = 'Play';
                }}
            }}

            draw();
            requestAnimationFrame(animate);
        }}

        playBtn.addEventListener('click', () => {{
            isPlaying = !isPlaying;
            playBtn.innerText = isPlaying ? 'Pause' : 'Play';
            if (currentTime >= data.duration) currentTime = 0;
            lastTimestamp = 0;
        }});

        slider.addEventListener('input', () => {{
            currentTime = (slider.value / 100) * data.duration;
            draw();
        }});

        requestAnimationFrame(animate);
    }})();
    </script>
    """
    return html_template

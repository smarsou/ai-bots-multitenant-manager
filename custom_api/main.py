import asyncio
import psutil
import json
import json
import subprocess
import asyncio
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import socketio
import psutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# Global state
mindcraft_process = None
mindcraft_sio = socketio.AsyncClient()
mindcraft_server_url = "http://localhost:58908"

def is_mindcraft_running():
    global mindcraft_process
    if mindcraft_process and mindcraft_process.poll() is None:
        return True
    # Check by process name as fallback
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        if 'node' in proc.info['name'] and 'main.js' in ' '.join(proc.info['cmdline']):
            return True
    return False

def start_mindcraft():
    global mindcraft_process
    if is_mindcraft_running():
        return False
    mindcraft_path = os.path.join(os.path.dirname(__file__), '..', 'mindcraft') 
    print(f"Starting the Mindcraft server from: {mindcraft_path}")
    if not os.path.exists(mindcraft_path):
        raise HTTPException(status_code=500, detail=f"Mindcraft directory not found: {mindcraft_path}")
    mindcraft_process = subprocess.Popen(["node", "main.js"], cwd=mindcraft_path)
    return True

def stop_mindcraft():
    print("Stoping the Mindcraft server...")
    global mindcraft_process
    if not is_mindcraft_running():
        return False
    try:
        mindcraft_process.terminate()
        mindcraft_process.wait(timeout=5)
        mindcraft_process = None
    except Exception:
        mindcraft_process.kill()
        mindcraft_process = None
    return True
    
def restart_mindcraft():
    print("Restarting Mindcraft server...")
    stop_mindcraft()
    start_mindcraft()

def get_mindcraft_status() -> str:
    if is_mindcraft_running():
        return "running"
    return "stopped"

### --- WEBSOCKET: /ws/server --- ###

@app.websocket("/ws/server")
async def websocket_server(websocket: WebSocket):
    await websocket.accept()
    
    # Initial status when client connects
    prev_status = get_mindcraft_status()
    await websocket.send_json({"status": prev_status})

    async def monitor_status():
        nonlocal prev_status
        while True:
            await asyncio.sleep(2)  # Monitor every 2 seconds
            current_status = get_mindcraft_status()
            if current_status != prev_status:
                prev_status = current_status
                await websocket.send_json({"status": current_status})

    monitor_task = asyncio.create_task(monitor_status())

    try:
        while True:
            data = await websocket.receive_text()
            if data == "status":
                await websocket.send_json({"status": get_mindcraft_status()})
            elif data == "start":
                start_mindcraft()
                await websocket.send_json({"status": "running"})
            elif data == "stop":
                stop_mindcraft()
                await websocket.send_json({"status": "stopped"})
            elif data == "restart":
                restart_mindcraft()
                await websocket.send_json({"status": "running"})
    except WebSocketDisconnect:
        monitor_task.cancel()


### --- WEBSOCKET: /ws/bots --- ###

@app.websocket("/ws/bots")
async def websocket_bots_bridge(client_ws: WebSocket):
    await client_ws.accept()

    try:
        await mindcraft_sio.connect(mindcraft_server_url)

        # Forward agent updates to client
        @mindcraft_sio.on("agents-update")
        async def agents_update(data):
            await client_ws.send_json({"event": "agents-update", "data": data})

        async def receive_from_client():
            while True:
                data = await client_ws.receive_json()
                print(f"Received from client: {data}")
                event = data.get("event")
                args = data.get("args", [])
                if event:
                    await mindcraft_sio.emit(event, *args)

        task = asyncio.create_task(receive_from_client())

        await task

    except WebSocketDisconnect:
        await mindcraft_sio.disconnect()
    except Exception as e:
        await client_ws.send_text(f"Error: {str(e)}")


### --- HTTP GET & PUT /settings --- ###

@app.get("/settings")
async def get_settings():
    try:
        with open("../mindcraft/settings.json", "r", encoding="utf-8") as f:
            content = f.read()
        return {"content": content}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Settings file not found.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/settings")
async def update_settings(payload: dict):
    print(f"Updating settings with payload: {payload}")
    try:
        with open("../mindcraft/settings.json", "r") as f:
            settings = json.load(f)
        settings.update(payload)
        with open("../mindcraft/settings.json", "w") as f:
            json.dump(settings, f, indent=4)
        return {"status": "updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
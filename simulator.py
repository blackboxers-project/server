import asyncio
import websockets
import json
import random
import math
import sys
import os
from datetime import datetime

# UI Imports
try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.text import Text
    from rich.align import Align
except ImportError:
    print("ERROR: Missing libraries. Please run: pip install rich websockets")
    sys.exit(1)

SERVER_URL = "ws://localhost:8000/ws/plane/"


# --- CROSS-PLATFORM INPUT HANDLER ---
class KeyListener:
    def __init__(self):
        self.windows = sys.platform.startswith('win')
        if not self.windows:
            import tty, termios
            self.fd = sys.stdin.fileno()
            self.old_settings = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)

    def get_key(self):
        if self.windows:
            import msvcrt
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                return ch.decode('utf-8').lower()
        else:
            import select
            if select.select([sys.stdin], [], [], 0)[0]:
                return sys.stdin.read(1).lower()
        return None

    def cleanup(self):
        if not self.windows:
            import termios
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_settings)


# --- CONFIGURATION & PHYSICS ---
PLANE_TYPES = {
    "B737": {"climb": 120, "speed": 1.0, "wobble": 2, "audio_base": 85, "max_alt": 35000},
    "C172": {"climb": 40, "speed": 0.5, "wobble": 15, "audio_base": 95, "max_alt": 10000},
    "F16": {"climb": 450, "speed": 2.8, "wobble": 4, "audio_base": 110, "max_alt": 50000},
}

SCENARIOS = [
    {"name": "ENGINE FIRE", "squawk": "7700", "audio": 120, "drop": True, "prob": 0.3},
    {"name": "HIJACKING", "squawk": "7500", "audio": 60, "drop": False, "prob": 0.1},
    {"name": "RADIO LOSS", "squawk": "7600", "audio": 0, "drop": False, "prob": 0.2},
    {"name": "TURBULENCE", "squawk": "1200", "audio": 95, "drop": True, "prob": 0.4},
]


class SimulatedPlane:
    def __init__(self, plane_id, model_name="B737"):
        self.id = plane_id
        self.model_name = model_name
        self.specs = PLANE_TYPES.get(model_name, PLANE_TYPES["B737"])

        self.status = "GROUND"
        self.squawk = "1200"
        self.altitude = 0
        self.target_altitude = 0
        self.audio_level = self.specs["audio_base"]

        self.tick_count = 0
        self.emergency_mode = None
        self.keep_running = False
        self.websocket = None

    async def connect_and_fly(self):
        self.keep_running = True
        self.status = "AIRBORNE"
        self.target_altitude = random.randint(5000, self.specs["max_alt"])

        uri = f"{SERVER_URL}{self.id}"

        try:
            async with websockets.connect(uri) as ws:
                self.websocket = ws
                while self.keep_running:
                    self.tick_count += 0.1 * self.specs["speed"]
                    self.update_physics()

                    data = {
                        "gyro": self.get_gyro(),
                        "audio_level": self.get_audio(),
                        "squawk": self.squawk,
                        "altitude": self.altitude
                    }

                    try:
                        await ws.send(json.dumps(data))
                    except:
                        break

                    await asyncio.sleep(0.2)

                if self.websocket:
                    await self.websocket.send(json.dumps({"type": "disconnect"}))
                    await self.websocket.close()

        except Exception:
            self.status = "CRASHED"

        if self.status != "CRASHED": self.status = "LANDED"

        # Auto-remove from fleet
        if self.id in manager.fleet:
            del manager.fleet[self.id]

    def update_physics(self):
        if self.emergency_mode == "drop":
            self.altitude -= (self.specs["climb"] * 2.0)
        elif self.altitude < self.target_altitude:
            self.altitude += self.specs["climb"] + random.randint(-10, 10)
        elif self.altitude > self.target_altitude:
            self.altitude -= self.specs["climb"]

        if self.altitude < 0: self.altitude = 0
        if random.random() > 0.95: self.target_altitude += random.randint(-500, 500)

    def get_gyro(self):
        w = self.specs["wobble"]
        if self.emergency_mode == "drop": w *= 5
        return {"x": math.sin(self.tick_count) * w, "y": 0, "z": 0}

    def get_audio(self):
        return max(0, self.audio_level + random.randint(-5, 5))

    def apply_scenario(self, scenario):
        self.emergency_mode = "drop" if scenario["drop"] else None
        self.squawk = scenario["squawk"]
        self.audio_level = scenario["audio"]
        return f"{scenario['name']} triggered!"

    async def land(self):
        self.target_altitude = 0
        self.keep_running = False

    async def crash(self):
        self.status = "CRASHED"
        self.keep_running = False
        if self.websocket: await self.websocket.close()


# --- FLEET MANAGER ---
class FleetManager:
    def __init__(self):
        self.fleet = {}
        self.chaos_mode = False
        self.log_messages = []
        self.log("System Initialized. Ready for traffic.")

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_messages.append(f"[{ts}] {msg}")
        if len(self.log_messages) > 6: self.log_messages.pop(0)

    def add_plane(self, model="B737"):
        pid = f"{model}-{random.randint(100, 999)}"
        self.fleet[pid] = SimulatedPlane(pid, model)
        asyncio.create_task(self.fleet[pid].connect_and_fly())
        self.log(f"LAUNCHED {pid}")

    async def trigger_chaos(self):
        candidates = [p for p in self.fleet.values() if p.status == "AIRBORNE"]
        if not candidates: return
        victim = random.choice(candidates)
        scenario = random.choice(SCENARIOS)
        msg = victim.apply_scenario(scenario)
        self.log(f"[bold red]CHAOS:[/bold red] {victim.id} -> {msg}")

    def kill_random(self):
        candidates = [p for p in self.fleet.values() if p.status == "AIRBORNE"]
        if candidates:
            victim = random.choice(candidates)
            asyncio.create_task(victim.crash())
            self.log(f"[bold red]KILLED[/bold red] {victim.id}")

    def land_random(self):
        candidates = [p for p in self.fleet.values() if p.status == "AIRBORNE"]
        if candidates:
            victim = random.choice(candidates)
            asyncio.create_task(victim.land())
            self.log(f"Landing {victim.id}")

    def get_table(self):
        table = Table(expand=True, border_style="blue", header_style="bold white")
        table.add_column("FLIGHT ID", style="cyan", no_wrap=True)
        table.add_column("TYPE", style="magenta")
        table.add_column("STATUS")
        table.add_column("SQUAWK", justify="center")
        table.add_column("ALTITUDE", justify="right")
        table.add_column("AUDIO", justify="right")

        if not self.fleet:
            table.add_row("---", "---", "[dim]NO ACTIVE TRAFFIC[/dim]", "---", "---", "---")
        else:
            for pid, p in self.fleet.items():
                # Dynamic Styling
                status_txt = p.status
                s_style = "green"

                if p.status == "CRASHED":
                    s_style = "bold red reverse blink"
                elif p.squawk in ["7500", "7700"]:
                    s_style = "bold red"
                    status_txt = "EMERGENCY"
                elif p.squawk == "7600":
                    s_style = "yellow"
                    status_txt = "RADIO FAIL"

                table.add_row(
                    pid,
                    p.model_name,
                    f"[{s_style}]{status_txt}[/{s_style}]",
                    f"[{'red' if p.squawk != '1200' else 'white'}]{p.squawk}[/]",
                    f"{int(p.altitude)} ft",
                    f"{p.get_audio()} dB"
                )
        return table


# --- MAIN LOOP ---
manager = FleetManager()
listener = KeyListener()


async def main_loop():
    # Header
    header = Panel(
        Align.center("[bold green]TACTICAL FLIGHT SIMULATOR[/bold green]\n[dim]Connected to SOC Localhost[/dim]"),
        border_style="green"
    )

    with Live(refresh_per_second=10, screen=True) as live:
        while True:
            # 1. INPUT HANDLING (Non-blocking)
            key = listener.get_key()
            if key:
                if key == ' ':
                    manager.add_plane(random.choice(["B737", "B737", "F16", "C172"]))
                elif key == 'c':
                    manager.chaos_mode = not manager.chaos_mode
                    manager.log(f"CHAOS MODE: {'[green]ON[/green]' if manager.chaos_mode else '[red]OFF[/red]'}")
                elif key == 'k':
                    manager.kill_random()
                elif key == 'l':
                    manager.land_random()
                elif key == 'q' or key == '\x1b':  # q or ESC
                    break

            # 2. CHAOS LOGIC
            if manager.chaos_mode and random.random() > 0.98:  # 2% chance per frame
                await manager.trigger_chaos()

            # 3. UI RENDER
            controls = "[white]SPACE[/white]: Launch | [white]K[/white]: Kill | [white]L[/white]: Land | [white]C[/white]: Chaos | [white]Q[/white]: Quit"

            layout = Layout()
            layout.split_column(
                Layout(header, size=4),
                Layout(manager.get_table(), name="main"),
                Layout(Panel("\n".join(manager.log_messages), title="LOGS", border_style="white", height=8)),
                Layout(Align.center(controls), size=2)
            )

            live.update(layout)
            await asyncio.sleep(0.1)


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        pass
    finally:
        listener.cleanup()
        print("Simulator Shutdown.")
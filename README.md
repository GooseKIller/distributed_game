
- `tkinter` draws placeholder shapes.
- UDP sockets synchronize players, host placement, and world state.
- No sprites are included yet

## Run

Start the host/builder:

```powershell
python run_game.py --host --port 5005
```

Start a runner in another terminal:

```powershell
python run_game.py --join 127.0.0.1:5005 --port 5006
```

For extra local players, use different ports:

```powershell
python run_game.py --join 127.0.0.1:5005 --port 5007
```

## Controls

Host/builder:

- Move the mouse to aim the builder cursor.
- Left click places a platform. Each platform type can be placed 3 times total.
- Right click removes the nearest placed platform.
- `1`, `2`, `3` select flat, jump pad, or stairs.
- `Q` and `E` cycle platform type.
- `A`/`D` or left/right arrows pan the camera.
- Mouse wheel also pans the camera.

Runner:

- `A`/`D` or left/right arrows move.
- `W`, up arrow, or space jumps.
- `R` respawns.

## Current Gameplay

Runners spawn on the left and try to reach the fish on the right. The map already has placeholder spikes and enemies. Enemies patrol left and right. The host cooperates by placing flat platforms, jump pads, and stairs so runners can cross hazards. Placement counts are lifetime counts: deleting a platform does not refund that platform type.

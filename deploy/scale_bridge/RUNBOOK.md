# Scale bridge: installing it on a station PC

The bridge reads the weighing indicator on a serial or USB-serial port and
posts each settled weight to the ERP (`/api/manufacturing/scale-readings/`).
The station then weighs a doff, roll or bale from the latest reading instead
of a typed figure. One bridge per scale; a PC with two scales runs two.

## What you need

- The station PC on the plant network, able to reach the ERP over HTTPS.
- Python 3.10 or later and pyserial (`apt install python3-serial`, or
  `pip install pyserial` on Windows). Nothing else.
- The scale's code as the station knows it (Settings, the station's scale
  code, e.g. `SC-1`), and the indicator's port settings (baud rate; most are
  9600, 8 data bits, no parity, 1 stop bit).

## 1. A user for the bridge

In the ERP, as an administrator: add a user named after the scale
(`bridge-sc1`) with a long random password and one permission only,
*manufacturing | scale reading | Can add scale reading*. No group, no other
right: it can post readings and do nothing else. Write the password into a
file on the station PC readable only by the bridge's account
(`/etc/scale-bridge/SC-1.password`, mode 600). Never put it in the config.

## 2. Install

Linux:

    sudo useradd --system --groups dialout scalebridge
    sudo mkdir -p /opt/scale-bridge /etc/scale-bridge
    sudo cp scale_bridge.py /opt/scale-bridge/
    sudo cp example.json /etc/scale-bridge/SC-1.json      # then edit it
    sudo cp scale-bridge@.service /etc/systemd/system/
    sudo chown scalebridge /etc/scale-bridge/SC-1.password && sudo chmod 600 /etc/scale-bridge/SC-1.password
    sudo systemctl enable --now scale-bridge@SC-1
    journalctl -u scale-bridge@SC-1 -f                    # "Posted 106.8 kg from SC-1."

Windows: copy the folder to `C:\scale-bridge`, set `"port": "COM3"` (Device
Manager, Ports), and run it at start-up as a service with NSSM:
`nssm install ScaleBridgeSC1 python C:\scale-bridge\scale_bridge.py C:\scale-bridge\SC-1.json`.

## 3. The config

| Key | Meaning |
|---|---|
| `server` | The ERP's address, `https://...` |
| `scale` | The scale code the station names |
| `username`, `password_file` | The bridge's user, and the file holding its password |
| `port`, `baud` | `/dev/ttyUSB0` or `COM3`; the indicator's baud rate |
| `pattern` | `status_gross` for frames like `ST,GS,+00123.4kg`; `plain_weight` for a bare `  123.45 kg` or `=0123.4`; or your own (below) |
| `repeat` | How many frames running must read the same weight before it counts as settled (3 at 5 frames a second is 0.6 s) |
| `retry_seconds` | The pause before opening a lost port again, or posting again |

Your own indicator: watch what it sends (`python3 -m serial.tools.miniterm
/dev/ttyUSB0 9600`) and write a regular expression with a group named
`weight`, and if it says stable or moving, a group named `status` and the
words meaning stable:

    "pattern": {"regex": "^W:(?P<weight>\\d+\\.\\d) (?P<status>S|M)$", "stable_status": ["S"]}

Optional groups: `sign` (`+`/`-`) and `unit` (`kg`/`g`; grammes are sent as
kilogrammes). Save a minute of its frames to a file and add a test beside
`apps/manufacturing/tests_scale_bridge.py` before relying on it.

## What it sends, and what it never does

- Only a settled weight: stable where the indicator says, the same weight
  `repeat` frames running. A moving frame, an overload, or a line it cannot
  read is never sent.
- Each weight once. The same roll left on the scale, or leaned on and
  settled again, is one reading. Between two rolls the empty scale settles
  at zero, and that zero is sent: so the next roll is new even at the same
  weight. Take a roll off before putting the next on.
- A lost port (cable pulled, indicator off) is opened again every
  `retry_seconds`, for ever. Nothing read before the gap counts.
- A post the ERP did not accept is tried again every `retry_seconds` while
  the scale still reads that weight, and dropped once it reads anything
  else: a late weight would be taken for whatever is on the scale then.

## Checking it

On the station screen the weight field shows the bridged reading and its
age; a reading over a minute old is refused and the operator is told so.
If the screen keeps saying there is no reading: the log says whether the
port opened ("Scale port lost ...") and whether posts were refused
("Posting ... failed (HTTP Error 403...)": the user lacks the permission;
401: the password file is wrong).

## Gross, net, and a reading posted once

- A GS (gross) frame is posted as `gross_kg`; an NT (net) frame, the indicator tared on the core, as
  `net_kg`. The station takes either, and does not take the core off a net weight again. The reading
  says which it was.
- Each settled weight carries a key: the scale, this run of the bridge, the weight's number in it, and
  the weight. Posted again because the answer was lost, the server answers with the first reading and
  keeps no second one.
- `server` must be `https://`. Only `http://localhost` (the station PC itself) is taken without TLS:
  the password goes with every post.

"""Windows->WSL MQTT relay -- bypass WSL NAT inbound block (for rt live demo).

Background: mosquitto service binds to 127.0.0.1 only, and Windows Firewall blocks
WSL->host inbound (measured). So the WSL rt demo cannot subscribe directly.
Windows->WSL outbound is allowed, so we reverse the direction: this script subscribes to
mosquitto (127.0.0.1:1883) on csi/# and republishes to the WSL-side broker (amqtt).

Usage (Windows, Espressif python -- same env as bridge.py):
    python tools\\mqtt_pump_wsl.py --wsl-host <WSL eth0 IP>

WSL broker is /tmp/mqtt-broker-venv/run_broker.py (temporary -- destroyed when WSL restarts).
Permanent solution: .wslconfig networkingMode=mirrored (assumed in rt.yaml comments) or
add mosquitto conf listener + firewall rule (admin).
"""
import argparse
import sys

import paho.mqtt.client as mqtt


def make_client():
    try:                                         # paho 2.x
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except (AttributeError, TypeError):          # paho 1.x
        return mqtt.Client()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wsl-host", required=True, help="WSL eth0 IP (check with `ip addr show eth0` in WSL)")
    ap.add_argument("--src-host", default="127.0.0.1", help="mosquitto host")
    ap.add_argument("--topic", default="csi/#")
    args = ap.parse_args()

    sub, pub = make_client(), make_client()
    relayed = [0]

    def on_connect(client, userdata, flags, rc, *extra):
        print(f"[pump] sub connected rc={rc} -> subscribe {args.topic}", flush=True)
        client.subscribe(args.topic, qos=0)

    def on_message(client, userdata, msg):
        pub.publish(msg.topic, msg.payload, qos=0)
        relayed[0] += 1
        if relayed[0] % 3000 == 0:
            print(f"[pump] relayed {relayed[0]}", flush=True)

    sub.on_connect = on_connect
    sub.on_message = on_message

    try:
        pub.connect(args.wsl_host, 1883, keepalive=30)
    except OSError as e:
        sys.exit(f"[pump] WSL broker connection failed {args.wsl_host}:1883 ({e}) -- "
                 "check if run_broker.py is running in WSL")
    pub.loop_start()

    try:
        sub.connect(args.src_host, 1883, keepalive=30)
    except OSError as e:
        sys.exit(f"[pump] mosquitto connection failed {args.src_host}:1883 ({e})")
    print("[pump] start", flush=True)
    sub.loop_forever()


if __name__ == "__main__":
    main()

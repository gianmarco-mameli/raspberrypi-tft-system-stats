#!/usr/bin/env python3

import os
import random
import signal
import socket
import sys
import threading
import paho.mqtt.client as mqtt
import psutil
from hurry.filesize import size
from influxdb_client import InfluxDBClient
from luma.core.interface.serial import spi
from luma.core.sprite_system import framerate_regulator
from luma.lcd.device import st7789
from PIL import Image, ImageDraw, ImageFont

# environment variables
mqtt_server = os.getenv("MQTT_SERVER")
motion_topic = os.getenv("MOTION_TOPIC")
lcd_rotation = int(os.getenv("LCD_ROTATION", 1))
influxdb_url = os.getenv("INFLUXDB_URL")
influxdb_org = os.getenv("INFLUXDB_ORG")
influxdb_bucket = os.getenv("INFLUXDB_BUCKET")
influxdb_token = os.getenv("INFLUXDB_TOKEN")

serial = spi(
    spi_mode=3,
    port=0,
    device=0,
    gpio_LIGHT=27,
    gpio_DC=17,
    gpio_RST=22,
    bus_speed_hz=8000000,
)
lcd = st7789(serial, rotate=lcd_rotation)
width = lcd.width
height = lcd.height

default_color = "#FFFFFF"
alternate_color = "#FFFF00"
outline_color = "#000000"
fill_color = "#000000"
logo = Image.open("raspberrypi.png").convert("RGB")
img = Image.new("RGB", (width, height), color=(0, 0, 0))
draw = ImageDraw.Draw(img)
imgss = Image.new("RGB", (width, height), color=(0, 0, 0))
drawss = ImageDraw.Draw(imgss)

# boot splashscreen
font = ImageFont.load_default(21)
font_big = ImageFont.load_default(29)
boot_text_1 = "Raspberry Pi Stats"
boot_text_2 = "Starting..."
print(boot_text_1)
print(boot_text_2)
w = draw.textlength(text=boot_text_1, font=font_big)
draw.text(
    (
        int((width - w) / 2), 2
    ),
    text=boot_text_1,
    font=font_big,
    fill=default_color
)
w = draw.textlength(text=boot_text_2, font=font_big)
draw.text(
    (int((width - w) / 2), 30),
    text=boot_text_2,
    font=font_big,
    fill=default_color
)
w = int(logo.width)
img.paste(logo, (int((width - w) / 2), 90))
lcd.display(img)

padding = -3
top = padding
bottom = height - padding
global x
x = 3
data = {}
show_stats = True

influxdbc = None
if influxdb_url and influxdb_token and influxdb_org and influxdb_bucket:
    influxdbc = InfluxDBClient(
        url=influxdb_url,
        token=influxdb_token,
        org=influxdb_org
    )
    query_api = influxdbc.query_api()
    try:
        health = influxdbc.health()
        print(
            f"InfluxDB2 connection status: {health.status} - "
            f"{health.message}"
        )
    except Exception as e:
        print(f"InfluxDB2 connection error: {e}")


class screensaver:
    __slots__ = (
        "_w", "_h", "_image", "_imgWidth", "_imgHeight",
        "_x_speed", "_y_speed", "_x_pos", "_y_pos"
    )

    def __init__(self, w, h, image):
        self._w = w
        self._h = h
        self._image = image
        self._imgWidth = image.width
        self._imgHeight = image.height
        self._x_speed = 1
        self._y_speed = 1
        # trunk-ignore(bandit/B311)
        self._x_pos = random.random() * (self._w - self._imgWidth)
        # trunk-ignore(bandit/B311)
        self._y_pos = random.random() * (self._h - self._imgHeight)

    def update_pos(self):
        x_next = self._x_pos + self._x_speed
        y_next = self._y_pos + self._y_speed

        if x_next + self._imgWidth > self._w or x_next < 0:
            self._x_speed = -self._x_speed
            x_next = self._x_pos + self._x_speed
        if y_next + self._imgHeight > self._h or y_next < 0:
            self._y_speed = -self._y_speed
            y_next = self._y_pos + self._y_speed

        self._x_pos = x_next
        self._y_pos = y_next

    def draw(self):
        imgss.paste(self._image, (int(self._x_pos), int(self._y_pos)))


class SignalHandler:
    shutdown_requested = False

    def __init__(self):
        signal.signal(signal.SIGINT, self.request_shutdown)
        signal.signal(signal.SIGTERM, self.request_shutdown)

    def request_shutdown(self, signum, frame):
        if not self.shutdown_requested:
            print("Request to shutdown received, stopping")
            self.shutdown_requested = True
            try:
                lcd.clear()
            except Exception as e:
                print(f"LCD clear error: {e}")
            try:
                lcd.cleanup()
            except Exception as e:
                print(f"LCD cleanup error: {e}")
            sys.exit(0)

    def can_run(self):
        return not self.shutdown_requested


def on_connect(mqttc, userdata, flags, reason_code, properties):
    print(f"MQTT Connected with result code '{reason_code}'")
    mqttc.subscribe(motion_topic)


def on_message(
    mqttc,
    userdata,
    msg,
):
    global show_stats
    payload = msg.payload.decode("utf-8").strip()
    if payload == "1":
        show_stats = True
    elif payload == "0":
        show_stats = False
    mqttc.publish(f"{hostname}/stats_display", payload)


def set_color(value, warn, crit):
    if warn is not None and crit is not None:
        if value >= crit:
            return "#FF0000"  # Critical
        if value >= warn:
            return "#FFFF00"  # Warning
        return "#00FF00"      # Normal
    return default_color


def get_value(metric_name, metric_value, metric_path):
    if influxdbc:
        flux_query = f"""from(bucket: "{influxdb_bucket}")
            |> range(start: -1h)
            |> filter(fn: (r) => r["_measurement"] == "{metric_name}")
            |> filter(fn: (r) => r["_field"] == "{metric_value}")
            |> filter(fn: (r) => r["hostname"] =~ /{hostname}/)
            |> filter(fn: (r) => r["metric"] == "{metric_path}")
            |> yield(name: "last")"""
        try:
            tables = query_api.query(flux_query)
            for table in tables:
                for record in table.records:
                    return record.get_value()
        except Exception as e:
            print(f"InfluxDB query error: {e}")
            return None
    return None


def get_info(service, path):
    service_name = service.split(".")[0]
    info = {
        service_name + "_crit": get_value(service, "crit", path),
        service_name + "_warn": get_value(service, "warn", path),
        service_name + "_max": get_value(service, "max", path),
    }
    return info


def get_iface_speed(iface):
    try:
        # trunk-ignore(bandit/B404)
        import subprocess

        # trunk-ignore(bandit/B603)
        # trunk-ignore(bandit/B607)
        result = subprocess.run(
            ["ethtool", iface],
            capture_output=True,
            text=True
        )
        for line in result.stdout.splitlines():
            if "Speed:" in line:
                speed_str = line.split("Speed:")[1].strip()
                if speed_str.endswith("Mb/s"):
                    return int(speed_str.replace("Mb/s", "").strip())
        return 0
    except Exception:
        return 0


def get_data():
    if influxdb_url:
        print("Using InfluxDB2 data")

    global hostname, ip, net_speed
    global cpu_freq_max, load_info
    global temp_info, disk_info
    global mem_info, procs_info

    # Use cached values if already set to avoid unnecessary lookups
    if not hasattr(get_data, "initialized"):
        hostname = socket.gethostname()
        # hostname = ""
        get_data.initialized = True

    net_if_stats = psutil.net_if_stats()
    net_if_addrs = psutil.net_if_addrs()
    net_speed = 0
    ip = ""

    # Prefer eth0, fallback to wlan0
    for iface in ("eth0", "wlan0"):
        stats = net_if_stats.get(iface)
        addrs = net_if_addrs.get(iface)
        if stats and stats.isup and addrs:
            net_speed = get_iface_speed(iface)
            ip = next(
                (
                    addr.address
                    for addr in addrs
                    if addr.family == socket.AF_INET
                ),
                ""
            )
            break

    cpu_freq = psutil.cpu_freq()
    cpu_freq_max = cpu_freq.max if cpu_freq else 0

    # Use a helper to reduce repetition
    def safe_get_info(service, path):
        try:
            return get_info(service, path)
        except Exception:
            return {}

    load_info = safe_get_info("load", "load1")
    temp_info = safe_get_info("check_rpi", "cputemp")
    disk_info = safe_get_info("disk", "/")
    mem_info = safe_get_info("mem", "USED")
    procs_info = safe_get_info("procs", "procs")

    print("New data fetched")


def update_data():
    # Use static variables to avoid global pollution and repeated lookups
    if not hasattr(update_data, "old_net_value"):
        update_data.old_net_value = 0

    cpu_used = round(psutil.getloadavg()[1], 1)
    cpu_clock = psutil.cpu_freq().current
    # Get CPU temperature safely
    temps = psutil.sensors_temperatures()
    cpu_temp = None
    if temps:
        for entries in temps.values():
            if entries:
                cpu_temp = round(entries[0].current, 1)
                break
    if cpu_temp is None:
        cpu_temp = 0.0

    mem = psutil.virtual_memory()
    mem_used = mem.total - mem.available
    df_usage = psutil.disk_usage("/")
    df = df_usage.used
    df_total = df_usage.total

    net_counters = psutil.net_io_counters()
    new_net_value = net_counters.bytes_sent + net_counters.bytes_recv
    net_data = new_net_value - update_data.old_net_value
    update_data.old_net_value = new_net_value

    procs = sum(1 for _ in psutil.process_iter())

    global data
    data = {
        "cpu_used": cpu_used,
        "cpu_clock": cpu_clock,
        "cpu_temp": cpu_temp,
        "mem_total": mem.total,
        "mem_used": mem_used,
        "df": df,
        "df_total": df_total,
        "net_data": net_data,
        "procs": procs,
    }


class RepeatingTimer(threading.Thread):
    def __init__(self, interval_seconds, callback, daemon):
        super().__init__()
        self.stop_event = threading.Event()
        self.interval_seconds = interval_seconds
        self.callback = callback
        self.daemon = daemon

    def run(self):
        while not self.stop_event.wait(self.interval_seconds):
            self.callback()

    def stop(self):
        self.stop_event.set()


def main(num_iterations=sys.maxsize):

    get_data()
    thread_get = RepeatingTimer(3600, get_data, True)
    thread_get.start()

    update_data()
    thread_update = RepeatingTimer(2, update_data, True)
    thread_update.start()

    regulator = framerate_regulator(30)

    if mqtt_server is not None:
        mqttc = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            socket.gethostname() + "_stats_display"
        )
        mqttc.on_connect = on_connect
        mqttc.on_message = on_message
        mqttc.connect(mqtt_server)
        mqttc.loop_start()

    ss = [screensaver(width, height, i) for i in [logo]]

    lcd.clear()

    while signal_handler.can_run():
        while show_stats and signal_handler.can_run():
            draw.rectangle(
                (0, 0, width, height),
                outline=outline_color,
                fill=fill_color
            )

            y = top

            Hostname = str(hostname)
            state_color = default_color
            draw.text(
                (x, y),
                Hostname.upper(),
                font=font_big,
                fill=alternate_color
            )
            y += font.getbbox(Hostname)[3]
            y += 6

            Ip = str(ip)
            state_color = default_color
            draw.text((x, y), Ip, font=font, fill=state_color)
            y += font.getbbox(Ip)[3]

            y += 11
            draw.line([(0, y), (width, y)], default_color, 2)
            y += 7

            Load = "Cpu Load: " + str(data["cpu_used"])
            state_color = set_color(
                data["cpu_used"],
                load_info["load_warn"],
                load_info["load_crit"]
            )
            draw.text((x, y), Load, font=font, fill=state_color)
            y += font.getbbox(Load)[3]

            Clock = "Cpu Clock: " + str(data["cpu_clock"])
            state_color = set_color(
                data["cpu_clock"], (cpu_freq_max * 0.80), cpu_freq_max
            )
            draw.text((x, y), Clock, font=font, fill=state_color)
            y += font.getbbox(Clock)[3]

            Temp = "Temp: " + str(data["cpu_temp"]) + "°C"
            state_color = set_color(
                data["cpu_temp"],
                temp_info["check_rpi_warn"],
                temp_info["check_rpi_crit"],
            )
            draw.text((x, y), Temp, font=font, fill=state_color)
            y += font.getbbox(Temp)[3]

            Mem = (
                "Mem: "
                + str(size(data["mem_used"]))
                + "/"
                + str(size(data["mem_total"]))
            )
            state_color = set_color(
                data["mem_used"], mem_info["mem_warn"], mem_info["mem_crit"]
            )
            draw.text((x, y), Mem, font=font, fill=state_color)
            y += font.getbbox(Mem)[3]

            Disk = (
                "Disk: "
                + str(size(data["df"]))
                + "/"
                + str(size(data["df_total"]))
            )
            state_color = set_color(
                data["df"], disk_info["disk_warn"], disk_info["disk_crit"]
            )
            draw.text((x, y), Disk, font=font, fill=state_color)
            y += font.getbbox(Disk)[3]

            Net = (
                "Net: "
                + str(size(data["net_data"]))
                + "/"
                + str(net_speed)
                + "M"
            )
            state_color = set_color(
                data["net_data"], (net_speed * 0.70), net_speed
            )
            draw.text((x, y), Net, font=font, fill=state_color)
            y += font.getbbox(Net)[3]

            Procs = "Procs: " + str(data["procs"])
            state_color = set_color(
                data["procs"],
                procs_info["procs_warn"],
                procs_info["procs_crit"]
            )
            draw.text((x, y), Procs, font=font, fill=state_color)
            y += font.getbbox(Procs)[3]

            lcd.display(img)
        while (
            not show_stats
            and num_iterations > 0
            and signal_handler.can_run()
        ):
            with regulator:
                num_iterations -= 1
                drawss.rectangle(
                    (0, 0, width, height),
                    outline=outline_color,
                    fill=fill_color
                )
                for s in ss:
                    s.update_pos()
                    s.draw()
                lcd.display(imgss)
    if signal_handler.shutdown_requested:
        sys.exit(0)


if __name__ == "__main__":
    signal_handler = SignalHandler()
    try:
        main()
    except KeyboardInterrupt:
        signal_handler.request_shutdown(signal.SIGINT, None)
